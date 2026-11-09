"""Fast behavioural proofs for maintained Python inventory coverage."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
ROOTS = (
    "orchestrator/src",
    "orchestrator/tests",
    "contract/src",
    "scripts",
    "lab/stages/raidar/define/tool/src",
    "lab/stages/custom/new/tool/src",
)


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "mypy_coverage_proofs", ROOT / "scripts/checks/check-mypy-coverage.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("omitted", ROOTS)
def test_each_omitted_maintained_root_has_one_actionable_finding(omitted: str) -> None:
    findings = _checker().coverage_findings(ROOTS, [root for root in ROOTS if root != omitted])
    assert len(findings) == 1
    assert re.fullmatch(
        rf"{re.escape(omitted)}: MYPY\.package-coverage: [^\r\n;]+; [^\r\n]+",
        findings[0],
    )


def test_package_selection_does_not_hide_a_new_sibling_package() -> None:
    covered = "lab/stages/custom/new/tool/src/existing"
    omitted = "lab/stages/custom/new/tool/src/existing_extra"
    findings = _checker().coverage_findings([covered, omitted, omitted], [covered])
    assert len(findings) == 1
    assert findings[0].startswith(f"{omitted}: MYPY.package-coverage:")
    assert "; " in findings[0]
    assert "\n" not in findings[0]


def test_complete_root_inventory_covers_packages_without_redundant_targets() -> None:
    discovered = [*ROOTS, "orchestrator/src/raidar", "contract/src/raidar_contract"]
    assert _checker().coverage_findings(discovered, ROOTS) == []


def test_nonmaintained_paths_never_create_inventory_drift() -> None:
    excluded = [
        "docs/examples",
        "var/generated/tool/src",
        "orchestrator/tests/fixtures/programs",
        "lab/stages/raidar/define/fixtures/tool/src",
        "lab/stages/custom/new/tool/src/fixtures",
    ]
    assert _checker().coverage_findings([*ROOTS, *excluded], ROOTS) == []
    assert _checker().coverage_findings(excluded, []) == []


def test_multiple_omissions_are_reported_once_in_stable_order() -> None:
    checker = _checker()
    discovered = ["scripts", "orchestrator/tests", "scripts"]
    findings = checker.coverage_findings(discovered, [])
    assert len(findings) == 2
    assert findings == checker.coverage_findings(list(reversed(discovered)), [])
    assert {line.split(":", 1)[0] for line in findings} == {"scripts", "orchestrator/tests"}
