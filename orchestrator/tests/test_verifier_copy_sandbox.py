"""DockerCopySandbox: the grader fed and read by ``docker cp`` (no Docker needed)."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from raidar.verifier.sandbox import (
    CONTAINER_LAYOUT,
    GRADER_ROLE_LABEL,
    DockerCopySandbox,
    DockerSandbox,
    SandboxRun,
    build_copy_in_tar,
    extract_out_tar,
    grading_sandbox,
)


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


def test_create_argv_has_the_lockdown_flags_and_no_host_paths(tmp_path: Path) -> None:
    run = _run(tmp_path)
    created = DockerCopySandbox(image="grader:1").create_argv(run, "raidar-oracle-x")
    mounted = DockerSandbox(image="grader:1").run_argv(run, "raidar-oracle-x")
    assert created[3] == "create"
    assert "--rm" not in created
    assert "-v" not in created
    assert str(tmp_path) not in " ".join(created)
    assert created[created.index("--label") :][:2] == ["--label", GRADER_ROLE_LABEL]
    for flag in ("--network", "--user", "--cap-drop", "--security-opt", "--memory", "-e"):
        assert flag in created
    assert created[-3:] == ["grader:1", "pytest", "-q"]
    assert (
        created[created.index("-w") : created.index("-w") + 2]
        == mounted[mounted.index("-w") : mounted.index("-w") + 2]
    )


def test_copy_in_tar_sets_ownership_and_modes(tmp_path: Path) -> None:
    run = _run(tmp_path)
    archive = tmp_path / "in.tar"
    build_copy_in_tar(run, CONTAINER_LAYOUT, archive)
    with tarfile.open(archive) as bundle:
        members = {m.name: m for m in bundle.getmembers()}
    assert (members["app/main.py"].uid, members["app/main.py"].gid) == (1000, 1000)
    assert members["app/main.py"].mode & 0o200
    tests = members["tests/test_a.py"]
    assert (tests.uid, tests.gid) == (0, 0)
    assert not tests.mode & 0o222
    assert not members["oracle/runtime/raidar_oracle_plugin.py"].mode & 0o222
    out = members["oracle/out"]
    assert out.isdir()
    assert (out.uid, out.gid) == (1000, 1000)
    assert not any(name.startswith("oracle/out/") for name in members)


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


def test_grading_sandbox_follows_the_copy_mode_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAIDAR_COPY_MODE", raising=False)
    assert type(grading_sandbox("g:1", "orbstack")) is DockerSandbox
    monkeypatch.setenv("RAIDAR_COPY_MODE", "1")
    assert isinstance(grading_sandbox("g:1", "orbstack"), DockerCopySandbox)
