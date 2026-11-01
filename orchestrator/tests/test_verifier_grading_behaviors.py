"""Oracle grading: SCB groups, strict all-cases, determinism, coverage parity (E1, E2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from raidar.grading.models import (
    CaseGroup,
    CaseResult,
    CaseStatus,
    CorpusEvidence,
    CoverageContext,
    EnvironmentEvidence,
    InfraFailure,
    InvocationEvidence,
    LedgerEntry,
    ProcessEvent,
    RunEvidence,
    VerifierGrade,
)
from raidar.grading.normalise import (
    case_results,
    checkpoint_origin,
    classify,
    ledger_consistency_failure,
    ledger_infra_failure,
    no_graded_cases,
    timing_sensitive,
)
from raidar.grading.parity import check_parity, outcome_differences
from tests.verifier_support import LocalProcessSandbox, make_bundle


@pytest.fixture(name="bundle")
def bundle_fixture(tmp_path: Path):
    return make_bundle(tmp_path / "fixture")


@pytest.fixture(name="sandbox")
def sandbox_fixture(tmp_path: Path) -> LocalProcessSandbox:
    return LocalProcessSandbox(tmp_path / "sandbox")


def _case(case_id: str, group: CaseGroup, status: CaseStatus) -> CaseResult:
    return CaseResult(id=case_id, group=group, checkpoint_origin="checkpoint_1", status=status)


# ----------------------------------------------------------------- rules
def test_prior_checkpoint_file_is_regression_regardless_of_markers() -> None:
    node = "test_checkpoint_1.py::test_x"
    assert classify(node, ["error"], "checkpoint_2") is CaseGroup.REGRESSION
    assert checkpoint_origin(node, "checkpoint_2") == "checkpoint_1"


def test_current_checkpoint_markers_follow_scb_precedence() -> None:
    node = "test_checkpoint_2.py::test_x"
    assert classify(node, ["functionality", "error"], "checkpoint_2") is CaseGroup.ERROR
    assert classify(node, ["functionality", "regression"], "checkpoint_2") is CaseGroup.REGRESSION
    assert classify(node, ["functionality"], "checkpoint_2") is CaseGroup.FUNCTIONALITY
    assert classify(node, [], "checkpoint_2") is CaseGroup.CORE
    assert checkpoint_origin("helpers_test.py::test_y", "checkpoint_2") == "checkpoint_2"


def test_strict_all_cases_ignores_skips_but_needs_a_graded_case() -> None:
    passed = _case("a", CaseGroup.CORE, CaseStatus.PASSED)
    skipped = _case("b", CaseGroup.CORE, CaseStatus.SKIPPED)
    failed = _case("c", CaseGroup.ERROR, CaseStatus.FAILED)
    grade = VerifierGrade(step="s", checkpoint="checkpoint_1", cases=(passed, skipped))
    assert grade.solved
    assert grade.rewards()["strict_pass_rate"] == 1.0

    only_skips = VerifierGrade(step="s", checkpoint="checkpoint_1", cases=(skipped,))
    assert not only_skips.solved

    one_failure = VerifierGrade(step="s", checkpoint="checkpoint_1", cases=(passed, failed))
    assert not one_failure.solved
    assert one_failure.rewards()["strict_pass_rate"] == 0.5
    assert one_failure.rewards()["core_pass_rate"] == 1.0


def test_unavailable_values_are_omitted_from_rewards_never_zeroed() -> None:
    passed = _case("a", CaseGroup.CORE, CaseStatus.PASSED)
    infra = VerifierGrade(
        step="s",
        checkpoint="checkpoint_1",
        cases=(passed,),
        infra_failure=InfraFailure(code="collection_error"),
    )
    assert not infra.solved
    assert infra.rewards() == {"infrastructure_failure": 1.0}
    assert infra.pass_rate() is None

    no_regression = VerifierGrade(step="s", checkpoint="checkpoint_1", cases=(passed,))
    assert no_regression.rewards() == {
        "solved": 1.0,
        "strict_pass_rate": 1.0,
        "core_pass_rate": 1.0,
        "infrastructure_failure": 0.0,
    }


def test_a_run_with_every_case_skipped_has_no_verdict() -> None:
    skipped = _case("b", CaseGroup.CORE, CaseStatus.SKIPPED)
    failure = no_graded_cases([skipped, skipped])
    assert failure is not None
    assert failure.code == "no_graded_cases"
    assert no_graded_cases([skipped, _case("a", CaseGroup.CORE, CaseStatus.FAILED)]) is None


@pytest.mark.parametrize(
    ("ledger", "code"),
    [
        ({"collect_errors": ["test_x.py"], "nodes": []}, "collection_error"),
        ({"collect_errors": [], "nodes": []}, "no_tests_collected"),
        ({"nodes": [{"node_id": "a", "ran": False}], "exit_status": 2}, "incomplete_run"),
        ({"nodes": [{"node_id": "a", "ran": True}], "exit_status": 3}, "pytest_exit_status"),
    ],
)
def test_untrustworthy_runs_are_infra_failures(ledger: dict, code: str) -> None:
    failure = ledger_infra_failure(ledger)
    assert failure is not None
    assert failure.code == code


def test_ordinary_test_failures_are_not_infra_failures() -> None:
    ledger = {"nodes": [{"node_id": "a", "ran": True}], "exit_status": 1}
    assert ledger_infra_failure(ledger) is None


def test_cases_within_twice_their_timeout_are_flagged() -> None:
    ledger: dict[str, Any] = {
        "nodes": [
            {"node_id": "slow", "duration_s": 5.0},
            {"node_id": "fast", "duration_s": 4.9},
        ]
    }
    assert timing_sensitive(ledger, 10.0) == ("slow",)
    assert timing_sensitive(ledger, None) == ()
    ledger["budget_exhausted"] = ["late"]
    assert timing_sensitive(ledger, 10.0) == ("late", "slow")


def test_case_results_are_sorted_and_normalised() -> None:
    ledger = {
        "nodes": [
            {"node_id": "test_checkpoint_2.py::b", "markers": [], "status": "failed"},
            {"node_id": "test_checkpoint_1.py::a", "markers": ["error"], "status": "passed"},
        ]
    }
    cases = case_results(ledger, "checkpoint_2")
    assert [c.id for c in cases] == ["test_checkpoint_1.py::a", "test_checkpoint_2.py::b"]
    assert cases[0].group is CaseGroup.REGRESSION
    assert cases[1] == CaseResult(
        id="test_checkpoint_2.py::b",
        group=CaseGroup.CORE,
        checkpoint_origin="checkpoint_2",
        status=CaseStatus.FAILED,
    )


SHADOW = """
import json, os
from pathlib import Path
Path(os.environ["RAIDAR_ORACLE_LEDGER"]).write_text(json.dumps({"forged": True}))
raise SystemExit(0)
"""


FORGER = """
import json, os, stat, sys
from pathlib import Path

leaked = sorted(key for key in os.environ if key.startswith("RAIDAR_ORACLE_"))
(Path.cwd().parent / "leaked.json").write_text(json.dumps(leaked))
ledger = Path.cwd().parent / "out" / "ledger.json"
ledger.write_text(json.dumps({
    "leaked": leaked,
    "exit_status": 0,
    "collect_errors": [],
    "nodes": [{"node_id": "test_checkpoint_2.py::test_multiplies", "markers": [],
               "status": "passed", "ran": True, "duration_s": 0.0}],
}))
ledger.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
print("wrong answer")
"""


def _consistency_ledger(status: int, node_status: str) -> dict[str, Any]:
    return {
        "exit_status": status,
        "nodes": [{"node_id": "test_checkpoint_1.py::a", "status": node_status, "ran": True}],
    }


@pytest.mark.parametrize(
    ("ledger", "exit_code", "files", "code"),
    [
        (_consistency_ledger(0, "passed"), 1, ["test_checkpoint_1.py"], "ledger_exit_mismatch"),
        (_consistency_ledger(0, "failed"), 0, ["test_checkpoint_1.py"], "ledger_exit_mismatch"),
        (_consistency_ledger(1, "passed"), 1, ["test_checkpoint_1.py"], "ledger_exit_mismatch"),
        (
            _consistency_ledger(0, "passed"),
            0,
            ["test_checkpoint_1.py", "test_checkpoint_2.py"],
            "ledger_corpus_mismatch",
        ),
    ],
)
def test_ledgers_that_disagree_with_the_host_are_infra(
    ledger: dict[str, Any], exit_code: int, files: list[str], code: str
) -> None:
    failure = ledger_consistency_failure(ledger, exit_code, files)
    assert failure is not None
    assert failure.code == code


def test_consistent_ledgers_pass_the_host_checks() -> None:
    assert (
        ledger_consistency_failure(_consistency_ledger(1, "failed"), 1, ["test_checkpoint_1.py"])
        is None
    )
    assert (
        ledger_consistency_failure(_consistency_ledger(0, "passed"), 0, ["test_checkpoint_1.py"])
        is None
    )


SLOW_TESTS = """
import time


def test_first():
    time.sleep(2)


def test_second():
    pass
"""


# ------------------------------------------------------------ parity rules
def _run(role: str, **changes: object) -> RunEvidence:
    base = RunEvidence(
        role=role,
        environment=EnvironmentEvidence(
            grader_environment_id="img",
            grader_tools_sha256="tools",
            grader_interpreter_sha256="py",
            submission_interpreter_sha256="py",
            platform_sha256="plat",
            plugin_sha256="plug",
        ),
        corpus=CorpusEvidence(corpus_sha256="corpus", selected_tests=("test_checkpoint_1.py",)),
        invocation=InvocationEvidence(argv=("-p", "x"), cwd="/app"),
        ledger=(LedgerEntry(node_id="t::a", phase="call", outcome="passed"),),
        process_events=(ProcessEvent(node_id="t::a", phase="call", event="subprocess.Popen"),),
    )
    return base.model_copy(update=changes)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        (
            {"environment": _run("coverage").environment.model_copy(update={"plugin_sha256": "x"})},
            "environment_mismatch",
        ),
        ({"corpus": CorpusEvidence(corpus_sha256="other", selected_tests=())}, "corpus_mismatch"),
        ({"invocation": InvocationEvidence(argv=("-p", "y"), cwd="/app")}, "invocation_mismatch"),
        (
            {"ledger": (LedgerEntry(node_id="t::a", phase="call", outcome="failed"),)},
            "ledger_mismatch",
        ),
        ({"process_events": ()}, "process_ledger_mismatch"),
        (
            {"coverage": (CoverageContext(node_id="t::zzz", path="a.py", executed_lines=(1,)),)},
            "coverage_outside_outcomes",
        ),
        ({"invalid_coverage_paths": 2}, "invalid_coverage_paths"),
    ],
)
def test_any_identity_or_ledger_difference_makes_coverage_ineligible(
    changes: dict, reason: str
) -> None:
    cases = [_case("t::a", CaseGroup.CORE, CaseStatus.PASSED)]
    assert check_parity(_run("grading"), _run("coverage"), cases, cases).eligible
    verdict = check_parity(_run("grading"), _run("coverage", **changes), cases, cases)
    assert not verdict.eligible
    assert reason in verdict.reasons


def test_coverage_prefix_is_not_part_of_the_invocation_identity() -> None:
    cases = [_case("t::a", CaseGroup.CORE, CaseStatus.PASSED)]
    measured = _run("coverage", measurement_prefix=("-m", "coverage", "run"))
    assert check_parity(_run("grading"), measured, cases, cases).eligible


def test_outcome_differences_name_each_diverging_case() -> None:
    left = [
        _case("a", CaseGroup.CORE, CaseStatus.PASSED),
        _case("b", CaseGroup.CORE, CaseStatus.PASSED),
    ]
    right = [
        _case("b", CaseGroup.CORE, CaseStatus.FAILED),
        _case("c", CaseGroup.CORE, CaseStatus.PASSED),
    ]
    assert outcome_differences(left, right) == [
        "a: only in grading",
        "c: only in coverage",
        "b: grading=passed coverage=failed",
    ]
