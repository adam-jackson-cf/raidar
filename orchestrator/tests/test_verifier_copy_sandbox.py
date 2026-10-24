"""DockerCopySandbox: the grader fed and read by ``docker cp`` (argv/tar need no Docker)."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from raidar.design.problem import direct
from raidar.design.problem.models import ProblemId
from raidar.grading.grader_image import ensure_grader_image
from raidar.grading.sandbox import (
    CONTAINER_LAYOUT,
    GRADER_ROLE_LABEL,
    DockerCopySandbox,
    SandboxRun,
    build_copy_in_tar,
    extract_out_tar,
)
from tests.component_trial_support import docker_context, docker_target_reachable
from tests.problems_support import POSITIVE_REF, build_fixture_store
from tests.verifier_support import LocalProcessSandbox


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


@pytest.mark.skipif(
    not docker_target_reachable(),
    reason="builds the grader image: needs a reachable Docker target (make target-status)",
)
async def test_copy_sandbox_grades_the_fixture_like_the_local_grader(tmp_path: Path) -> None:
    problem = build_fixture_store(tmp_path / "problems").load(ProblemId.parse(POSITIVE_REF))
    image = await ensure_grader_image(
        problem.bundle.manifest.grader, docker_context=docker_context()
    )
    local = await direct.grade_references(
        problem, LocalProcessSandbox(tmp_path / "local"), repeats=1, concurrency=1
    )
    copied = await direct.grade_references(
        problem,
        DockerCopySandbox(image=image.tag, docker_context=docker_context()),
        repeats=1,
        concurrency=1,
    )

    assert copied == local
    assert all(
        grade.infra_failure is None for runs in copied.values() for grade in runs[0].values()
    )
