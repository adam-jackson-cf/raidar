import json
from types import SimpleNamespace

from raidar.runtime import scorecard
from raidar.runtime.models import EvaluationOutputs, ProcessMetrics
from raidar.schemas.events import GateEvent, TraceEvent
from raidar.schemas.scenario import (
    DeterministicCheck,
    QueryRoleTestEvidence,
    QueryTextTestEvidence,
    RequirementSpec,
)
from raidar.schemas.scorecard import (
    CoverageScore,
    ExecutionValidityScore,
    FunctionalScore,
    GateCheck,
    PerformanceGatesScore,
    RequirementsCoverageScore,
    VerificationStabilityScore,
)
from raidar.scorers.code_task import typescript_evidence


def test_coverage_uses_lowest_summary_metric_and_ignores_invalid_sources(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    summary = workspace / "coverage" / "coverage-summary.json"
    summary.parent.mkdir(parents=True)
    summary.write_text(
        json.dumps(
            {
                "total": {
                    "lines": {"pct": 96},
                    "statements": {"pct": 94},
                    "functions": {"pct": 98},
                    "branches": {"pct": 91},
                }
            }
        ),
        encoding="utf-8",
    )

    result = typescript_evidence.evaluate_typescript_coverage(workspace, [], threshold=0.9)

    assert result.measured == 0.91
    assert result.source == str(summary)
    assert result.passed is True

    summary.write_text("{not-json", encoding="utf-8")
    assert (
        typescript_evidence.evaluate_typescript_coverage(workspace, [], threshold=None).measured
        is None
    )

    summary.write_text(json.dumps({"total": []}), encoding="utf-8")
    assert (
        typescript_evidence.evaluate_typescript_coverage(workspace, [], threshold=None).measured
        is None
    )

    summary.write_text(json.dumps({"total": {"lines": {}}}), encoding="utf-8")
    assert (
        typescript_evidence.evaluate_typescript_coverage(workspace, [], threshold=0.1).passed
        is False
    )


def test_coverage_falls_back_to_gate_history_table_and_named_metrics(tmp_path) -> None:
    gate_history = [
        GateEvent(
            timestamp="2026-01-01T00:00:00Z",
            gate_name="unit-tests",
            command="pytest",
            exit_code=0,
            stdout="Lines : 99%\nFunctions : 88%",
            stderr="",
        ),
        GateEvent(
            timestamp="2026-01-01T00:00:01Z",
            gate_name="coverage",
            command="npm run coverage",
            exit_code=0,
            stdout="All files | 95 | 93 | 90 | 97",
            stderr="",
        ),
    ]

    result = typescript_evidence.evaluate_typescript_coverage(
        tmp_path, gate_history, threshold=0.91
    )

    assert result.measured == 0.9
    assert result.source == "gate:coverage"
    assert result.passed is False
    assert typescript_evidence.parse_istanbul_coverage_percent("no coverage here") is None
    assert typescript_evidence.coverage_from_gate_history(
        [
            GateEvent(
                timestamp="2026-01-01T00:00:02Z",
                gate_name="unit",
                command="pytest",
                exit_code=0,
                stdout="Lines : 77%",
                stderr="",
            )
        ]
    ) == (None, None)


def test_requirements_count_satisfied_and_missing_test_evidence(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    src = workspace / "src"
    src.mkdir(parents=True)
    (src / "component.tsx").write_text("export const label = 'Dashboard';", encoding="utf-8")
    (src / "component.test.tsx").write_text(
        "\n".join(
            [
                "screen.getByRole('heading', { level: 1, name: /Dashboard/ });",
                "screen.getByText(/Dashboard/);",
            ]
        ),
        encoding="utf-8",
    )

    present = RequirementSpec(
        id="R1",
        description="Dashboard heading exists",
        check=DeterministicCheck(
            type="import_present",
            pattern="Dashboard",
            description="Dashboard label present",
        ),
        required_test_evidence=[
            QueryRoleTestEvidence(role="heading", level=1, name="Dashboard"),
            QueryTextTestEvidence(pattern="Dashboard"),
        ],
    )
    missing_evidence = RequirementSpec(
        id="R2",
        description="Button test evidence exists",
        check=DeterministicCheck(
            type="file_exists",
            pattern="src/component.tsx",
            description="component exists",
        ),
        required_test_evidence=[QueryRoleTestEvidence(role="button", min_count=2)],
    )

    result = typescript_evidence.evaluate_typescript_requirements(
        workspace, [present, missing_evidence]
    )

    assert result.total_requirements == 2
    assert result.satisfied_requirements == 2
    assert result.mapped_requirements == 1
    assert result.mapped_satisfied_requirements == 1
    assert result.requirement_gap_ids == ["R2"]
    assert result.requirement_test_evidence_gaps == {"R2": ["query_role:button x2"]}


def test_requirements_report_missing_presence_checks_and_unknown_evidence(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)
    missing = RequirementSpec(
        id="R3",
        description="Missing forbidden pattern",
        check=DeterministicCheck(
            type="import_present",
            pattern="NeverPresent",
            description="missing import",
        ),
    )
    unknown_evidence = SimpleNamespace(model_dump=lambda mode: {"type": "unknown", "min_count": 1})
    no_role = SimpleNamespace(
        model_dump=lambda mode: {"type": "query_role", "role": "", "min_count": 1}
    )
    with_evidence_gap = RequirementSpec(
        id="R4",
        description="Unsupported test evidence",
        check=DeterministicCheck(
            type="file_exists",
            pattern="src",
            description="src exists",
        ),
    )
    object.__setattr__(
        with_evidence_gap,
        "required_test_evidence",
        [unknown_evidence, no_role],
    )

    result = typescript_evidence.evaluate_typescript_requirements(
        workspace, [missing, with_evidence_gap]
    )

    assert result.satisfied_requirements == 1
    assert result.missing_requirement_ids == ["R3"]
    assert result.requirement_gap_ids == ["R4"]
    assert result.requirement_test_evidence_gaps == {"R4": ["unknown", "query_role: x1"]}
    assert (
        typescript_evidence.test_evidence_label(
            {"type": "query_role", "role": "heading", "level": 2, "name": "Settings"}
        )
        == "query_role:heading,level=2,name=Settings x1"
    )
    assert typescript_evidence.test_evidence_label({"type": "query_text", "pattern": "Save"}) == (
        "query_text:Save x1"
    )
    assert (
        typescript_evidence.count_role_query_matches(
            ["screen.getByRole('heading', { level: 1, name: /Settings/ });"],
            {"role": "heading", "level": 2, "name": "Settings"},
        )
        == 0
    )
    assert (
        typescript_evidence.count_role_query_matches(
            ["screen.getByRole('heading', { level: 2, name: /Profile/ });"],
            {"role": "heading", "level": 2, "name": "Settings"},
        )
        == 0
    )
    assert typescript_evidence.count_text_query_matches(["no queries"], {"pattern": "Save"}) == 0
    assert (
        typescript_evidence.count_text_query_matches(["screen.getByText(/Save/);"], {"pattern": ""})
        == 0
    )


def test_terminated_outputs_zero_scored_surfaces_and_preserve_failure_reason() -> None:
    outputs = scorecard.terminated_outputs("agent stopped")

    assert outputs.functional.passed is False
    assert outputs.test_coverage.passed is False
    assert outputs.execution_validity.checks[0].name == "run_completed"
    assert outputs.execution_validity.checks[0].evidence == "agent stopped"
    assert outputs.requirements_coverage.total_requirements == 0


def _outputs(*, gates_passed: int = 1, gates_total: int = 1) -> EvaluationOutputs:
    return EvaluationOutputs(
        functional=FunctionalScore(
            passed=gates_passed == gates_total,
            tests_passed=1,
            tests_total=1,
            build_succeeded=True,
            gates_passed=gates_passed,
            gates_total=gates_total,
        ),
        visual=None,
        verification_stability=VerificationStabilityScore(),
        test_coverage=CoverageScore(),
        requirements_coverage=RequirementsCoverageScore(),
        execution_validity=ExecutionValidityScore(checks=[GateCheck(name="stale", passed=True)]),
        performance_gates=PerformanceGatesScore(),
        metric_scores=[],
        gate_history=[],
    )


def _metrics(
    *,
    bypass_commands: list[str] | None = None,
    required_verification_commands: int = 1,
    executed_required_verification_commands: int = 1,
) -> ProcessMetrics:
    return ProcessMetrics(
        uncached_input_tokens=1,
        output_tokens=2,
        command_count=3,
        failed_command_count=0,
        process_failed_command_count=0,
        verification_rounds=1,
        repeated_verification_failures=0,
        required_verification_commands=required_verification_commands,
        executed_required_verification_commands=executed_required_verification_commands,
        git_commit_verification_bypass_commands=bypass_commands or [],
    )


def test_execution_validity_flags_premature_completion_claims_and_commit_bypass(
    monkeypatch, tmp_path
) -> None:
    events = [
        TraceEvent(
            timestamp="2026-01-01T00:00:00Z",
            event_type="assistant_message",
            data={"content": "All done"},
        )
    ]
    monkeypatch.setattr(scorecard, "_git_commit_count", lambda _workspace: (0, "commit_count=0"))

    validity = scorecard.build_execution_validity_score(
        scorecard.ExecutionValidityInput(
            outputs=_outputs(gates_passed=0, gates_total=1),
            terminated_early=False,
            termination_reason=None,
            process_metrics=_metrics(bypass_commands=["git commit --no-verify"]),
            events=events,
            workspace_path=tmp_path,
            atomic_commits_required=True,
            verification_patterns=["pytest"],
        )
    )

    checks = {check.name: check for check in validity.checks}
    assert checks["completion_claim_integrity"].passed is False
    assert checks["commit_verification_hooks_not_bypassed"].passed is False
    assert checks["atomic_commits_present"].passed is False


def test_execution_validity_handles_required_zero_and_atomic_completion_failure(
    monkeypatch, tmp_path
) -> None:
    events = [
        TraceEvent(
            timestamp="2026-01-01T00:00:00Z",
            event_type="assistant_message",
            data={"content": "Completed"},
        )
    ]
    monkeypatch.setattr(scorecard, "_git_commit_count", lambda _workspace: (0, "commit_count=0"))

    validity = scorecard.build_execution_validity_score(
        scorecard.ExecutionValidityInput(
            outputs=_outputs(),
            terminated_early=False,
            termination_reason=None,
            process_metrics=_metrics(
                required_verification_commands=0,
                executed_required_verification_commands=0,
            ),
            events=events,
            workspace_path=tmp_path,
            atomic_commits_required=True,
            verification_patterns=[],
        )
    )

    checks = {check.name: check for check in validity.checks}
    assert checks["required_verification_commands_executed"].evidence == "required=0"
    assert checks["completion_claim_integrity"].passed is False


def test_git_commit_count_reports_unavailable_error_and_unparseable_output(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        scorecard.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )
    assert scorecard._git_commit_count(tmp_path) == (0, "git not available in run environment.")

    monkeypatch.setattr(
        scorecard.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="fatal"),
    )
    assert scorecard._git_commit_count(tmp_path) == (0, "fatal")

    monkeypatch.setattr(
        scorecard.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="many", stderr=""),
    )
    assert scorecard._git_commit_count(tmp_path) == (0, "Unable to parse git commit count: many")

    monkeypatch.setattr(
        scorecard.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="3\n", stderr=""),
    )
    assert scorecard._git_commit_count(tmp_path) == (3, "commit_count=3")


def test_upsert_gate_check_replaces_existing_check() -> None:
    checks = [GateCheck(name="run_completed", passed=False, evidence="old")]

    scorecard._upsert_gate_check(
        checks,
        GateCheck(name="run_completed", passed=True, evidence="new"),
    )

    assert checks == [GateCheck(name="run_completed", passed=True, evidence="new")]


def test_quality_score_from_scorers_uses_positive_quality_weights_only() -> None:
    assert scorecard._quality_score_from_scorers([]) == 0.0
    assert (
        scorecard._quality_score_from_scorers(
            [
                SimpleNamespace(category="quality", score=0.5, weight=2),
                SimpleNamespace(category="quality", score=1.0, weight=0),
                SimpleNamespace(category="efficiency", score=1.0, weight=10),
                SimpleNamespace(category="quality", score=1.0, weight=1),
            ]
        )
        == 2 / 3
    )
