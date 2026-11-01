"""``raidar/verify`` reaches the spike gate's verdict on every check of the same trees.

Two real containers of the pack image, no model. The spike side builds its tools environment the
way the spike stage did (the pins of the schema-1 ``one-shot-verify``) and runs the spike gate's
own ``decide`` as the agent's user over each tree. The package side runs the package through the
generic gate primitive, offline, exactly as ``stage-check`` does. For every tree the verdict and
each check's status and exit code must agree, so a drift in a pin, the excluded directories, the
file selection, mypy's use of the project environment or the per-test timeout shows up here.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from raidar_contract.package.manifest import StagePackage
from raidar_contract.package.registry import load_stage_package

from raidar.contract.snapshot import SNAPSHOT_EXCLUDES
from raidar.subject.environment.python.image import image_tag
from raidar.subject.environment.python.pack_definition import load_pack_spec
from raidar.subject.primitive import gate_tool
from raidar.subject.workflow.stage_check import Rig, _gate_once
from tests.docker_trial_support import docker_context, docker_target_ready
from tests.release_support import REAL_LAB

pytestmark = pytest.mark.skipif(
    not docker_target_ready(),
    reason="needs a ready Docker target (data check passing) and the built python pack image",
)

PACKAGE = REAL_LAB / "stages" / "raidar" / "verify"
REFERENCE = "raidar/verify"
SPIKE_PINS = ("ruff==0.8.6", "mypy==2.3.1", "pytest==9.1.1")
SPIKE_TEST_TIMEOUT = 60


def _test_timeout(case: str) -> int:
    """The per-test limit the case's own context sets (the package's effective param)."""
    path = PACKAGE / "fixtures" / case / "context.json"
    if not path.is_file():
        return SPIKE_TEST_TIMEOUT
    return int(json.loads(path.read_text(encoding="utf-8"))["params"]["test_timeout"])


SPIKE_CHECKS = [
    {"name": "lint", "category": "lint", "argv": ["ruff", "check"], "blocking": True},
    {
        "name": "format",
        "category": "format",
        "argv": ["ruff", "format", "--check"],
        "blocking": True,
    },
    {"name": "types", "category": "types", "argv": ["mypy", "--strict"], "blocking": True},
    {"name": "tests", "category": "tests", "argv": [], "blocking": True},
]
AGENT_UID = 1000
HOME = "/opt/spike-gate"
CALC = "def add(a: int, b: int) -> int:\n    return a + b\n"
TEST_CALC = "from calc import add\n\n\ndef test_add() -> None:\n    assert add(1, 2) == 3\n"
UNFORMATTED_AND_UNUSED = "import os\ndef  f( ) -> int:\n  return 1\n"
DECIDE = (
    "import json, pathlib, sys; sys.path.insert(0, sys.argv[1]); import verify_gate; "
    "d = verify_gate.decide(json.load(open(sys.argv[2])), pathlib.Path(sys.argv[3])); "
    "print(json.dumps({'verdict': d.verdict, 'checks': d.summary()}))"
)

# Trees the fixtures do not hold: each exercises one rule of which files the checks see.
EXTRA_TREES: dict[str, dict[str, str]] = {
    "no-python-files": {"README.md": "# nothing to check\n"},
    "hidden-excluded-directories": {
        "calc.py": CALC,
        "test_calc.py": TEST_CALC,
        ".agents/skill.py": UNFORMATTED_AND_UNUSED,
        ".claude/hook.py": UNFORMATTED_AND_UNUSED,
        "node_modules/pkg.py": UNFORMATTED_AND_UNUSED,
        "dist/out.py": UNFORMATTED_AND_UNUSED,
    },
    "nested-package-failing-lint-and-format": {
        "calc.py": CALC,
        "test_calc.py": TEST_CALC,
        "pkg/__init__.py": "",
        "pkg/bad.py": UNFORMATTED_AND_UNUSED,
    },
    "slow-test": {
        "calc.py": CALC,
        "test_calc.py": (
            "import time\n\nfrom calc import add\n\n\n"
            "def test_add() -> None:\n    time.sleep(90)\n    assert add(1, 2) == 3\n"
        ),
    },
}
FIXTURE_CASES = sorted(
    folder.name for folder in (PACKAGE / "fixtures").iterdir() if (folder / "state").is_dir()
)
CASES = [*FIXTURE_CASES, *EXTRA_TREES]


def _docker(*args: str, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "--context", docker_context(), *args],
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _docker_ok(*args: str, timeout: int = 900) -> str:
    done = _docker(*args, timeout=timeout)
    assert done.returncode == 0, f"docker {args[0]} failed: {done.stdout}\n{done.stderr}"
    return done.stdout


def _write_tree(root: Path, files: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


@pytest.fixture(scope="module")
def trees(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Every tree compared, by case name: each package fixture's state and the extra trees."""
    scratch = tmp_path_factory.mktemp("parity")
    found = {case: PACKAGE / "fixtures" / case / "state" for case in FIXTURE_CASES}
    for name, files in EXTRA_TREES.items():
        _write_tree(scratch / name, files)
        found[name] = scratch / name
    return found


def _exec(name: str, script: str, *, user: str = "root") -> str:
    return _docker_ok("exec", "--user", user, name, "bash", "-c", script)


def _spike_decisions(name: str, trees: dict[str, Path], configs: Path) -> dict[str, dict[str, Any]]:
    _exec(
        name,
        "set -eu; cd /; export UV_NO_CONFIG=1 UV_PYTHON_DOWNLOADS=never; "
        f"mkdir -p {HOME} /work; uv venv -q --python /usr/local/bin/python3 {HOME}/tools; "
        f"uv pip install -q --python {HOME}/tools/bin/python {' '.join(SPIKE_PINS)}",
    )
    _docker_ok("cp", str(Path(gate_tool.__file__)), f"{name}:{HOME}/verify_gate.py")
    found: dict[str, dict[str, Any]] = {}
    for case, tree in trees.items():
        root, scratch = f"/work/{case}", f"/work/scratch-{case}"
        config = {
            "root": root,
            "tools": f"{HOME}/tools",
            "excludes": list(SNAPSHOT_EXCLUDES),
            "test_timeout": _test_timeout(case),
            "checks": SPIKE_CHECKS,
            "scratch": scratch,
        }
        written = configs / f"{case}.json"
        written.write_text(json.dumps(config), encoding="utf-8")
        _exec(name, f"rm -rf {root} {scratch}; mkdir -p {root} {scratch}")
        _docker_ok("cp", f"{tree}/.", f"{name}:{root}")
        _docker_ok("cp", str(written), f"{name}:/work/{case}.json")
        _exec(name, f"chown -R {AGENT_UID}:{AGENT_UID} {root} {scratch} /work/{case}.json")
        out = _exec(
            name,
            f"cd {root} && {HOME}/tools/bin/python -B -c {json.dumps(DECIDE)} "
            f"{HOME} /work/{case}.json {scratch}/decide",
            user=str(AGENT_UID),
        )
        found[case] = json.loads(out.splitlines()[-1])
    return found


@pytest.fixture(scope="module")
def spike(
    trees: dict[str, Path], tmp_path_factory: pytest.TempPathFactory
) -> Iterator[dict[str, dict[str, Any]]]:
    """The spike gate's decision over every tree, from one networked container of the pack image."""
    name = f"raidar-verify-parity-{uuid.uuid4().hex[:10]}"
    _docker_ok(
        "run", "--detach", "--name", name, "--user", "root", "--entrypoint", "sleep",
        image_tag(load_pack_spec()), "3600",
    )  # fmt: skip
    try:
        yield _spike_decisions(name, trees, tmp_path_factory.mktemp("spike-configs"))
    finally:
        _docker("rm", "--force", name, timeout=120)


def _package_with_extra_fixtures(scratch: Path) -> StagePackage:
    """A copy of the package whose fixtures also hold the extra trees (read as data only)."""
    root = scratch / "verify"
    shutil.copytree(PACKAGE, root, symlinks=True)
    context = (PACKAGE / "fixtures" / "pass" / "context.json").read_text(encoding="utf-8")
    for name, files in EXTRA_TREES.items():
        _write_tree(root / "fixtures" / name / "state", files)
        (root / "fixtures" / name / "context.json").write_text(context, encoding="utf-8")
    return replace(load_stage_package(PACKAGE), root=root)


@pytest.fixture(scope="module")
def package_side(
    tmp_path_factory: pytest.TempPathFactory, trees: dict[str, Path]
) -> dict[str, dict[str, Any]]:
    """The package's own gate result over every tree, offline in throwaway pack containers."""
    scratch = tmp_path_factory.mktemp("package-side")
    package = _package_with_extra_fixtures(scratch)
    rig = Rig(image_tag(load_pack_spec()), docker_context(), scratch / "logs", None)

    async def run_all() -> dict[str, dict[str, Any]]:
        return {
            case: await _gate_once(package, REFERENCE, case, f"parity-{case}", rig)
            for case in trees
        }

    return asyncio.run(run_all())


def _spike_statuses(checks: list[dict[str, Any]]) -> dict[str, tuple[str, int | None]]:
    return {item["name"]: (item["status"], item["exit"]) for item in checks}


def _package_statuses(checks: list[list[Any]]) -> dict[str, tuple[str, int | None]]:
    return {name.removeprefix("verify."): (status, code) for name, status, code in checks}


@pytest.mark.parametrize("case", CASES)
def test_each_check_reaches_the_spike_gates_verdict_on_the_same_tree(
    case: str,
    spike: dict[str, dict[str, Any]],
    package_side: dict[str, dict[str, Any]],
) -> None:
    old, new = spike[case], package_side[case]
    expected = _spike_statuses(old["checks"])
    assert set(expected) == {"lint", "format", "types", "tests"}
    assert _package_statuses(new["checks"]) == expected
    assert old["verdict"] == new["attribution"]


def test_the_compared_trees_fail_exactly_the_checks_each_is_named_for(
    spike: dict[str, dict[str, Any]],
) -> None:
    """The trees mean what their names say, so the parity above compares distinct verdicts."""
    failed = {
        case: sorted(
            name for name, (status, _) in _spike_statuses(decision["checks"]).items()
            if status != "passed"
        )
        for case, decision in spike.items()
    }  # fmt: skip
    assert failed["pass"] == failed["pass-excluded-directories"] == []
    assert failed["hidden-excluded-directories"] == []
    assert failed["refuse-lint"] == ["lint"]
    assert failed["refuse-format"] == ["format"]
    assert failed["refuse-types"] == ["types"]
    assert failed["refuse"] == ["tests"]
    assert failed["refuse-no-tests"] == ["tests"]
    assert failed["no-python-files"] == ["tests"]
    assert failed["slow-test"] == ["tests"]
    assert failed["refuse-test-timeout-override"] == ["tests"]
    assert failed["pass-slow-test-default-timeout"] == []
    assert failed["nested-package-failing-lint-and-format"] == ["format", "lint"]
