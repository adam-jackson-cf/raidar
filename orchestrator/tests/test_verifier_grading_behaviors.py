"""Oracle grading: SCB groups, strict all-cases, determinism, coverage parity (E1, E2)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest
from harbor.models.trial.paths import TrialPaths

from raidar.contract.wheels import DependencyInfraError
from raidar.grading import grader as grader_module
from raidar.grading.grader import (
    BUDGET_MARGIN_S,
    _coverage_contexts,
    _environment,
    _sandbox_infra,
    _sha256_hex,
    attribute_to_submission,
    grade_step,
    grading_budget,
    parity_verdict,
)
from raidar.grading.leak import LeakReport
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
from raidar.grading.outcomes import RunOutcome, StepGrade
from raidar.grading.parity import check_parity, fork_regression_run_evidence, outcome_differences
from raidar.grading.records import (
    GraderRef,
    SnapshotRef,
    errata_for,
    fork_payload,
    parse_errata,
    parse_solvable,
)
from raidar.grading.sandbox import CONTAINER_LAYOUT, SandboxResult, SandboxRun
from raidar.grading.submission_deps import SubmissionDependencies
from raidar.grading.verifier import RaidarVerifier
from tests.verifier_support import FakeAgentEnvironment, make_bundle, step_spec


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


# ------------------------------------------------------- grader decisions
def _outcome(
    infra: InfraFailure | None = None,
    *,
    cases: tuple[CaseResult, ...] = (),
    evidence: RunEvidence | None = None,
    dependencies: object | None = None,
) -> RunOutcome:
    return RunOutcome("grading", cases, infra, evidence, (), 1.5, cast(Any, dependencies))


def test_sandbox_outcomes_classify_success_timeout_nonzero_and_missing_ledger(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.json"
    ledger.write_text("{}")
    assert _sandbox_infra(SandboxResult(0, False, "ok"), ledger) is None
    assert _sandbox_infra(SandboxResult(1, False, "pytest failed"), ledger) is None

    timed_out = _sandbox_infra(SandboxResult(None, True, ""), ledger)
    assert timed_out is not None
    assert timed_out.code == "sandbox_timeout"

    missing = _sandbox_infra(SandboxResult(137, False, "x" * 5000 + "killed"), tmp_path / "none")
    assert missing is not None
    assert missing.code == "sandbox_error"
    assert missing.detail.startswith("exit=137: ")
    assert missing.detail.endswith("killed")
    assert len(missing.detail) < 2100  # the log tail is bounded


@pytest.mark.parametrize(
    ("timeout_s", "case_timeout", "budget"),
    [(900.0, 30.0, 810.0), (900.0, None, 840.0), (90.0, 30.0, None), (60.0, None, None)],
)
def test_grading_budget_leaves_room_for_the_last_case(
    timeout_s: float, case_timeout: float | None, budget: float | None
) -> None:
    step = step_spec("checkpoint_1", ["test_checkpoint_1.py"]).model_copy(
        update={"timeout_s": case_timeout}
    )
    assert grading_budget(step, timeout_s) == budget


def test_coverage_rows_merge_into_sorted_per_test_line_sets() -> None:
    rows = [
        {"node_id": "t::b", "path": "a.py", "executed_line": 3},
        {"node_id": "t::a", "path": "b.py", "executed_line": "2"},
        {"node_id": "t::a", "path": "a.py", "executed_line": 9},
        {"node_id": "t::a", "path": "a.py", "executed_line": 1},
        {"node_id": "t::a", "path": "a.py", "executed_line": 9},
    ]
    assert _coverage_contexts(rows) == (
        CoverageContext(node_id="t::a", path="a.py", executed_lines=(1, 9)),
        CoverageContext(node_id="t::a", path="b.py", executed_lines=(2,)),
        CoverageContext(node_id="t::b", path="a.py", executed_lines=(3,)),
    )
    assert _coverage_contexts([]) == ()


def test_environment_evidence_hashes_the_probe_and_defaults_missing_parts() -> None:
    probed = _environment(
        {
            "environment": {
                "grader_tools": ["pytest", "coverage"],
                "grader_python_sha256": "g",
                "submission_python_sha256": "s",
                "platform": {"b": 1, "a": 2},
            }
        },
        "img",
    )
    assert probed.grader_environment_id == "img"
    assert (probed.grader_interpreter_sha256, probed.submission_interpreter_sha256) == ("g", "s")
    assert probed.grader_tools_sha256 == _sha256_hex("pytest\ncoverage")
    assert probed.platform_sha256 == _sha256_hex('{"a": 2, "b": 1}')
    bare = _environment({}, "img")
    assert bare.grader_interpreter_sha256 == bare.submission_interpreter_sha256 == ""
    assert bare.grader_tools_sha256 == _sha256_hex("")
    assert bare.plugin_sha256 == probed.plugin_sha256


def test_parity_verdict_is_ineligible_when_either_run_has_no_evidence() -> None:
    evidence = _run("grading")
    case = _case("t::a", CaseGroup.CORE, CaseStatus.PASSED)
    graded = _outcome(cases=(case,), evidence=evidence)

    crashed = _outcome(InfraFailure(code="sandbox_timeout"))
    verdict, differences = parity_verdict(graded, crashed)
    assert verdict.reasons == ("coverage_run_failed:sandbox_timeout",)
    assert differences == ()

    verdict, _ = parity_verdict(graded, _outcome())
    assert verdict.reasons == ("coverage_run_failed:missing",)

    verdict, _ = parity_verdict(_outcome(), _outcome(cases=(case,), evidence=_run("coverage")))
    assert verdict.reasons == ("grading_run_failed",)

    verdict, differences = parity_verdict(
        graded, _outcome(cases=(case,), evidence=_run("coverage"))
    )
    assert verdict.eligible
    assert differences == ()
    failed = _case("t::a", CaseGroup.CORE, CaseStatus.FAILED)
    verdict, differences = parity_verdict(
        graded, _outcome(cases=(failed,), evidence=_run("coverage"))
    )
    assert not verdict.eligible
    assert differences == ("t::a: grading=passed coverage=failed",)


def test_attributing_a_crash_to_the_submission_fails_every_starter_case() -> None:
    crashed = _outcome(InfraFailure(code="sandbox_timeout"), dependencies="deps")
    starter = _outcome(
        cases=(
            _case("t::a", CaseGroup.CORE, CaseStatus.PASSED),
            _case("t::b", CaseGroup.ERROR, CaseStatus.SKIPPED),
        )
    )
    attributed = attribute_to_submission(crashed, starter)
    assert attributed.infra_failure is None
    assert attributed.evidence is None
    assert [c.status for c in attributed.cases] == [CaseStatus.FAILED, CaseStatus.FAILED]
    assert [c.id for c in attributed.cases] == ["t::a", "t::b"]
    assert (attributed.seconds, attributed.dependencies) == (1.5, "deps")


def test_fork_projection_keeps_graded_nodes_and_hashes_the_environment() -> None:
    evidence = _run(
        "grading",
        ledger=(
            LedgerEntry(node_id="t::a", phase="call", outcome="passed"),
            LedgerEntry(node_id="t::unlisted", phase="call", outcome="passed"),
        ),
        coverage=(CoverageContext(node_id="t::a", path="a.py", executed_lines=(1,)),),
    )
    cases = [
        _case("t::a", CaseGroup.REGRESSION, CaseStatus.PASSED),
        _case("t::b", CaseGroup.CORE, CaseStatus.PASSED),
    ]

    projected = fork_regression_run_evidence(evidence, cases)

    assert set(projected) == {
        "environment",
        "corpus",
        "invocation",
        "outcomes",
        "ledger",
        "coverage",
        "process_events",
    }
    assert projected["environment"] == {
        "environment_id": hashlib.sha256(b"img").hexdigest(),
        "project_sha256": hashlib.sha256(b"img:tools").hexdigest(),
        "lock_sha256": "tools",
        "platform_sha256": "plat",
        "plugin_sha256": "plug",
        "interpreter_sha256": hashlib.sha256(b"pypy").hexdigest(),
    }
    assert projected["invocation"] == {"argv": ["-p", "x"], "cwd": "/app"}
    assert projected["corpus"]["corpus_id"] == "corpus"
    assert [row["node_id"] for row in projected["ledger"]] == ["t::a"]  # only graded nodes
    assert [(r["node_id"], r["group_type"]) for r in projected["outcomes"]] == [
        ("t::a", "Regression")
    ]
    assert projected["coverage"][0]["executed_lines"] == (1,)
    assert len(projected["process_events"]) == 1
    assert json.dumps(projected)  # serialisable as the fork's JSON input


def _step_grade(grading: RunOutcome, coverage: RunOutcome | None) -> StepGrade:
    case = _case("t::a", CaseGroup.CORE, CaseStatus.PASSED)
    grade = VerifierGrade(step="checkpoint_1", checkpoint="checkpoint_1", cases=(case,))
    return StepGrade(grade, grading, coverage, None)


def test_the_fork_handoff_needs_evidence_from_both_runs() -> None:
    case = _case("t::a", CaseGroup.CORE, CaseStatus.PASSED)
    graded = _outcome(cases=(case,), evidence=_run("grading"))
    measured = _outcome(cases=(case,), evidence=_run("coverage"))

    payload = fork_payload(_step_grade(graded, measured))
    assert payload is not None
    assert payload["transition_step"] == "checkpoint_1"
    assert set(payload) == {"transition_step", "baseline", "produced"}

    assert fork_payload(_step_grade(graded, None)) is None
    assert fork_payload(_step_grade(graded, _outcome(cases=(case,)))) is None
    assert fork_payload(_step_grade(_outcome(cases=(case,)), measured)) is None


def test_pinned_errata_and_solvable_arguments_are_optional() -> None:
    assert parse_errata(None) is None
    assert parse_solvable(None) is None
    grade = VerifierGrade(step="s", checkpoint="checkpoint_1", cases=())
    assert errata_for(grade, None) is None


# ------------------------------------------------ grade_step over scripted sandbox runs
NODE = "test_checkpoint_1.py::test_adds"
OTHER = "test_checkpoint_1.py::test_subtracts"


def _ledger(
    statuses: dict[str, str], *, coverage_lines: tuple[int, ...] = (), **changes: Any
) -> dict[str, Any]:
    failing = any(status in ("failed", "error") for status in statuses.values())
    ledger: dict[str, Any] = {
        "exit_status": 1 if failing else 0,
        "collect_errors": [],
        "nodes": [
            {"node_id": node, "markers": [], "status": status, "ran": True, "duration_s": 0.01}
            for node, status in statuses.items()
        ],
        "ledger": [
            {"node_id": node, "phase": "call", "outcome": "failed" if "fail" in status else status}
            for node, status in statuses.items()
        ],
        "coverage": [
            {"node_id": NODE, "phase": "call", "path": "app.py", "executed_line": line}
            for line in coverage_lines
        ],
        "process_events": [],
        "environment": {
            "grader_tools": ["pytest==9"],
            "grader_python_sha256": "grader-py",
            "submission_python_sha256": "submission-py",
            "platform": {"system": "Linux"},
        },
    }
    return ledger | changes


PASSED = {NODE: "passed", OTHER: "passed"}
TIMED_OUT = (SandboxResult(None, True, ""), None)


def _ran(ledger: dict[str, Any] | str) -> tuple[SandboxResult, dict[str, Any] | str]:
    exit_code = ledger["exit_status"] if isinstance(ledger, dict) else 1
    return SandboxResult(exit_code, False, "pytest output"), ledger


class ScriptedSandbox:
    """One scripted (result, ledger) per run; the ledger lands where the plugin would write it."""

    layout = CONTAINER_LAYOUT

    def __init__(self, *script: tuple[SandboxResult, dict[str, Any] | str | None]) -> None:
        self.script = list(script)
        self.runs: list[SandboxRun] = []

    async def environment_id(self) -> str:
        return "img"

    async def run(self, run: SandboxRun) -> SandboxResult:
        self.runs.append(run)
        result, ledger = self.script[len(self.runs) - 1]
        if ledger is not None:
            text = ledger if isinstance(ledger, str) else json.dumps(ledger)
            (run.out_dir / "ledger.json").write_text(text)
        return result


@pytest.fixture(name="dependency_outage")
def seams_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[Exception]:
    """Inject the staging and dependency-provisioning seams; append an error to make it fail."""
    outage: list[Exception] = []
    staged: list[Path] = []

    def stage(snapshot: Path, run_dir: Path) -> tuple[Path, Path]:
        out = tmp_path / "stages" / str(len(staged)) / "out"
        out.mkdir(parents=True)
        staged.append(out)
        return out.parent / "app", out

    async def provision(*args: object, **kwargs: object) -> SubmissionDependencies:
        if outage:
            raise outage[0]
        return SubmissionDependencies()

    monkeypatch.setattr(grader_module, "stage_snapshot", stage)
    monkeypatch.setattr(grader_module, "prepare_dependencies", provision)
    return outage


async def _grade(
    tmp_path: Path, sandbox: ScriptedSandbox, *, coverage: bool = False, timeout_s: float = 900.0
) -> StepGrade:
    bundle = make_bundle(tmp_path / "fixture")
    return await grade_step(
        bundle,
        "checkpoint_1",
        tmp_path / "snapshot",
        cast(Any, sandbox),
        tmp_path / "work",
        coverage=coverage,
        timeout_s=timeout_s,
    )


async def test_a_passing_ledger_solves_the_step_with_normalised_evidence(
    tmp_path: Path, dependency_outage: list[Exception]
) -> None:
    sandbox = ScriptedSandbox(_ran(_ledger(PASSED)))

    result = await _grade(tmp_path, sandbox)

    assert result.grade.solved
    assert result.grade.infra_failure is None
    assert [(c.id, c.status) for c in result.grade.cases] == [
        (NODE, CaseStatus.PASSED),
        (OTHER, CaseStatus.PASSED),
    ]
    assert (result.coverage, result.parity) == (None, None)
    assert result.canary.status == "not_run"
    assert list(result.timings) == ["oracle_run_s"]
    evidence = result.grading.evidence
    assert evidence is not None
    assert evidence.environment.grader_environment_id == "img"
    assert evidence.environment.grader_interpreter_sha256 == "grader-py"
    assert evidence.environment.submission_interpreter_sha256 == "submission-py"
    assert [e.node_id for e in evidence.ledger] == [NODE, OTHER]
    assert evidence.corpus.selected_tests == ("test_checkpoint_1.py",)
    assert evidence.measurement_prefix == ()
    assert len(sandbox.runs) == 1
    assert sandbox.runs[0].env["RAIDAR_ORACLE_COVERAGE"] == "0"


async def test_a_failing_case_fails_the_step_without_being_infrastructure(
    tmp_path: Path, dependency_outage: list[Exception]
) -> None:
    sandbox = ScriptedSandbox(_ran(_ledger({NODE: "passed", OTHER: "failed"})))

    result = await _grade(tmp_path, sandbox)

    assert not result.grade.solved
    assert result.grade.infra_failure is None
    assert result.grade.rewards()["solved"] == 0.0
    assert result.grade.rewards()["infrastructure_failure"] == 0.0


async def test_the_coverage_pass_measures_the_same_run_and_attributes_lines_per_test(
    tmp_path: Path, dependency_outage: list[Exception]
) -> None:
    sandbox = ScriptedSandbox(
        _ran(_ledger(PASSED)), _ran(_ledger(PASSED, coverage_lines=(2, 1, 2)))
    )

    result = await _grade(tmp_path, sandbox, coverage=True)

    assert result.parity is not None
    assert result.parity.eligible, result.parity
    assert result.coverage is not None
    assert result.coverage.evidence is not None
    assert result.coverage.evidence.coverage == (
        CoverageContext(node_id=NODE, path="app.py", executed_lines=(1, 2)),
    )
    assert result.grading.evidence is not None
    assert result.grading.evidence.coverage == ()
    assert result.coverage.evidence.measurement_prefix[:3] == ("-m", "coverage", "run")
    assert result.grading.evidence.invocation == result.coverage.evidence.invocation
    assert [run.env["RAIDAR_ORACLE_COVERAGE"] for run in sandbox.runs] == ["0", "1"]
    assert {"oracle_run_s", "coverage_run_s"} <= set(result.timings)


async def test_a_verdict_flipped_by_instrumentation_makes_coverage_ineligible(
    tmp_path: Path, dependency_outage: list[Exception]
) -> None:
    flipped = _ledger({NODE: "failed", OTHER: "passed"})
    sandbox = ScriptedSandbox(_ran(_ledger(PASSED)), _ran(flipped))

    result = await _grade(tmp_path, sandbox, coverage=True)

    assert result.grade.solved  # the uninstrumented grading run decides the verdict
    assert result.parity is not None
    assert not result.parity.eligible
    assert "outcome_mismatch" in result.parity.reasons
    assert f"{NODE}: grading=passed coverage=failed" in result.parity_differences


async def test_a_failed_coverage_run_never_changes_the_grade(
    tmp_path: Path, dependency_outage: list[Exception]
) -> None:
    sandbox = ScriptedSandbox(_ran(_ledger(PASSED)), TIMED_OUT)

    result = await _grade(tmp_path, sandbox, coverage=True)

    assert result.grade.solved
    assert result.coverage is not None
    assert result.coverage.infra_failure is not None
    assert result.parity is not None
    assert result.parity.reasons == ("coverage_run_failed:sandbox_timeout",)


@pytest.mark.parametrize(
    ("ledger", "code"),
    [
        (_ledger({}, collect_errors=["test_checkpoint_1.py"]), "collection_error"),
        (_ledger({}), "no_tests_collected"),
        (_ledger({NODE: "skipped", OTHER: "skipped"}), "no_graded_cases"),
    ],
)
async def test_a_run_that_cannot_give_a_verdict_is_infrastructure_and_skips_coverage(
    tmp_path: Path, dependency_outage: list[Exception], ledger: dict[str, Any], code: str
) -> None:
    sandbox = ScriptedSandbox(_ran(ledger))

    result = await _grade(tmp_path, sandbox, coverage=True)

    assert result.grade.infra_failure is not None
    assert result.grade.infra_failure.code == code
    assert not result.grade.solved
    assert result.grade.rewards() == {"infrastructure_failure": 1.0}
    assert result.coverage is None
    assert result.parity is not None
    assert result.parity.reasons == (f"grading_infra_failure:{code}",)
    assert result.canary.status == "not_run"  # a corpus fault is never the submission's
    assert len(sandbox.runs) == 1


async def test_a_resolver_outage_is_infrastructure_never_a_verdict_on_the_submission(
    tmp_path: Path, dependency_outage: list[Exception]
) -> None:
    dependency_outage.append(DependencyInfraError("pypi unreachable"))
    sandbox = ScriptedSandbox()

    result = await _grade(tmp_path, sandbox, coverage=True)

    assert result.grade.infra_failure is not None
    assert result.grade.infra_failure.code == "dependency_resolver_unavailable"
    assert result.grade.infra_failure.detail == "pypi unreachable"
    assert sandbox.runs == []  # nothing was graded, so no canary and no coverage pass
    assert result.canary.status == "not_run"
    assert result.parity is not None
    assert result.parity.reasons == ("grading_infra_failure:dependency_resolver_unavailable",)


NODE_WITHOUT_STATUS = json.dumps({"nodes": [{"node_id": NODE}], "exit_status": 0})
SUBMISSION_CRASHES = [
    pytest.param(TIMED_OUT, "sandbox_timeout", id="timeout"),
    pytest.param((SandboxResult(137, False, "killed"), None), "sandbox_error", id="no-ledger"),
    pytest.param((SandboxResult(1, False, "x"), "{not json"), "ledger_unreadable", id="truncated"),
    pytest.param(
        (SandboxResult(1, False, "x"), NODE_WITHOUT_STATUS), "ledger_unreadable", id="no-status"
    ),
    pytest.param(
        (SandboxResult(1, False, "x"), _ledger(PASSED)), "ledger_exit_mismatch", id="exit-mismatch"
    ),
    pytest.param(
        _ran(_ledger(PASSED, nodes=[{"node_id": NODE, "status": "passed", "ran": False}])),
        "incomplete_run",
        id="incomplete",
    ),
    pytest.param(_ran(_ledger(PASSED, exit_status=3)), "pytest_exit_status", id="exit-status"),
]


@pytest.mark.parametrize(("crash", "code"), SUBMISSION_CRASHES)
async def test_a_crash_the_starter_canary_clears_is_a_failed_outcome_not_infrastructure(
    tmp_path: Path,
    dependency_outage: list[Exception],
    crash: tuple[SandboxResult, Any],
    code: str,
) -> None:
    starter = _ran(_ledger({NODE: "failed", OTHER: "passed"}))
    sandbox = ScriptedSandbox(crash, starter)

    result = await _grade(tmp_path, sandbox, coverage=True)

    assert result.canary.status == "passed"
    assert result.canary.trigger == code
    assert result.grade.infra_failure is None
    assert result.grade.rewards()["infrastructure_failure"] == 0.0
    assert not result.grade.solved
    assert {c.status for c in result.grade.cases} == {CaseStatus.FAILED}
    assert result.coverage is None
    assert result.parity is not None
    assert result.parity.reasons == (f"grading_failed_on_submission:{code}",)
    assert len(sandbox.runs) == 2
    assert "canary_run_s" in result.timings


async def test_a_crash_the_starter_canary_reproduces_stays_infrastructure(
    tmp_path: Path, dependency_outage: list[Exception]
) -> None:
    sandbox = ScriptedSandbox(TIMED_OUT, TIMED_OUT)

    result = await _grade(tmp_path, sandbox, coverage=True)

    assert result.canary.status == "failed"
    assert result.canary.trigger == "sandbox_timeout"
    assert result.canary.detail.startswith("sandbox_timeout")
    assert result.grade.infra_failure is not None
    assert result.grade.infra_failure.code == "sandbox_timeout"
    assert result.parity is not None
    assert result.parity.reasons == ("grading_infra_failure:sandbox_timeout",)


async def test_the_sandbox_error_names_the_exit_code_and_the_log_tail(
    tmp_path: Path, dependency_outage: list[Exception]
) -> None:
    sandbox = ScriptedSandbox((SandboxResult(137, False, "oom-killed"), None), TIMED_OUT)

    result = await _grade(tmp_path, sandbox)

    assert result.grade.infra_failure is not None
    assert result.grade.infra_failure.code == "sandbox_error"
    assert result.grade.infra_failure.detail == "exit=137: oom-killed"


async def test_cases_left_when_the_budget_runs_out_fail_and_are_timing_sensitive(
    tmp_path: Path, dependency_outage: list[Exception]
) -> None:
    late = _ledger(
        {NODE: "passed", OTHER: "error"},
        budget_exhausted=[OTHER],
    )
    sandbox = ScriptedSandbox(_ran(late))

    result = await _grade(tmp_path, sandbox, timeout_s=BUDGET_MARGIN_S + 1.0)

    assert result.grade.infra_failure is None
    assert not result.grade.solved
    assert result.grading.timing_sensitive == (OTHER,)
    assert sandbox.runs[0].env["RAIDAR_ORACLE_BUDGET_S"] == "1"
    assert sandbox.runs[0].timeout_s == BUDGET_MARGIN_S + 1.0


class _FastVerifier(RaidarVerifier):
    """The production ``verify()`` with the grader, snapshot and pre-agent scan scripted."""

    sandbox: ScriptedSandbox
    pre_agent: LeakReport | None

    async def prepare_sandbox(self) -> tuple[Any, GraderRef]:
        ref = GraderRef(image="local", environment_id="img", image_built=False, timeout_s=900.0)
        return self.sandbox, ref

    def _pre_agent_leak(self, step: str, record_dir: Path) -> LeakReport | None:
        return self.pre_agent

    async def _snapshot(self, work: Path, record_dir: Path) -> tuple[Path, SnapshotRef]:
        ref = SnapshotRef(requested="artifact", used="artifact", digest="d", stored_path="app")
        return work / "snapshot", ref


@pytest.mark.parametrize(
    ("verify_leaks", "pre_agent_clean", "leaked"),
    [
        (False, None, False),
        (False, True, False),
        (True, None, True),
        (True, True, True),
        (False, False, True),  # only the pre-agent scan can see a /tests copy baked into the image
        (True, False, True),
    ],
)
async def test_a_step_leaks_when_either_scan_found_the_hidden_tests(
    tmp_path: Path,
    dependency_outage: list[Exception],
    verify_leaks: bool,
    pre_agent_clean: bool | None,
    leaked: bool,
) -> None:
    bundle = make_bundle(tmp_path / "fixture")
    digest = next(iter(bundle.hidden_file_hashes()))
    stdout = f"{digest}  /app/stolen.py\n" if verify_leaks else ""
    env = FakeAgentEnvironment(leak_stdout=stdout + "RAIDAR_LEAK_SCAN_DONE\n")
    verifier = _FastVerifier(
        oracle_bundle=str(bundle.root),
        docker_context="test-ctx",
        task=cast(Any, None),
        trial_paths=TrialPaths(trial_dir=tmp_path / "trial"),
        environment=cast(Any, env),
        step_name="checkpoint_1",
        pinned={"grader": {}},
        coverage=False,
    )
    verifier.sandbox = ScriptedSandbox(_ran(_ledger(PASSED)))
    verifier.pre_agent = None if pre_agent_clean is None else LeakReport(clean=pre_agent_clean)

    result = await verifier.verify()

    assert result.rewards is not None
    assert (result.rewards["oracle_leak"] == 1.0) is leaked
    assert ("solved" in result.rewards) is not leaked  # a leaked step reports no verdict at all
    assert result.rewards["infrastructure_failure"] == 0.0
