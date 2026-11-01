"""Contract tests for ``install/1``: root-owned, offline, hash-checked, wheels-only trees."""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from raidar.contract import wheel_cache
from raidar.contract.wheels import LockedWheel
from raidar.subject.primitive import calls, install
from tests.dependency_support import make_wheel
from tests.primitive_support import LocalEnvironment, relocate

needs_uv = pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required")
INDEX = "https://pypi.org/simple"


@dataclass(frozen=True)
class Shipped:
    path: str
    data: bytes
    executable: bool = False
    digest: str | None = None

    @property
    def sha256(self) -> str:
        return self.digest or hashlib.sha256(self.data).hexdigest()


def _lock(wheels: dict[str, Path], extra: str = "") -> str:
    lines = ["version = 1", 'requires-python = ">=3.12"', ""]
    for name, wheel in wheels.items():
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        lines += [
            "[[package]]",
            f'name = "{name}"',
            'version = "1.0"',
            f'source = {{ registry = "{INDEX}" }}',
            extra,
            f'wheels = [{{ url = "{wheel.resolve().as_uri()}", hash = "sha256:{digest}", '
            f"size = {wheel.stat().st_size} }}]",
            "",
        ]
    lines += ["[[package]]", 'name = "tool"', 'version = "0.1.0"', 'source = { virtual = "." }']
    return "\n".join(lines) + "\n"


def _files(lock: str | None, *, source: str = "VALUE = 'raw'\n") -> list[Shipped]:
    files = [Shipped("prompt.md", b"prompt"), Shipped("tool/pyproject.toml", b"[project]\n")]
    files.append(Shipped("tool/src/mod.py", source.encode()))
    if lock is not None:
        files.append(Shipped("tool/uv.lock", lock.encode()))
    return files


@pytest.fixture
def container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    relocate(monkeypatch, tmp_path / "container")
    monkeypatch.setattr(wheel_cache, "WHEEL_URL_PREFIXES", ("file://",))
    return tmp_path


async def _install(
    container: Path, files: list[Shipped], **options: object
) -> install.InstalledPackage:
    return await install.install_package(
        LocalEnvironment(),
        container / "logs",
        reference="ns/pkg",
        package_digest="d" * 64,
        files=files,
        cache_root=container / "cache",
        owner=None,
        **options,  # type: ignore[arg-type]
    )


@needs_uv
async def test_fresh_installs_match_despite_different_uploaded_wheel_timestamps(
    container: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = make_wheel(container / "wheels", "demo")
    files = _files(_lock({"demo": wheel}))
    original = install._stage
    timestamps = iter((1_000_000_000, 2_000_000_000))

    def stage(
        host: Path,
        tool: Sequence[install.ShippedFile],
        wheels: Sequence[LockedWheel],
        cache_root: Path | None,
    ) -> None:
        original(host, tool, wheels, cache_root)
        timestamp = next(timestamps)
        for path in (host / "wheels").iterdir():
            os.utime(path, ns=(timestamp, timestamp))

    monkeypatch.setattr(install, "_stage", stage)
    first = await _install(container, files)
    shutil.rmtree(first.root)
    second = await _install(container, files)

    assert first.digest == second.digest


@needs_uv
async def test_a_tree_changed_after_install_is_not_verified(container: Path) -> None:
    wheel = make_wheel(container / "wheels", "demo")
    package = await _install(container, _files(_lock({"demo": wheel})))

    (Path(package.root) / "venv" / "lib").joinpath("planted.txt").write_text("x")

    check = await package.verify()
    assert not check.ok
    assert check.digest != check.expected


@needs_uv
async def test_installing_over_an_existing_tree_is_refused(container: Path) -> None:
    files = _files(_lock({}))
    await _install(container, files)

    with pytest.raises(install.InstallError, match="already exists"):
        await _install(container, files)


async def _reuse(
    container: Path, files: list[Shipped] | None = None
) -> install.InstalledPackage | None:
    return await install.reuse_installed(
        LocalEnvironment(),
        container / "logs-2",
        reference="ns/pkg",
        package_digest="d" * 64,
        files=files or (),
        owner=None,
    )


async def test_nothing_is_reused_when_nothing_was_installed(container: Path) -> None:
    assert await _reuse(container) is None
    assert not (Path(calls.STAGES_ROOT) / "ns" / "pkg").exists()  # nothing was created


@needs_uv
async def test_a_second_checkpoint_measures_the_same_tree_and_records_it(container: Path) -> None:
    wheel = make_wheel(container / "wheels", "demo")
    files = _files(_lock({"demo": wheel}))
    first = await _install(container, files)

    again = await _reuse(container, files)

    assert again is not None
    assert again.digest == first.digest
    assert again.record["tree_digest"] == first.record["tree_digest"]
    assert [w.name for w in again.wheels] == ["demo"]
    assert (container / "logs-2" / "install" / "ns--pkg.json").is_file()
    assert (await again.verify()).ok


@needs_uv
async def test_a_tree_changed_between_checkpoints_is_measured_as_changed_not_repaired(
    container: Path,
) -> None:
    files = _files(_lock({}))
    first = await _install(container, files)
    planted = Path(first.root) / "tool" / "src" / "planted.py"
    planted.write_text("x")

    again = await _reuse(container)

    assert again is not None
    assert again.digest != first.digest  # the caller compares with the locked digest
    assert planted.exists()  # nothing was deleted or repaired


@needs_uv
async def test_a_special_file_in_the_installed_tree_is_refused(container: Path) -> None:
    first = await _install(container, _files(_lock({})))
    os.mkfifo(Path(first.root) / "tool" / "fifo")

    with pytest.raises(install.InstallError, match="special file"):
        await _reuse(container)


async def test_a_bad_reference_is_refused_before_looking(container: Path) -> None:
    with pytest.raises(install.InstallError, match="reference"):
        await install.reuse_installed(
            LocalEnvironment(), container, reference="../x", package_digest="d"
        )


@needs_uv
async def test_a_command_may_not_overwrite_an_existing_executable(container: Path) -> None:
    (Path(install.LINKS_DIR) / "taken").write_text("#!/bin/sh\n")

    with pytest.raises(install.InstallError, match="already exists"):
        await _install(container, _files(_lock({})), commands={"taken": ["python"]})
    assert not (Path(calls.STAGES_ROOT) / "ns" / "pkg").exists()


@needs_uv
async def test_a_tampered_staged_file_is_refused_by_the_root_install(container: Path) -> None:
    files = _files(_lock({}))
    files[2] = Shipped("tool/src/mod.py", b"VALUE = 1\n", digest="0" * 64)

    with pytest.raises(install.InstallError, match="does not match its digest"):
        await _install(container, files)
    assert not (Path(calls.STAGES_ROOT) / "ns" / "pkg").exists()


@needs_uv
async def test_a_wheel_that_does_not_match_its_locked_hash_is_never_installed(
    container: Path,
) -> None:
    wheel = make_wheel(container / "wheels", "demo")
    lock = _lock({"demo": wheel}).replace(hashlib.sha256(wheel.read_bytes()).hexdigest(), "a" * 64)

    with pytest.raises(ValueError, match="does not match its locked sha256"):
        await _install(container, _files(lock))
    assert not (Path(calls.STAGES_ROOT) / "ns" / "pkg").exists()


# ---------------------------------------------------------------- policy refusals (no uv)
SDIST = (
    'sdist = { url = "https://files.pythonhosted.org/d.tar.gz", '
    f'hash = "sha256:{"c" * 64}", size = 1 }}\n'
)
PROJECT = '[[package]]\nname = "tool"\nversion = "0.1.0"\nsource = { virtual = "." }\n'


def _package(extra: str = "", wheel: str = "demo-1.0-py3-none-any.whl", source: str = "") -> str:
    url = f"https://files.pythonhosted.org/packages/{wheel}"
    source = source or f'{{ registry = "{INDEX}" }}'
    return (
        f'version = 1\n\n[[package]]\nname = "demo"\nversion = "1.0"\nsource = {source}\n'
        f'{extra}wheels = [{{ url = "{url}", hash = "sha256:{"b" * 64}", size = 1 }}]\n\n{PROJECT}'
    )


@pytest.mark.parametrize(
    ("lock", "message"),
    [
        (None, "no tool/uv.lock"),
        (_package(SDIST), "sdist"),
        ("version = 1\n" + PROJECT + SDIST, "sdist"),
        (_package(wheel="demo-1.0-cp311-cp311-win_amd64.whl"), "no (arm64|amd64) CPython"),
        (_package(source='{ git = "https://x/y" }'), "not an index package"),
        (_package(source='{ path = "../demo" }'), "not an index package"),
    ],
)
async def test_source_builds_and_unlocked_dependencies_are_refused_before_anything_runs(
    container: Path, lock: str | None, message: str
) -> None:
    environment = LocalEnvironment()

    with pytest.raises(install.InstallError, match=message):
        await install.install_package(
            environment,
            container / "logs",
            reference="ns/pkg",
            package_digest="d" * 64,
            files=_files(lock),
        )
    assert not any("uv" in command for command in environment.commands)
    assert not (Path(calls.STAGES_ROOT) / "ns" / "pkg").exists()


@pytest.mark.parametrize("reference", ["../x", "Ns/Pkg", "a/b/c", ""])
async def test_a_bad_reference_is_refused(container: Path, reference: str) -> None:
    with pytest.raises(install.InstallError, match="reference"):
        await install.install_package(
            LocalEnvironment(), container, reference=reference, package_digest="d", files=[]
        )
