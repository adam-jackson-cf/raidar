"""Capture selected pytest inventory and actual execution phases in one run."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

_REPORT = pytest.StashKey[dict[str, Any]]()


def pytest_configure(config: pytest.Config) -> None:
    config.stash[_REPORT] = {
        "inventory": [],
        "cases": {},
        "collected": False,
        "duplicatePhases": False,
        "collectionIncomplete": False,
    }


def pytest_collection_finish(session: pytest.Session) -> None:
    """Snapshot selection before execution, including cases a later -x will omit."""
    report = session.config.stash[_REPORT]
    report["inventory"] = [item.nodeid for item in session.items]
    report["collected"] = True


class _PhaseRecorder:
    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed or report.skipped:
            self.report["collectionIncomplete"] = True

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        case = self.report["cases"].setdefault(report.nodeid, {"phases": {}, "outcome": "passed"})
        phases = case["phases"]
        if report.when in phases:
            self.report["duplicatePhases"] = True
        phases[report.when] = report.duration
        if report.failed:
            case["outcome"] = "failed"
        elif report.skipped and case["outcome"] != "failed":
            case["outcome"] = "skipped"


def pytest_sessionstart(session: pytest.Session) -> None:
    session.config.pluginmanager.register(
        _PhaseRecorder(session.config.stash[_REPORT]), "guardrail-budget-phases"
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int | pytest.ExitCode) -> None:
    report = session.config.stash[_REPORT]
    inventory = report["inventory"]
    cases = report["cases"]
    report["complete"] = (
        int(exitstatus) == 0
        and report["collected"]
        and bool(inventory)
        and len(inventory) == len(set(inventory))
        and not report["duplicatePhases"]
        and not report["collectionIncomplete"]
        and set(inventory) == set(cases)
        and all(
            case["outcome"] == "passed" and set(case["phases"]) == {"setup", "call", "teardown"}
            for case in cases.values()
        )
    )
    Path(os.environ["RAIDAR_TEST_BUDGET_PHASE_REPORT"]).write_text(
        json.dumps(report, sort_keys=True) + "\n", encoding="utf-8"
    )
