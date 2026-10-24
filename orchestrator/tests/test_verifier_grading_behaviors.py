"""Oracle grading: SCB groups, strict all-cases, determinism, coverage parity (E1, E2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from raidar.grading.grader import BUDGET_MARGIN_S, grade_step
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
from raidar.grading.parity import check_parity, fork_regression_run_evidence, outcome_differences
from raidar.grading.sandbox import SandboxResult, SandboxRun
from tests.verifier_support import LocalProcessSandbox, make_bundle, write_app


@pytest.fixture
def bundle(tmp_path: Path):
    return make_bundle(tmp_path / "fixture")


@pytest.fixture
def sandbox(tmp_path: Path) -> LocalProcessSandbox:
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


# ------------------------------------------------------- real pytest runs
async def test_reference_solution_solves_and_prior_solution_fails_next_checkpoint(
    tmp_path: Path, bundle, sandbox: LocalProcessSandbox
) -> None:
    v1 = write_app(tmp_path / "v1")
    v2 = write_app(tmp_path / "v2", mul=True)

    first = await grade_step(bundle, "checkpoint_1", v1, sandbox, tmp_path / "w1", coverage=False)
    counts = first.grade.group_counts()
    assert first.grade.solved
    assert counts[CaseGroup.CORE].total == 1
    assert counts[CaseGroup.FUNCTIONALITY].total == 1
    assert counts[CaseGroup.ERROR].total == 1
    assert any(c.status is CaseStatus.SKIPPED for c in first.grade.cases)

    stale = await grade_step(bundle, "checkpoint_2", v1, sandbox, tmp_path / "w2", coverage=False)
    assert not stale.grade.solved
    assert stale.grade.group_counts()[CaseGroup.REGRESSION].passed == 3

    solved = await grade_step(bundle, "checkpoint_2", v2, sandbox, tmp_path / "w3", coverage=False)
    assert solved.grade.solved


SHADOW = """
import json, os
from pathlib import Path
Path(os.environ["RAIDAR_ORACLE_LEDGER"]).write_text(json.dumps({"forged": True}))
raise SystemExit(0)
"""


async def test_submission_modules_cannot_replace_the_grader_tools(
    tmp_path: Path, bundle, sandbox: LocalProcessSandbox
) -> None:
    app = write_app(tmp_path / "app")  # checkpoint_2 is unsolved by this app
    for name in ("pytest.py", "coverage.py", "json.py"):
        (app / name).write_text(SHADOW)

    result = await grade_step(bundle, "checkpoint_2", app, sandbox, tmp_path / "w", coverage=True)

    assert result.grade.infra_failure is None
    assert not result.grade.solved
    assert any(c.id.endswith("test_multiplies") for c in result.grade.cases)
    assert result.parity is not None
    assert result.parity.eligible, result.parity


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


async def test_submission_cannot_forge_the_grading_ledger(
    tmp_path: Path, bundle, sandbox: LocalProcessSandbox
) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.py").write_text(FORGER.lstrip())

    result = await grade_step(bundle, "checkpoint_2", app, sandbox, tmp_path / "w", coverage=False)

    assert not result.grade.solved
    ledger = json.loads((tmp_path / "w" / "grading" / "out" / "ledger.json").read_text())
    assert "leaked" not in ledger  # the plugin's own ledger replaced the forgery
    assert json.loads((sandbox.root / "leaked.json").read_text()) == []
    assert ledger["plugin_id"] == "raidar-oracle-plugin@1"
    assert {c.status for c in result.grade.cases} >= {CaseStatus.FAILED}


@pytest.mark.parametrize(
    "ledger_text",
    ["{not json", '{"nodes": [{"node_id": "t.py::a"}], "exit_status": 0}'],
    ids=["truncated", "node_without_status"],
)
async def test_a_malformed_ledger_is_an_infra_failure_not_a_crash(
    tmp_path: Path, bundle, sandbox: LocalProcessSandbox, ledger_text: str
) -> None:
    class BrokenLedger(LocalProcessSandbox):
        async def run(self, run: SandboxRun) -> SandboxResult:
            result = await super().run(run)
            (run.out_dir / "ledger.json").write_text(ledger_text)
            return result

    app = write_app(tmp_path / "app")
    result = await grade_step(
        bundle, "checkpoint_2", app, BrokenLedger(sandbox.root), tmp_path / "w", coverage=False
    )

    assert result.grade.infra_failure is not None
    assert result.grade.infra_failure.code == "ledger_unreadable"
    assert not result.grade.solved


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


async def test_cases_left_when_the_grading_budget_runs_out_fail_rather_than_infra(
    tmp_path: Path, sandbox: LocalProcessSandbox
) -> None:
    fixture = make_bundle(tmp_path / "slow")
    (fixture.tests_dir / "test_checkpoint_1.py").write_text(SLOW_TESTS.lstrip())
    app = write_app(tmp_path / "app")
    # Budget = hard timeout - per-test timeout (none) - margin: 1 s of case starts.
    timeout_s = BUDGET_MARGIN_S + 1.0

    result = await grade_step(
        fixture, "checkpoint_1", app, sandbox, tmp_path / "w", coverage=False, timeout_s=timeout_s
    )

    assert result.grade.infra_failure is None
    assert not result.grade.solved
    statuses = {c.id.split("::")[1]: c.status for c in result.grade.cases}
    assert statuses["test_second"] is CaseStatus.ERROR
    assert "test_checkpoint_1.py::test_second" in result.grading.timing_sensitive
    assert result.grade.rewards()["infrastructure_failure"] == 0.0


async def test_regrading_the_same_snapshot_gives_identical_results(
    tmp_path: Path, bundle, sandbox: LocalProcessSandbox
) -> None:
    app = write_app(tmp_path / "app")
    first = await grade_step(bundle, "checkpoint_2", app, sandbox, tmp_path / "a", coverage=True)
    second = await grade_step(bundle, "checkpoint_2", app, sandbox, tmp_path / "b", coverage=True)
    assert first.grade == second.grade
    assert first.parity == second.parity
    assert first.grading.evidence == second.grading.evidence


async def test_grading_never_modifies_the_snapshot(
    tmp_path: Path, bundle, sandbox: LocalProcessSandbox
) -> None:
    app = write_app(tmp_path / "app", mul=True)
    before = (app / "app.py").read_bytes()
    await grade_step(bundle, "checkpoint_2", app, sandbox, tmp_path / "w", coverage=True)
    assert sorted(p.name for p in app.iterdir()) == ["app.py"]
    assert (app / "app.py").read_bytes() == before


async def test_coverage_pass_attributes_lines_per_test_with_parity(
    tmp_path: Path, bundle, sandbox: LocalProcessSandbox
) -> None:
    app = write_app(tmp_path / "app", mul=True)
    result = await grade_step(bundle, "checkpoint_2", app, sandbox, tmp_path / "w", coverage=True)

    assert result.parity is not None
    assert result.parity.eligible, result.parity
    assert result.coverage is not None
    assert result.coverage.evidence is not None
    contexts = {c.node_id: c for c in result.coverage.evidence.coverage}
    assert "test_checkpoint_2.py::test_multiplies" in contexts
    assert contexts["test_checkpoint_2.py::test_multiplies"].path == "app.py"
    assert result.grading.evidence is not None
    assert result.grading.evidence.coverage == ()
    assert result.coverage.evidence.measurement_prefix[:3] == ("-m", "coverage", "run")
    assert result.grading.evidence.invocation == result.coverage.evidence.invocation


async def test_verdict_flipped_by_instrumentation_makes_coverage_ineligible(
    tmp_path: Path, bundle, sandbox: LocalProcessSandbox
) -> None:
    app = write_app(tmp_path / "app", mul=True, coverage_sensitive=True)
    result = await grade_step(bundle, "checkpoint_2", app, sandbox, tmp_path / "w", coverage=True)

    assert result.grade.solved  # the uninstrumented grading run decides the verdict
    assert result.parity is not None
    assert not result.parity.eligible
    assert "outcome_mismatch" in result.parity.reasons
    assert any("test_adds" in note for note in result.parity_differences)


async def test_grading_infra_failure_skips_coverage_and_is_ineligible(
    tmp_path: Path, bundle, sandbox: LocalProcessSandbox
) -> None:
    (bundle.tests_dir / "conftest.py").write_text("raise RuntimeError('broken corpus')\n")
    app = write_app(tmp_path / "app")
    result = await grade_step(bundle, "checkpoint_1", app, sandbox, tmp_path / "w", coverage=True)

    assert result.grade.infra_failure is not None
    assert not result.grade.solved
    assert result.coverage is None
    assert result.parity is not None
    assert not result.parity.eligible
    assert result.parity.reasons[0].startswith("grading_infra_failure:")


async def test_fork_projection_carries_the_g_kernel_input_shape(
    tmp_path: Path, bundle, sandbox: LocalProcessSandbox
) -> None:
    app = write_app(tmp_path / "app", mul=True)
    result = await grade_step(bundle, "checkpoint_2", app, sandbox, tmp_path / "w", coverage=True)
    assert result.grading.evidence is not None
    assert result.coverage is not None
    assert result.coverage.evidence is not None

    baseline = fork_regression_run_evidence(result.grading.evidence, result.grading.cases)
    produced = fork_regression_run_evidence(result.coverage.evidence, result.coverage.cases)

    assert set(baseline) == {
        "environment",
        "corpus",
        "invocation",
        "outcomes",
        "ledger",
        "coverage",
        "process_events",
    }
    for key in ("environment", "corpus", "invocation"):
        assert baseline[key] == produced[key]
    assert all(len(value) == 64 for value in baseline["environment"].values())
    groups = {row["node_id"]: row["group_type"] for row in baseline["outcomes"]}
    assert groups["test_checkpoint_1.py::test_adds"] == "Regression"
    assert groups["test_checkpoint_2.py::test_multiplies"] == "Core"
    assert json.dumps(produced)  # serialisable as the fork's JSON input


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
