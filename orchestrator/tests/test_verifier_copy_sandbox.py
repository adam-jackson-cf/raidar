"""DockerCopySandbox: the grader fed and read by ``docker cp`` (argv/tar need no Docker)."""

from __future__ import annotations

import asyncio
import io
import json
import tarfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from raidar.grading import sandbox as sandbox_module
from raidar.grading.sandbox import (
    CONTAINER_LAYOUT,
    GRADER_ROLE_LABEL,
    DockerCopySandbox,
    SandboxRun,
    build_copy_in_tar,
    extract_out_tar,
)
from raidar.judge.secrets import SecretIsolationError


def _run(tmp_path: Path) -> SandboxRun:
    app, tests, out = tmp_path / "app", tmp_path / "tests", tmp_path / "out"
    for directory in (app, tests, out):
        directory.mkdir()
    (app / "main.py").write_text("x = 1\n")
    (tests / "test_a.py").write_text("def test_a(): ...\n")
    return SandboxRun(
        app_dir=app,
        tests_dir=tests,
        out_dir=out,
        argv=("pytest", "-q"),
        env={"B": "2", "A": "1"},
        timeout_s=5,
        cpus=1.5,
        memory_mb=512,
    )


def _members(run: SandboxRun, tmp_path: Path) -> dict[str, tarfile.TarInfo]:
    archive = tmp_path / "in.tar"
    build_copy_in_tar(run, CONTAINER_LAYOUT, archive)
    with tarfile.open(archive) as bundle:
        return {member.name: member for member in bundle.getmembers()}


def test_create_argv_has_the_lockdown_flags_labels_and_no_host_paths(tmp_path: Path) -> None:
    run = _run(tmp_path)
    sandbox = DockerCopySandbox(image="grader:1", docker_context="ctx", run_scope="abc123")
    created = sandbox.create_argv(run, "raidar-oracle-x")

    assert created[:4] == ["docker", "--context", "ctx", "create"]
    assert "--rm" not in created
    assert "-v" not in created
    assert "--volume" not in created
    assert str(tmp_path) not in " ".join(created)
    labels = [created[i + 1] for i, arg in enumerate(created) if arg == "--label"]
    assert GRADER_ROLE_LABEL in labels
    assert "raidar.run_scope=abc123" in labels
    assert any(label.startswith("raidar.host=") for label in labels)
    for flag, value in (
        ("--network", "none"),
        ("--user", "1000:1000"),
        ("--cap-drop", "ALL"),
        ("--security-opt", "no-new-privileges"),
        ("--memory", "512m"),
        ("--cpus", "1.5"),
        ("--pids-limit", "1024"),
        ("-w", "/app"),
    ):
        assert created[created.index(flag) + 1] == value
    assert created[-3:] == ["grader:1", "pytest", "-q"]
    assert created.index("A=1") < created.index("B=2")  # env sorted, so the argv is stable


def test_copy_in_tar_sets_ownership_and_modes(tmp_path: Path) -> None:
    members = _members(_run(tmp_path), tmp_path)

    app = members["app/main.py"]
    assert (app.uid, app.gid) == (1000, 1000)
    assert app.mode & 0o200
    tests = members["tests/test_a.py"]
    assert (tests.uid, tests.gid) == (0, 0)
    assert not tests.mode & 0o222
    assert tests.mode & 0o444 == 0o444
    runtime = members["oracle/runtime/raidar_oracle_plugin.py"]
    assert (runtime.uid, runtime.gid) == (0, 0)
    assert not runtime.mode & 0o222
    out = members["oracle/out"]
    assert out.isdir()
    assert (out.uid, out.gid) == (1000, 1000)
    assert not any(name.startswith("oracle/out/") for name in members)


def test_copy_in_tar_keeps_case_sensitive_names_and_symlinks(tmp_path: Path) -> None:
    run = _run(tmp_path)
    (run.app_dir / "Pkg").mkdir()
    (run.app_dir / "Pkg" / "MixedCase.py").write_text("a")
    (run.app_dir / "link").symlink_to("main.py")
    (run.app_dir / "escape").symlink_to("/etc/passwd")
    members = _members(run, tmp_path)

    assert "app/Pkg/MixedCase.py" in members
    assert (members["app/link"].issym(), members["app/link"].linkname) == (True, "main.py")
    assert (members["app/escape"].issym(), members["app/escape"].linkname) == (
        True,
        "/etc/passwd",
    )
    assert (members["app/link"].uid, members["app/link"].gid) == (1000, 1000)


def test_extract_out_tar_strips_the_directory_prefix(tmp_path: Path) -> None:
    archive = tmp_path / "out.tar"
    with tarfile.open(archive, "w") as bundle:
        top = tarfile.TarInfo("out")
        top.type = tarfile.DIRTYPE
        bundle.addfile(top)
        data = b"{}"
        info = tarfile.TarInfo("out/ledger.json")
        info.size = len(data)
        bundle.addfile(info, io.BytesIO(data))
    dest = tmp_path / "dest"
    extract_out_tar(archive, dest)
    assert (dest / "ledger.json").read_text() == "{}"


def test_extract_out_tar_refuses_a_link_that_leaves_the_directory(tmp_path: Path) -> None:
    archive = tmp_path / "out.tar"
    with tarfile.open(archive, "w") as bundle:
        link = tarfile.TarInfo("out/escape")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        bundle.addfile(link)
    with pytest.raises(tarfile.TarError):
        extract_out_tar(archive, tmp_path / "dest")


# ------------------------------------------------ run, driven through an injected spawner
def _out_tar(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as bundle:
        top = tarfile.TarInfo("out")
        top.type = tarfile.DIRTYPE
        bundle.addfile(top)
        for name, data in files.items():
            info = tarfile.TarInfo(f"out/{name}")
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@dataclass
class _Reply:
    code: int = 0
    out: bytes = b""
    err: bytes = b""
    hang_first: bool = False  # communicate() times out once, as a wait_for expiry would
    raises: BaseException | None = None


class _Proc:
    def __init__(self, reply: _Reply, stdout: Any) -> None:
        self.reply = reply
        self.sink = stdout if hasattr(stdout, "write") else None
        self.returncode: int | None = None
        self.killed = False
        self.calls = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def communicate(self) -> tuple[bytes | None, bytes]:
        self.calls += 1
        if self.reply.hang_first and self.calls == 1:
            raise TimeoutError
        if self.returncode is None:
            self.returncode = self.reply.code
        if self.sink is not None:
            self.sink.write(self.reply.out)
            return None, self.reply.err
        return self.reply.out, self.reply.err


@dataclass
class _FakeDocker:
    replies: dict[str, _Reply]
    argvs: list[list[str]] = field(default_factory=list)
    procs: list[_Proc] = field(default_factory=list)
    copied_in: bytes = b""

    def verb(self, argv: tuple[str, ...]) -> str:
        verb = argv[3]
        if verb == "cp":
            return "cp-in" if argv[4] == "-" else "cp-out"
        return verb

    async def __call__(self, *argv: str, stdin: Any = None, stdout: Any = None, **_: Any) -> _Proc:
        self.argvs.append(list(argv))
        verb = self.verb(argv)
        if hasattr(stdin, "read"):
            self.copied_in = stdin.read()
        reply = self.replies.get(verb, _Reply())
        if reply.raises is not None:
            raise reply.raises
        proc = _Proc(reply, stdout)
        self.procs.append(proc)
        return proc

    @property
    def verbs(self) -> list[str]:
        return [self.verb(tuple(argv)) for argv in self.argvs]


def _docker(**overrides: _Reply) -> _FakeDocker:
    replies = {
        "inspect": _Reply(out=b"PATH\nHOME\n"),
        "start": _Reply(code=3, out=b"graded"),
        "cp-out": _Reply(out=_out_tar({"ledger.json": b"{}"})),
        **overrides,
    }
    return _FakeDocker(replies)


_SPAWNER: list[_FakeDocker] = []


@pytest.fixture(autouse=True)
def docker_spawner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every docker process the sandbox starts is answered by the registered ``_FakeDocker``."""
    _SPAWNER.clear()

    async def spawn(*argv: str, **options: Any) -> _Proc:
        return await _SPAWNER[0](*argv, **options)

    monkeypatch.setattr(sandbox_module.asyncio, "create_subprocess_exec", spawn)


def _sandbox(docker: _FakeDocker) -> DockerCopySandbox:
    _SPAWNER[:] = [docker]
    return DockerCopySandbox(image="grader:1", docker_context="ctx")


async def test_a_graded_run_reports_exit_code_output_ledger_and_isolation_evidence(
    tmp_path: Path,
) -> None:
    docker = _docker()
    run = _run(tmp_path)

    result = await _sandbox(docker).run(run)

    assert docker.verbs == ["create", "inspect", "cp-in", "start", "cp-out", "rm"]
    assert (result.exit_code, result.timed_out) == (3, False)
    assert result.output.startswith("graded")
    assert "[raidar-copy] copy_in_s=" in result.output
    assert "run_s=" in result.output
    assert "copy_out_s=" in result.output
    name = docker.argvs[0][docker.argvs[0].index("--name") + 1]
    assert name.startswith("raidar-oracle-")
    assert docker.argvs[3][-2:] == ["-a", name]  # start -a <name>
    assert docker.argvs[5][-3:] == ["rm", "-f", name]
    with tarfile.open(fileobj=io.BytesIO(docker.copied_in)) as archive:
        submitted = archive.extractfile("app/main.py")
        assert submitted is not None
        assert submitted.read() == b"x = 1\n"
    assert (run.out_dir / "ledger.json").read_text() == "{}"
    evidence = json.loads((run.out_dir / "secret-isolation.json").read_text())
    assert evidence["judge_key_in_sandbox"] is False
    assert evidence["sandbox_env_names"]["container"] == ["HOME", "PATH"]


@pytest.mark.parametrize("failing", ["create", "cp-in"])
async def test_a_transfer_that_exits_nonzero_is_reported_and_never_starts_the_grader(
    tmp_path: Path, failing: str
) -> None:
    docker = _docker(**{failing: _Reply(code=125, out=b"no space left")})

    result = await _sandbox(docker).run(_run(tmp_path))

    assert (result.exit_code, result.timed_out, result.output) == (125, False, "no space left")
    assert "start" not in docker.verbs
    assert docker.verbs[-1] == "rm"  # the container is always removed


@pytest.mark.parametrize("failing", ["create", "cp-in"])
async def test_a_hung_transfer_is_reported_as_a_timeout_of_that_docker_command(
    tmp_path: Path, failing: str
) -> None:
    docker = _docker(**{failing: _Reply(hang_first=True)})

    result = await _sandbox(docker).run(_run(tmp_path))

    assert result.exit_code is None
    assert not result.timed_out  # the grader never ran: infrastructure, not a graded timeout
    assert result.output == f"{'create' if failing == 'create' else 'cp'} timed out after 600 s"
    assert docker.procs[docker.verbs.index(failing)].killed


async def test_a_container_that_exceeds_its_budget_is_killed_removed_and_reported(
    tmp_path: Path,
) -> None:
    docker = _docker(start=_Reply(hang_first=True, out=b"partial"))

    result = await _sandbox(docker).run(_run(tmp_path))

    assert (result.exit_code, result.timed_out) == (None, True)
    assert result.output.startswith("partial")
    assert docker.verbs == ["create", "inspect", "cp-in", "start", "kill", "cp-out", "rm"]
    start = docker.procs[docker.verbs.index("start")]
    assert start.killed
    name = docker.argvs[0][docker.argvs[0].index("--name") + 1]
    assert docker.argvs[4][-2:] == ["kill", name]


async def test_a_kill_that_cannot_run_still_reaps_the_started_process(tmp_path: Path) -> None:
    docker = _docker(
        start=_Reply(hang_first=True, out=b"partial"), kill=_Reply(raises=OSError("daemon gone"))
    )

    with pytest.raises(OSError, match="daemon gone"):
        await _sandbox(docker).run(_run(tmp_path))

    assert docker.procs[docker.verbs.index("start")].killed
    assert docker.verbs[-1] == "rm"


def _bundle_of(*members: tarfile.TarInfo) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as bundle:
        for member in members:
            bundle.addfile(member)
    return buffer.getvalue()


def _escaping_link() -> bytes:
    link = tarfile.TarInfo("out/escape")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    return _bundle_of(link)


@pytest.mark.parametrize(
    ("reply", "note"),
    [
        (_Reply(code=1, err=b"no such path"), "copy-out failed: no such path"),
        (
            _Reply(out=b"this is not a tar archive" * 50),
            "copy-out failed: unpacking the out directory failed",
        ),
        (_Reply(out=_escaping_link()), "copy-out failed: unpacking the out directory failed"),
    ],
    ids=["nonzero", "malformed", "escaping-link"],
)
async def test_unusable_copy_out_is_noted_in_the_output_not_raised(
    tmp_path: Path, reply: _Reply, note: str
) -> None:
    run = _run(tmp_path)

    result = await _sandbox(_docker(**{"cp-out": reply})).run(run)

    assert result.exit_code == 3  # the graded exit code survives
    assert note in result.output
    assert not (run.out_dir / "escape").exists()
    assert (run.out_dir / "secret-isolation.json").is_file()


@pytest.mark.parametrize(
    ("inspect", "error"),
    [
        (_Reply(code=1, out=b"boom"), RuntimeError),
        (_Reply(out=b"PATH\nOPENROUTER_API_KEY\n"), SecretIsolationError),
    ],
)
async def test_a_container_whose_environment_cannot_be_proven_clean_never_starts(
    tmp_path: Path, inspect: _Reply, error: type[Exception]
) -> None:
    docker = _docker(inspect=inspect)

    with pytest.raises(error):
        await _sandbox(docker).run(_run(tmp_path))

    assert "start" not in docker.verbs
    assert docker.verbs[-1] == "rm"


async def test_a_judge_key_in_the_run_environment_is_refused_before_any_docker_call(
    tmp_path: Path,
) -> None:
    docker = _docker()
    run = replace(_run(tmp_path), env={"OPENROUTER_API_KEY": "secret"})

    with pytest.raises(SecretIsolationError):
        await _sandbox(docker).run(run)

    assert docker.argvs == []


async def test_a_failing_cleanup_never_masks_the_run_result(tmp_path: Path) -> None:
    docker = _docker(rm=_Reply(raises=OSError("docker vanished")))

    result = await _sandbox(docker).run(_run(tmp_path))

    assert result.exit_code == 3


async def test_environment_id_is_the_trimmed_image_id_or_an_error() -> None:
    present = _docker(image=_Reply(out=b"sha256:abc\n"))
    assert await _sandbox(present).environment_id() == "sha256:abc"
    assert present.argvs[0][:5] == ["docker", "--context", "ctx", "image", "inspect"]

    for reply in (_Reply(code=1, out=b"No such image"), _Reply(out=b"  \n")):
        with pytest.raises(RuntimeError, match="grader image grader:1 is not available"):
            await _sandbox(_docker(image=reply)).environment_id()


def test_staging_replaces_stale_submission_and_output_files(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "main.py").write_text("new implementation\n")
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "old-result").write_text("untrusted stale evidence")
    app, out = sandbox_module.stage_snapshot(snapshot, staging)
    assert (app / "main.py").read_text() == "new implementation\n"
    assert list(out.iterdir()) == []
    assert not (staging / "old-result").exists()


async def test_cancellation_reaps_the_attached_process_and_removes_the_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    communicate = _Proc.communicate

    async def cancelled(self: _Proc) -> tuple[bytes | None, bytes]:
        if self.reply.out == b"cancelled":
            raise asyncio.CancelledError
        return await communicate(self)

    monkeypatch.setattr(_Proc, "communicate", cancelled)
    docker = _docker(start=_Reply(out=b"cancelled"))
    with pytest.raises(asyncio.CancelledError):
        await _sandbox(docker).run(_run(tmp_path))
    attached = docker.procs[docker.verbs.index("start")]
    assert attached.killed
    assert docker.verbs[-1] == "rm"
