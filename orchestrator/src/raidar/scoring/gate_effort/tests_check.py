"""The gate's tests check, read from each gate run's report.

A gate run's report (``tests`` block, python-guardrails@3; a run without one is
``not_applicable``) gives, per step: ``first_run_tests_passed`` and ``first_run_tests_missing``
(the first run's tests check passed / reported ``TEST.missing``), ``tests_passed_at_step_end``,
``tests_run_at_step_end`` and ``coverage_at_step_end`` (the step's last decided run), and
``first_run_coverage`` (owned line coverage, percent, when tests ran). Failing tests and
missed coverage are separate first-run outcomes: ``first_run_tests_failed`` (the first run's
tests failed or the run timed out; ``not_applicable`` when no test ran), ``first_run_coverage_met``
(``not_applicable`` when no coverage was measured) and ``first_run_failed_tests``. They describe
the code, not the enforcement, so a shadow gate measures them too.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.scoring.framework.context import StepInputs
from raidar.scoring.framework.result import AssessStatus
from raidar.scoring.gate_effort.families import TEST_CODE_PREFIX
from raidar.scoring.gate_effort.measured import Measured, same_for
from raidar.scoring.gate_effort.metrics import (
    FIRST_RUN_COVERAGE_MET,
    FIRST_RUN_FAILED_TESTS,
    FIRST_RUN_TESTS_FAILED,
)
from raidar.scoring.gate_effort.records import RECORD_PATH, REPORT_SCHEMA, _inside

_NA = AssessStatus.NOT_APPLICABLE
_INSUFFICIENT = AssessStatus.INSUFFICIENT_EVIDENCE
MISSING_CODE = "TEST.missing"
FAILED_CODE = "TEST.failed"
COVERAGE_CODE = "TEST.coverage-minimum"
NO_TESTS_BLOCK = "the gate run's report has no tests check (a pack without one)"
NO_TEST_RAN = "no test ran in the first gate run"
NO_COVERAGE = "the first gate run measured no coverage (no test ran)"


def read_record(inputs: StepInputs) -> dict[str, Any]:
    """The step's gate record as JSON (empty when there is none)."""
    if inputs.trace_dir is None:
        return {}
    try:
        payload = json.loads((inputs.trace_dir / RECORD_PATH).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_report(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) and payload.get("schema") == REPORT_SCHEMA else None


def _single_report(trace_dir: Path, run: Mapping[str, Any]) -> dict[str, Any] | None:
    """The run's one gate report (None: no report dir, or not exactly one report)."""
    folder = _inside(trace_dir, run.get("report_dir"))
    if folder is None or not folder.is_dir():
        return None
    reports = [found for path in sorted(folder.glob("*.json")) if (found := _load_report(path))]
    return reports[0] if len(reports) == 1 else None


def _test_codes(report: Mapping[str, Any]) -> list[str]:
    """The report's blocking ``TEST.*`` codes."""
    findings = [item for item in report.get("findings") or [] if isinstance(item, dict)]
    blocking = [str(item.get("code")) for item in findings if item.get("tier") == "block"]
    return [code for code in blocking if code.startswith(TEST_CODE_PREFIX)]


def _tests_block(trace_dir: Path, run: Mapping[str, Any]) -> dict[str, Any] | None:
    """The tests block of the run's single gate report (None: no report or no block)."""
    report = _single_report(trace_dir, run)
    block = report.get("tests") if report is not None else None
    if report is None or not isinstance(block, dict):
        return None
    return {**block, "blocking_codes": _test_codes(report)}


@dataclass(frozen=True)
class _RunTests:
    decided: bool
    block: dict[str, Any] | None


def _run_tests(trace_dir: Path | None, record: Mapping[str, Any]) -> list[_RunTests]:
    runs = record.get("gate_runs") or []
    return [
        _RunTests(
            decided=isinstance(run, dict) and not run.get("error"),
            block=_tests_block(trace_dir, run) if trace_dir and isinstance(run, dict) else None,
        )
        for run in runs
    ]


def _tests_of(run: _RunTests | None, what: str) -> tuple[Measured, Measured, Measured]:
    """(tests check passed, TEST.missing, coverage) of one run, or why there is none."""
    if run is None or not run.decided:
        gap = Measured.gap(_INSUFFICIENT, f"no decided gate run for the {what}")
        return gap, gap, gap
    if run.block is None:
        gap = Measured.gap(_NA, NO_TESTS_BLOCK)
        return gap, gap, gap
    codes = run.block.get("blocking_codes") or []
    coverage = run.block.get("coverage_percent")
    measured_coverage = (
        Measured(float(coverage))
        if isinstance(coverage, int | float)
        else Measured.gap(_NA, "no test ran, so no coverage was measured")
    )
    return (
        Measured(0.0 if codes else 1.0),
        Measured(1.0 if MISSING_CODE in codes else 0.0),
        measured_coverage,
    )


def _outcomes(runs: list[_RunTests]) -> dict[str, Measured]:
    first = runs[0] if runs else None
    last = next((run for run in reversed(runs) if run.decided), None)
    passed, missing, first_coverage = _tests_of(first, "first run")
    end_passed, _, end_coverage = _tests_of(last, "step end")
    ran = (
        Measured(float(last.block.get("tests_ran") or 0))
        if last is not None and last.block is not None
        else end_passed
    )
    return {
        "first_run_tests_passed": passed,
        "first_run_tests_missing": missing,
        "first_run_coverage": first_coverage,
        "tests_passed_at_step_end": end_passed,
        "tests_run_at_step_end": ran,
        "coverage_at_step_end": end_coverage,
    }


def _failed_tests(block: Mapping[str, Any], codes: list[str]) -> dict[str, Measured]:
    """Whether the first run's own tests failed, and how many (not applicable: none ran)."""
    failed = FAILED_CODE in codes
    if not (int(block.get("tests_ran") or 0) or failed):
        gap = Measured.gap(_NA, NO_TEST_RAN)
        return {FIRST_RUN_TESTS_FAILED: gap, FIRST_RUN_FAILED_TESTS: gap}
    return {
        FIRST_RUN_TESTS_FAILED: Measured(1.0 if failed else 0.0),
        FIRST_RUN_FAILED_TESTS: Measured(float(int(block.get("failed") or 0))),
    }


def _coverage_met(block: Mapping[str, Any], codes: list[str]) -> Measured:
    if not isinstance(block.get("coverage_percent"), int | float):
        return Measured.gap(_NA, NO_COVERAGE)
    return Measured(0.0 if COVERAGE_CODE in codes else 1.0)


def _first_tests(run: _RunTests | None) -> dict[str, Measured]:
    """The first run's tests failed / coverage met / failing tests, or why not measured."""
    names = (FIRST_RUN_TESTS_FAILED, FIRST_RUN_COVERAGE_MET, FIRST_RUN_FAILED_TESTS)
    if run is None or not run.decided:
        return same_for(names, Measured.gap(_INSUFFICIENT, "no decided gate run for the first run"))
    if run.block is None:
        return same_for(names, Measured.gap(_NA, NO_TESTS_BLOCK))
    codes = run.block.get("blocking_codes") or []
    return _failed_tests(run.block, codes) | {
        FIRST_RUN_COVERAGE_MET: _coverage_met(run.block, codes)
    }


def tests_values(trace_dir: Path | None, record: Mapping[str, Any]) -> dict[str, Measured]:
    """The step's tests-check values: the first run's and the step end's."""
    runs = _run_tests(trace_dir, record)
    return _outcomes(runs) | _first_tests(runs[0] if runs else None)
