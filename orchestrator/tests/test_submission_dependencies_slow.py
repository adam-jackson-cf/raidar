"""Slow-tier behaviours split from :mod:`tests.test_submission_dependencies`."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from raidar.grading import closure
from raidar.grading.grader import grade_step
from raidar.grading.sandbox import RUNTIME_DIR
from raidar.grading.submission_deps import (
    closure_dir,
)
from tests.dependency_support import FakeResolver
from tests.test_submission_dependencies import (
    _declaring as _declaring,
)
from tests.test_submission_dependencies import (
    _grading_bundle as _grading_bundle,
)
from tests.verifier_support import LocalProcessSandbox

pytestmark = pytest.mark.slow


async def test_the_graded_program_runs_in_the_closure_image_with_the_closure_on_its_path(
    tmp_path: Path,
) -> None:
    fake = FakeResolver()
    bundle = _grading_bundle(tmp_path / "problem", fake)
    sandbox = LocalProcessSandbox(tmp_path / "sandbox")
    result = await grade_step(
        bundle,
        "checkpoint_1",
        _declaring(tmp_path / "app", "flask"),
        sandbox,
        tmp_path / "work",
        coverage=False,
        resolver=fake,
    )
    assert result.grade.solved is True
    [run] = sandbox.runs
    deps = result.grading.dependencies
    assert deps is not None
    assert deps.provided == ("flask==1.0",)
    assert run.image == "local-closure"
    entry = closure_dir(deps.lock_sha256)
    assert run.env["RAIDAR_SUBMISSION_DEPS"] == entry
    assert run.env["PYTHONPATH"].endswith(f":{entry}")
    assert run.env["UV_OFFLINE"] == "1"


async def test_a_coverage_run_reuses_the_grading_runs_lock(tmp_path: Path) -> None:
    fake = FakeResolver()
    bundle = _grading_bundle(tmp_path / "problem", fake)
    sandbox = LocalProcessSandbox(tmp_path / "sandbox")
    fake.calls.clear()
    result = await grade_step(
        bundle,
        "checkpoint_1",
        _declaring(tmp_path / "app", "flask"),
        sandbox,
        tmp_path / "work",
        coverage=True,
        resolver=fake,
    )
    assert len(fake.calls) == 2  # both architectures, once
    assert result.coverage is not None
    assert len(sandbox.runs) == 2
    assert {run.image for run in sandbox.runs} == {"local-closure"}


async def test_run_docker_returns_the_exit_code_and_output(tmp_path: Path) -> None:
    script = tmp_path / "docker"
    script.write_text(
        f"#!{sys.executable}\nimport sys\nprint(' '.join(sys.argv[1:]))\nsys.exit(3)\n"
    )
    script.chmod(0o755)
    code, output = await closure.run_docker(str(script), "ctx", "ps", env={"X": "1"})
    assert (code, output.strip()) == (3, "--context ctx ps")
    code, output = await closure.run_docker(str(script), "ctx", "build", stdin=b"FROM x\n")
    assert code == 3


async def test_a_wedged_docker_command_is_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "docker"
    script.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
    script.chmod(0o755)
    monkeypatch.setattr(closure, "BUILD_TIMEOUT_S", 0.5)
    with pytest.raises(TimeoutError):
        await closure.run_docker(str(script), "ctx", "ps")


# ---------------------------------------------------- .pth and namespace packages
def test_pth_files_and_namespace_packages_of_the_closure_are_honoured(tmp_path: Path) -> None:
    """A split namespace and a .pth path remain usable in the offline closure."""
    deps = tmp_path / "deps"
    (deps / "rns" / "protobuf").mkdir(parents=True)
    (deps / "rns" / "protobuf" / "__init__.py").write_text("NAME = 'protobuf'\n")
    (deps / "extra" / "viaPth").mkdir(parents=True)
    (deps / "extra" / "viaPth" / "__init__.py").write_text("OK = True\n")
    (deps / "thing.pth").write_text("extra\n")
    other = tmp_path / "other" / "rns" / "cloud"
    other.mkdir(parents=True)
    (other / "__init__.py").write_text("NAME = 'cloud'\n")
    program = (
        "import rns.protobuf, rns.cloud, viaPth; "
        "print(rns.protobuf.NAME, rns.cloud.NAME, viaPth.OK)"
    )
    environment = {
        "PYTHONPATH": f"{RUNTIME_DIR}:{tmp_path / 'other'}:{deps}",
        "RAIDAR_SUBMISSION_DEPS": str(deps),
        "PATH": "",
    }
    done = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert done.stdout.strip() == "protobuf cloud True", done.stderr
    bare = {**environment, "RAIDAR_SUBMISSION_DEPS": ""}
    missing = subprocess.run(
        [sys.executable, "-c", "import viaPth"],
        capture_output=True,
        text=True,
        env=bare,
        check=False,
    )
    assert missing.returncode != 0
    assert "ModuleNotFoundError" in missing.stderr
