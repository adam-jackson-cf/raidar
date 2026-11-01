"""A4/V4: import closures lie in the identity inventory; package trees are never imported."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from raidar_contract.agent import AgentName
from raidar_contract.package.registry import load_stage_package

from raidar.scoring.framework.rescore import rescore_path
from raidar.scoring.framework.store import AssessmentStore
from raidar.subject.primitive.registry import PRIMITIVE_MODULES
from raidar.subject.workflow.identity import (
    ADAPTER_ENTRIES,
    RAIDAR_DIR,
    RUNNER_ID,
    package_primitives,
    primitive_code,
    primitive_inventory,
)
from tests.health_support import audit, healthy_trial
from tests.package_support import STAGE_FIXTURES
from tests.scorer_support import fake_registry, two_step_trial

pytestmark = pytest.mark.slow


def _runtime_files(modules: list[str]) -> set[str]:
    """``raidar`` files really loaded by importing ``modules`` in a fresh interpreter."""
    code = (
        "import importlib, json, sys\n"
        f"[importlib.import_module(m) for m in {modules!r}]\n"
        "print(json.dumps(sorted(n for n in sys.modules "
        "if n == 'raidar' or n.startswith('raidar.'))))\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    files = set()
    for name in json.loads(done.stdout):
        base = RAIDAR_DIR.parent / name.replace(".", "/")
        path = (
            base.with_suffix(".py") if base.with_suffix(".py").is_file() else base / "__init__.py"
        )
        if path.is_file():
            files.add(path.relative_to(RAIDAR_DIR).as_posix())
    return files


@pytest.mark.parametrize("contract", sorted(PRIMITIVE_MODULES))
def test_each_primitives_runtime_imports_lie_inside_its_inventory(contract: str) -> None:
    loaded = _runtime_files([PRIMITIVE_MODULES[contract]])

    for adapter in AgentName:
        assert loaded <= set(primitive_inventory(contract, adapter)), (
            f"{contract} imports outside its identity inventory: "
            f"{sorted(loaded - set(primitive_inventory(contract, adapter)))}"
        )


def test_the_package_runner_and_each_native_adapter_are_inside_the_inventory() -> None:
    runner = _runtime_files(["raidar.subject.workflow.package", "raidar.subject.workflow.runner"])
    for adapter, entries in ADAPTER_ENTRIES.items():
        loaded = _runtime_files([*entries])
        inventory = set(primitive_inventory(RUNNER_ID, adapter)) | set(
            primitive_inventory("frame/1", adapter)
        )
        assert runner <= set(primitive_inventory(RUNNER_ID, adapter))
        assert loaded <= inventory, sorted(loaded - inventory)


def test_real_primitive_code_is_deterministic_adapter_bound_and_core_is_adapter_free() -> None:
    package = load_stage_package(STAGE_FIXTURES / "gate-check")
    contracts = {"gate/1"}

    codex = primitive_code(contracts, AgentName.CODEX)
    claude = primitive_code(contracts, AgentName.CLAUDE_CODE)

    assert codex == primitive_code(contracts, AgentName.CODEX)
    assert all(codex[key] != claude[key] for key in codex)
    core = package_primitives([package])
    assert set(core) == {"frame/1", "install/1", "gate/1"}
    assert not set(core.values()) & set(codex.values())


MARKER_FIXTURE = Path(__file__).parent / "fixtures" / "stages" / "acme" / "marker-tool"
MARKER_MODULE = "marker_tool"


@pytest.fixture
def marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "imported.marker"
    monkeypatch.setenv("RAIDAR_IMPORT_MARKER", str(path))
    sys.modules.pop(MARKER_MODULE, None)
    return path


def _plant(destination: Path) -> None:
    shutil.copytree(MARKER_FIXTURE, destination)


def _assert_untouched(marker: Path) -> None:
    assert not marker.exists(), "package code ran on the host"
    assert MARKER_MODULE not in sys.modules


def test_the_health_audit_never_imports_a_package_tree_in_a_trial(
    tmp_path: Path, marker: Path
) -> None:
    t = healthy_trial(tmp_path)
    t.seal()
    _plant(t.trial_dir / "stages" / "marker-tool")
    _plant(t.trial_dir / "steps" / "checkpoint_1" / "agent" / "stages" / "marker-tool")

    checks = audit(t)

    assert checks
    _assert_untouched(marker)


def test_an_assessment_never_imports_a_package_tree_in_a_trial(
    tmp_path: Path, marker: Path
) -> None:
    trial = two_step_trial(tmp_path / "jobs" / "trial")
    _plant(trial / "stages" / "marker-tool")

    records = rescore_path(
        trial, ["fake-quality@1"], AssessmentStore(tmp_path / "s"), fake_registry()
    )

    assert records
    _assert_untouched(marker)
