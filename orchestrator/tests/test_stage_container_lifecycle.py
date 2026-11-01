"""``PackContainer``: a throwaway container is always disposed, and copies change real state.

The Docker CLI is replaced by a small executable that keeps "containers" as directories under a
scratch home, so what the tests observe is whether a container still exists, what files moved
and which errors surfaced. No Docker daemon, image or network is involved.
"""

from __future__ import annotations

import asyncio
import shutil
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from raidar.subject.workflow.stage_container import ContainerError, PackContainer

pytestmark = pytest.mark.slow

FAKE_DOCKER = textwrap.dedent(
    """\
    #!{python}
    import os, shutil, sys
    from pathlib import Path

    home = Path(os.environ["FAKE_DOCKER_HOME"])
    args = sys.argv[1:]
    assert args[0] == "--context", args
    args = args[2:]
    command, rest = args[0], args[1:]


    def fail(text, code=1):
        sys.stderr.write(text + "\\n")
        sys.exit(code)


    def root(name):
        folder = home / "containers" / name
        if not folder.is_dir():
            fail(f"Error: No such container: {{name}}")
        return folder / "fs"


    def resolve(arg):
        if arg.endswith("/."):
            arg = arg[:-2]
        if arg.startswith("raidar-stage-check-"):
            name, path = arg.split(":", 1)
            return root(name) / path.lstrip("/"), True
        return Path(arg), False


    if command == "run":
        if os.environ.get("FAKE_FAIL_RUN"):
            fail("docker: Error response from daemon: pull access denied", 125)
        name = rest[rest.index("--name") + 1]
        (home / "containers" / name / "fs").mkdir(parents=True)
    elif command == "rm":
        if os.environ.get("FAKE_FAIL_RM"):
            fail("Error: cannot remove container", 1)
        shutil.rmtree(home / "containers" / rest[-1], ignore_errors=True)
    elif command == "exec":
        name_at = next(i for i, item in enumerate(rest) if item.startswith("raidar-stage-check-"))
        options, name, script = rest[:name_at], rest[name_at], rest[name_at + 3]
        env, workdir = dict(os.environ), "/"
        for flag, value in zip(options[::2], options[1::2]):
            if flag == "--env":
                key, _, val = value.partition("=")
                env[key] = val
            elif flag == "--workdir":
                workdir = value
        fs = root(name)
        env["FAKE_FS"] = str(fs)
        os.chdir(fs / workdir.lstrip("/") if (fs / workdir.lstrip("/")).is_dir() else fs)
        os.execvpe("bash", ["bash", "-c", script], env)
    elif command == "cp":
        (src, src_in), (dst, dst_in) = resolve(rest[0]), resolve(rest[1])
        if rest[0].endswith("/."):
            if not src.is_dir():
                fail(f"Could not find the file {{src}} in container")
            shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=True)
        elif not src.exists():
            fail(f"Could not find the file {{src}} in container")
        elif src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=True)
        else:
            if dst.is_dir():
                dst = dst / src.name
            if not dst.parent.is_dir():
                fail(f"directory {{dst.parent}} does not exist")
            shutil.copyfile(src, dst)
    else:
        fail(f"unsupported fake docker command {{command}}")
    """
)


@pytest.fixture
def docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The fake docker's executable; its "containers" live in ``<tmp>/docker-home``."""
    home = tmp_path / "docker-home"
    (home / "containers").mkdir(parents=True)
    monkeypatch.setenv("FAKE_DOCKER_HOME", str(home))
    for flag in ("FAKE_FAIL_RUN", "FAKE_FAIL_RM"):
        monkeypatch.delenv(flag, raising=False)
    binary = tmp_path / "docker"
    binary.write_text(FAKE_DOCKER.format(python=sys.executable))
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    return binary


def _container(docker: Path) -> PackContainer:
    return PackContainer("pack-image", "ctx", docker_bin=str(docker))


def _live(tmp_path: Path) -> list[str]:
    return sorted(path.name for path in (tmp_path / "docker-home" / "containers").iterdir())


def _fs(tmp_path: Path, container: PackContainer) -> Path:
    return tmp_path / "docker-home" / "containers" / container.name / "fs"


# ---- lifecycle -------------------------------------------------------------------------------


def test_the_container_exists_only_inside_the_block(docker: Path, tmp_path: Path) -> None:
    async def scenario() -> tuple[list[str], str]:
        container = _container(docker)
        async with container:
            inside = _live(tmp_path)
        return inside, container.name

    inside, name = asyncio.run(scenario())

    assert inside == [name]
    assert _live(tmp_path) == []


def test_a_failing_body_still_removes_the_container_and_its_error_surfaces(
    docker: Path, tmp_path: Path
) -> None:
    async def scenario() -> None:
        async with _container(docker):
            raise ValueError("body broke")

    with pytest.raises(ValueError, match="body broke"):
        asyncio.run(scenario())

    assert _live(tmp_path) == []


def test_cancellation_in_the_body_still_removes_the_container(docker: Path, tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _container(docker):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())

    assert _live(tmp_path) == []


def test_a_container_that_cannot_start_raises_with_docker_stderr_and_leaves_nothing(
    docker: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_FAIL_RUN", "1")
    entered: list[bool] = []

    async def scenario() -> None:
        async with _container(docker):
            entered.append(True)

    with pytest.raises(ContainerError, match=r"docker run failed \(125\).*pull access denied"):
        asyncio.run(scenario())

    assert entered == []
    assert _live(tmp_path) == []


def test_a_removal_that_fails_neither_masks_the_body_error_nor_fails_a_clean_block(
    docker: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_FAIL_RM", "1")

    async def clean() -> str:
        async with _container(docker) as container:
            return container.name

    async def broken() -> None:
        async with _container(docker):
            raise ValueError("body broke")

    name = asyncio.run(clean())
    with pytest.raises(ValueError, match="body broke"):
        asyncio.run(broken())

    # the leaked containers are the removal's failure only; nothing was raised for them
    assert name in _live(tmp_path)
    assert len(_live(tmp_path)) == 2


def test_each_container_gets_its_own_name_so_disposal_never_removes_a_sibling(
    docker: Path, tmp_path: Path
) -> None:
    async def scenario() -> list[str]:
        async with _container(docker) as outer:
            async with _container(docker) as inner:
                assert inner.name != outer.name
            return _live(tmp_path)

    survivors = asyncio.run(scenario())

    assert len(survivors) == 1
    assert _live(tmp_path) == []


# ---- exec ------------------------------------------------------------------------------------


def _exec(docker: Path, command: str) -> tuple[str, str, int]:
    async def scenario() -> tuple[str, str, int]:
        async with _container(docker) as container:
            result = await container.exec(command)
            return result.stdout or "", result.stderr or "", result.return_code

    return asyncio.run(scenario())


def test_exec_returns_output_and_a_nonzero_exit_without_raising(docker: Path) -> None:
    out, err, code = _exec(docker, "echo seen; echo problem >&2; exit 7")

    assert (out.strip(), err.strip(), code) == ("seen", "problem", 7)


def test_exec_applies_environment_and_working_directory_inside_the_container(
    docker: Path,
) -> None:
    async def scenario() -> str:
        async with _container(docker) as container:
            await container.exec("mkdir -p work/dir")
            result = await container.exec(
                'printf "%s@%s" "$MARK" "$(basename "$PWD")"',
                cwd="/work/dir",
                env={"MARK": "value with spaces"},
            )
            return result.stdout or ""

    assert asyncio.run(scenario()) == "value with spaces@dir"


def test_exec_that_exceeds_its_timeout_is_killed_reported_and_the_container_still_disposed(
    docker: Path, tmp_path: Path
) -> None:
    async def scenario() -> tuple[str, int]:
        async with _container(docker) as container:
            result = await container.exec("exec sleep 30", timeout_sec=1)
            return result.stderr or "", result.return_code

    err, code = asyncio.run(scenario())

    assert code == 124
    assert "timed out after 1s" in err
    assert _live(tmp_path) == []


def test_exec_in_a_container_that_is_gone_reports_a_failure(docker: Path, tmp_path: Path) -> None:
    async def scenario() -> tuple[str, int]:
        async with _container(docker) as container:
            shutil.rmtree(tmp_path / "docker-home" / "containers" / container.name)
            result = await container.exec("true")
            return result.stderr or "", result.return_code

    err, code = asyncio.run(scenario())

    assert code != 0
    assert "No such container" in err


# ---- copy ------------------------------------------------------------------------------------


def test_uploaded_and_downloaded_files_change_real_state_and_missing_parents_are_made(
    docker: Path, tmp_path: Path
) -> None:
    source = tmp_path / "in.txt"
    source.write_text("payload")
    target = tmp_path / "out" / "nested" / "back.txt"

    async def scenario() -> str:
        async with _container(docker) as container:
            await container.upload_file(source, "/data.txt")
            await container.download_file("/data.txt", target)
            return (_fs(tmp_path, container) / "data.txt").read_text()

    assert asyncio.run(scenario()) == "payload"
    assert target.read_text() == "payload"


def test_a_failed_download_raises_a_container_error_and_writes_no_file(
    docker: Path, tmp_path: Path
) -> None:
    target = tmp_path / "out" / "never.txt"

    async def scenario() -> None:
        async with _container(docker) as container:
            await container.download_file("/absent.txt", target)

    with pytest.raises(ContainerError, match=r"docker cp failed \(1\).*Could not find"):
        asyncio.run(scenario())

    assert not target.exists()
    assert _live(tmp_path) == []


def test_upload_dir_copies_the_contents_not_the_directory_itself(
    docker: Path, tmp_path: Path
) -> None:
    source = tmp_path / "tree"
    (source / "sub").mkdir(parents=True)
    (source / "a.txt").write_text("a")
    (source / "sub" / "b.txt").write_text("b")

    async def scenario() -> list[str]:
        async with _container(docker) as container:
            (_fs(tmp_path, container) / "dest").mkdir()
            await container.upload_dir(source, "/dest")
            return sorted(
                p.relative_to(_fs(tmp_path, container)).as_posix()
                for p in (_fs(tmp_path, container) / "dest").rglob("*")
            )

    assert asyncio.run(scenario()) == ["dest/a.txt", "dest/sub", "dest/sub/b.txt"]


def test_a_failed_upload_raises_and_the_container_is_still_removed(
    docker: Path, tmp_path: Path
) -> None:
    async def scenario() -> None:
        async with _container(docker) as container:
            await container.upload_file(tmp_path / "does-not-exist", "/x")

    with pytest.raises(ContainerError, match="docker cp failed"):
        asyncio.run(scenario())

    assert _live(tmp_path) == []


def test_download_dir_merges_into_an_existing_target_and_creates_a_missing_one(
    docker: Path, tmp_path: Path
) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep.txt").write_text("host")
    fresh = tmp_path / "fresh" / "deep"

    async def scenario() -> None:
        async with _container(docker) as container:
            (_fs(tmp_path, container) / "out").mkdir()
            (_fs(tmp_path, container) / "out" / "new.txt").write_text("container")
            await container.download_dir("/out", existing)
            await container.download_dir("/out", fresh)

    asyncio.run(scenario())

    assert (existing / "keep.txt").read_text() == "host"
    assert (existing / "new.txt").read_text() == "container"
    assert (fresh / "new.txt").read_text() == "container"


def _seed_tree(fs: Path, outside: Path) -> None:
    out = fs / "out"
    (out / "pkg" / ".git").mkdir(parents=True)
    (out / "pkg" / ".git" / "config").write_text("secret")
    (out / "pkg" / "src").mkdir()
    (out / "pkg" / "src" / "mod.py").write_text("kept")
    (out / "pkg" / "src" / "mod.pyc").write_bytes(b"\0")
    (out / "note.md").write_text("note")
    outside.mkdir()
    (outside / "precious.txt").write_text("outside the tree")
    (out / "link").symlink_to(outside, target_is_directory=True)
    (out / "alias.md").symlink_to("note.md")


def test_download_with_exclusions_drops_named_entries_at_any_depth_and_keeps_the_rest(
    docker: Path, tmp_path: Path
) -> None:
    target = tmp_path / "result"
    outside = tmp_path / "outside"

    async def scenario() -> None:
        async with _container(docker) as container:
            _seed_tree(_fs(tmp_path, container), outside)
            await container.download_dir_with_exclusions("/out", target, [".git", "/*.pyc", "link"])

    asyncio.run(scenario())

    kept = sorted(p.relative_to(target).as_posix() for p in target.rglob("*"))
    assert kept == ["alias.md", "note.md", "pkg", "pkg/src", "pkg/src/mod.py"]
    assert (target / "alias.md").is_symlink()
    assert (target / "alias.md").readlink() == Path("note.md")
    # an excluded symlink is removed as a link: what it pointed at is never touched
    assert (outside / "precious.txt").read_text() == "outside the tree"


def test_download_with_exclusions_into_an_existing_target_preserves_its_files(
    docker: Path, tmp_path: Path
) -> None:
    target = tmp_path / "result"
    target.mkdir()
    (target / "host.txt").write_text("host")

    async def scenario() -> None:
        async with _container(docker) as container:
            _seed_tree(_fs(tmp_path, container), tmp_path / "outside")
            await container.download_dir_with_exclusions("/out", target, [".git"])

    asyncio.run(scenario())

    assert (target / "host.txt").read_text() == "host"
    assert (target / "note.md").read_text() == "note"
    assert not (target / "pkg" / ".git").exists()


def test_a_failed_filtered_download_raises_and_creates_nothing_at_the_target(
    docker: Path, tmp_path: Path
) -> None:
    target = tmp_path / "result"

    async def scenario() -> None:
        async with _container(docker) as container:
            await container.download_dir_with_exclusions("/missing", target, [".git"])

    with pytest.raises(ContainerError, match="docker cp failed"):
        asyncio.run(scenario())

    assert not target.exists()
    assert _live(tmp_path) == []
