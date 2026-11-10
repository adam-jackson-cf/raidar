"""First evidence may establish limits, but cannot relax an existing budget."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("calibrated", [False, True])
def test_first_calibration_is_not_a_budget_increase(calibrated: bool) -> None:
    spec = importlib.util.spec_from_file_location(
        "budget_calibration_checker", ROOT / "scripts/checks/check-test-budgets.py"
    )
    assert spec is not None
    assert spec.loader is not None
    checker = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = checker
    spec.loader.exec_module(checker)
    policy = json.loads((ROOT / ".omp/test-budgets/limits.json").read_text())
    base = copy.deepcopy(policy)
    base.update(wallSeconds=None, perTestSeconds=None, exceptions={})
    cases = {
        f"test_measure.py::test_case_{index}": {
            "outcome": "passed",
            "phases": {"setup": 0.01, "call": 1.0 if index == 20 else 0.02, "teardown": 0.01},
        }
        for index in range(21)
    }
    report: dict[str, Any] = {
        "schemaVersion": 1,
        "revision": "measured-revision",
        "invocationId": "current",
        "profile": policy["profile"],
        "machine": {"imageVersion": "measured-image", "cpuModel": "measured-cpu"},
        "wallSeconds": 10.0,
        "inventory": list(cases),
        "cases": cases,
        "exitCode": 0,
        "complete": True,
    }
    baselines = []
    for index in range(5):
        baseline = copy.deepcopy(report)
        baseline["invocationId"] = f"measurement-{index}"
        baseline["measurement"] = {"runId": str(index), "jobId": "job", "successful": True}
        baselines.append(baseline)
    limits = checker.compute_limits(baselines)
    policy.update(wallSeconds=limits["wallSeconds"], perTestSeconds=limits["perTestSeconds"])
    policy["exceptions"] = {
        node: {
            "limitSeconds": ceiling,
            "owner": "repository-owner",
            "remedy": "Inject a runner to eliminate process setup",
            "reviewDeadline": "2026-12-10",
        }
        for node, ceiling in limits["exceptionCeilings"].items()
    }
    if calibrated:
        base.update(wallSeconds=limits["wallSeconds"], perTestSeconds=limits["perTestSeconds"])
    result = checker.check_budget(
        report,
        policy,
        baselines,
        revision="measured-revision",
        invocation_id="current",
        ci=True,
        now=lambda: date(2026, 11, 10),
        base_policy=base,
    )
    assert result["exitCode"] == (1 if calibrated else 0)
    assert any(finding["kind"] == "approval" for finding in result["findings"]) is calibrated
