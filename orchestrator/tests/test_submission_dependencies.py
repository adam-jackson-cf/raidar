"""FCR-007: packages a submission declares are graded offline from Raidar's pinned copies."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from raidar.grading import submission_deps
from raidar.grading.bundle import (
    BundleManifest,
    GraderSpec,
    VerifierBundle,
    VerifierStepSpec,
    write_bundle,
)
from raidar.grading.grader import grade_step
from raidar.grading.grader_image import grader_dockerfile
from raidar.grading.submission_deps import (
    SubmissionDependencies,
    declared_requirements,
    provisioned_pins,
    python_path_entries,
    resolve_dependencies,
)
from tests.verifier_support import LocalProcessSandbox

PINS = {"pyyaml": "PyYAML==6.0.3"}

TEST = """
import subprocess


def test_reads_config(entrypoint_argv):
    result = subprocess.run(entrypoint_argv, capture_output=True, text=True)
    assert result.stdout.strip() == "parsed", result.stderr
"""
CONFTEST = """
import shlex

import pytest


def pytest_addoption(parser):
    parser.addoption("--entrypoint", action="store", required=True)
    parser.addoption("--checkpoint", action="store", required=True)


@pytest.fixture(scope="session")
def entrypoint_argv(request):
    return shlex.split(request.config.getoption("--entrypoint"))
"""


def _snapshot(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (root / name).write_text(text)
    return root


def test_declared_requirements_come_from_pyproject_and_requirements_txt(tmp_path: Path) -> None:
    snapshot = _snapshot(
        tmp_path / "app",
        {
            "pyproject.toml": '[project]\nname = "x"\ndependencies = ["PyYAML>=6.0", "requests"]\n',
            "requirements.txt": "# pins\npyyaml==5.1  # old\n-r other.txt\nnot a requirement!!\n",
        },
    )
    assert declared_requirements(snapshot) == (
        "PyYAML>=6.0",
        "requests",
        "pyyaml==5.1",
        "not a requirement!!",
    )
    resolved = resolve_dependencies(snapshot, PINS)
    assert resolved.provided == ("PyYAML==6.0.3",)
    assert resolved.unprovided == (
        "requests (no pinned offline package)",
        "pyyaml==5.1 (pinned PyYAML==6.0.3 does not satisfy it)",
        "not a requirement!! (unparseable requirement)",
    )
    assert python_path_entries(resolved) == ("/opt/raidar-submission-deps/pyyaml",)
    # Nothing declared, nothing provided: a package is never offered unasked.
    bare = resolve_dependencies(_snapshot(tmp_path / "bare", {"app.py": ""}), PINS)
    assert bare == SubmissionDependencies()
    broken = _snapshot(tmp_path / "broken", {"pyproject.toml": "[project\n"})
    assert declared_requirements(broken) == ()
    no_list = _snapshot(tmp_path / "nolist", {"pyproject.toml": '[project]\nname = "x"\n'})
    assert declared_requirements(no_list) == ()


def test_the_grader_image_pins_the_problems_scb_dependencies() -> None:
    assert provisioned_pins(["pyyaml", "unpinned-thing", "bad req!!"]) == PINS
    with_yaml = grader_dockerfile(GraderSpec(base_image="base", test_dependencies=("pyyaml",)))
    assert (
        "uv pip install --no-cache --python /usr/local/bin/python3 "
        '--target /opt/raidar-submission-deps/pyyaml "PyYAML==6.0.3"'
    ) in with_yaml
    without = grader_dockerfile(GraderSpec(base_image="base"))
    assert "raidar-submission-deps" not in without


def _bundle(root: Path) -> VerifierBundle:
    tests = _snapshot(root / "tests", {"conftest.py": CONFTEST, "test_checkpoint_1.py": TEST})
    return write_bundle(
        root / "bundle",
        BundleManifest(
            problem="cfg",
            source={"format": "test-fixture"},
            env={},
            grader=GraderSpec(base_image="local", test_dependencies=("raidar-fake-yaml",)),
            steps=(
                VerifierStepSpec(
                    name="checkpoint_1",
                    checkpoint="checkpoint_1",
                    test_files=("test_checkpoint_1.py",),
                    pytest_args=(
                        "{tests}/test_checkpoint_1.py",
                        "--checkpoint",
                        "checkpoint_1",
                        "--entrypoint",
                        f"{sys.executable} {{app}}/app.py",
                    ),
                ),
            ),
        ),
        tests,
    )


@pytest.mark.parametrize(
    ("declared", "solved"),
    [('dependencies = ["raidar-fake-yaml>=1"]', True), ("dependencies = []", False)],
)
async def test_a_declared_package_is_importable_by_the_graded_program_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared: str, solved: bool
) -> None:
    # The grader image's pinned copy, stood in for by a local package directory.
    deps_root = tmp_path / "deps"
    package = deps_root / "raidar-fake-yaml" / "raidar_fake_yaml"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("def load(text):\n    return 'parsed'\n")
    monkeypatch.setattr(submission_deps, "SUBMISSION_DEPS_ROOT", str(deps_root))
    monkeypatch.setattr(
        submission_deps, "SUBMISSION_DEPENDENCY_PINS", {"raidar-fake-yaml": "raidar-fake-yaml==1.0"}
    )
    snapshot = _snapshot(
        tmp_path / "app",
        {
            "app.py": "import raidar_fake_yaml\nprint(raidar_fake_yaml.load('a: 1'))\n",
            "pyproject.toml": f'[project]\nname = "app"\n{declared}\n',
        },
    )
    bundle = _bundle(tmp_path / "problem")
    result = await grade_step(
        bundle,
        "checkpoint_1",
        snapshot,
        LocalProcessSandbox(tmp_path / "sandbox"),
        tmp_path / "work",
        coverage=False,
    )
    assert result.grade.solved is solved
    dependencies = result.grading.dependencies
    assert dependencies is not None
    assert dependencies.provided == (("raidar-fake-yaml==1.0",) if solved else ())
