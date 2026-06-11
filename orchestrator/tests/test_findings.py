"""Behavior tests for deterministic review findings projection."""

from datetime import UTC, datetime

from raidar.findings import (
    experiment_findings,
    run_findings,
    run_findings_artifact,
)
from raidar.schemas.events import GateEvent
from raidar.schemas.scorecard import (
    EvalConfig,
    EvalRun,
    FunctionalScore,
    GateCheck,
    MetricScore,
    RequirementsCoverageScore,
    Scorecard,
)


def _eval_config() -> EvalConfig:
    return EvalConfig(
        model="openai/gpt-5.5",
        harness="codex-cli",
        scenario_name="bugfix-ledger-balance",
        scenario_revision="v001",
        starter_root="starter",
        evaluation_profile="scorers:bugfix@1:0.88+requirements@1:0.10+resource-efficiency@1:0.02",
    )


def _scorecard(run_id: str, **overrides) -> Scorecard:
    payload = {
        "run_id": run_id,
        "scenario_name": "bugfix-ledger-balance",
        "scenario_revision": "v001",
        "harness": "codex-cli",
        "model": "openai/gpt-5.5",
        "starter_root": "starter",
        "duration_sec": 120.0,
        "metadata": {"process": {}},
    }
    payload.update(overrides)
    return Scorecard(**payload)


def _run(run_id: str, scorecard: Scorecard, *, duration: float = 120.0, gates=()) -> EvalRun:
    return EvalRun(
        id=run_id,
        timestamp=datetime.now(UTC).isoformat(),
        config=_eval_config(),
        duration_sec=duration,
        terminated_early=False,
        scores=scorecard,
        gate_history=list(gates),
    )


def _gate_event(name: str, exit_code: int, *, category: str | None = None) -> GateEvent:
    return GateEvent(
        timestamp=datetime.now(UTC).isoformat(),
        gate_name=name,
        command=f"bun run {name}",
        exit_code=exit_code,
        stdout="",
        stderr="boom" if exit_code else "",
        failure_category=category,
    )


def _by_category(findings):
    grouped: dict[str, list] = {}
    for finding in findings:
        grouped.setdefault(finding.category, []).append(finding)
    return grouped


def test_failed_gates_project_to_issue_findings_with_evidence():
    run = _run(
        "run-a",
        _scorecard("run-a"),
        gates=[
            _gate_event("lint", 1, category="lint"),
            _gate_event("lint", 1, category="lint"),
            _gate_event("test", 0),
        ],
    )

    grouped = _by_category(run_findings(run))

    assert len(grouped["failed-gate"]) == 1
    finding = grouped["failed-gate"][0]
    assert finding.kind == "issue"
    assert "lint" in finding.title
    assert "2 time(s)" in finding.title
    assert finding.evidence[0].source == "gate_history"
    assert finding.evidence[0].reference == "lint"


def test_missing_required_commands_and_requirements_gaps_are_issues():
    scorecard = _scorecard(
        "run-b",
        metadata={
            "process": {
                "missing_required_verification_commands": 1,
                "required_verification_first_pass": {
                    "bun run test": "missing",
                    "bun run lint": "success",
                },
            }
        },
        requirements_coverage=RequirementsCoverageScore(
            total_requirements=5,
            satisfied_requirements=3,
            missing_requirement_ids=["req-a", "req-b"],
            requirement_test_evidence_gaps={"req-c": ["query_role:button"]},
        ),
    )

    grouped = _by_category(run_findings(_run("run-b", scorecard)))

    missing_command = grouped["missing-required-command"][0]
    assert missing_command.kind == "issue"
    assert missing_command.evidence[0].reference == "bun run test"
    requirement_findings = grouped["requirements-gap"]
    assert len(requirement_findings) == 2
    assert any("2 requirement(s) not satisfied" in item.title for item in requirement_findings)
    assert any("missing required test evidence" in item.title for item in requirement_findings)


def test_retained_evidence_records_project_to_good_and_issue_findings():
    scorecard = _scorecard(
        "run-c",
        metadata={
            "process": {},
            "evidence": {
                "retained_files": [
                    {
                        "path": "evidence/defect-evidence.json",
                        "status": "ingested",
                        "keys": ["reproduction_note"],
                    },
                    {"path": "evidence/other.json", "status": "missing", "keys": []},
                ]
            },
        },
    )

    grouped = _by_category(run_findings(_run("run-c", scorecard)))

    assert grouped["retained-evidence"][0].kind == "good"
    missing = grouped["missing-artifact"][0]
    assert missing.kind == "issue"
    assert "evidence/other.json" in missing.title


def test_metric_findings_cover_judge_cap_and_missing_pattern_paths():
    scorecard = _scorecard(
        "run-d",
        metric_scores=[
            MetricScore(
                metric_id="requirements-adherence",
                score=0.5,
                passed=False,
                judge_output={"verdict": "partial"},
                evidence="judge verdict partial",
            ),
            MetricScore(
                metric_id="defect-resolution",
                score=0.4,
                passed=False,
                evidence="direct: functional execution capped by defect-linked requirement checks",
            ),
            MetricScore(
                metric_id="artifact-checks",
                score=0.0,
                passed=False,
                missing_patterns=["src/lib/ledger.ts"],
                evidence="missing artifacts",
            ),
            MetricScore(metric_id="functional", score=1.0, passed=True),
        ],
    )

    grouped = _by_category(run_findings(_run("run-d", scorecard)))

    assert grouped["judge-review"][0].kind == "issue"
    assert grouped["deterministic-cap"][0].evidence[0].reference == "defect-resolution"
    assert grouped["missing-artifact"][0].detail == "src/lib/ledger.ts"


def test_validity_workflow_and_strength_findings():
    scorecard = _scorecard(
        "run-e",
        metadata={
            "process": {
                "git_commit_verification_bypass_commands": ["git commit --no-verify"],
            }
        },
        functional=FunctionalScore(
            passed=True,
            tests_passed=9,
            tests_total=9,
            build_succeeded=True,
            gates_passed=4,
            gates_total=4,
        ),
        requirements_coverage=RequirementsCoverageScore(
            total_requirements=5,
            satisfied_requirements=5,
        ),
    )
    scorecard.execution_validity.checks = [
        GateCheck(name="completion_claim_integrity", passed=False, evidence="claimed early"),
    ]

    findings = run_findings(_run("run-e", scorecard))
    grouped = _by_category(findings)

    assert grouped["completion-claim"][0].kind == "issue"
    assert grouped["workflow-anomaly"][0].kind == "issue"
    assert {finding.category for finding in findings if finding.kind == "good"} == {
        "clean-verification",
        "requirements-satisfied",
    }
    assert len({finding.id for finding in findings}) == len(findings)
    assert all(finding.id.startswith("run-run-e-finding-") for finding in findings)


def test_run_findings_artifact_carries_run_identity():
    artifact = run_findings_artifact(_run("run-f", _scorecard("run-f")))

    assert artifact.run_id == "run-f"
    assert artifact.scenario_name == "bugfix-ledger-balance"
    assert artifact.schema_version == 1


def test_experiment_findings_cover_unscored_variance_outliers_and_sample():
    runs = [
        _run("run-1", _scorecard("run-1"), duration=100.0),
        _run("run-2", _scorecard("run-2"), duration=110.0),
        _run("run-3", _scorecard("run-3"), duration=105.0),
        _run("run-4", _scorecard("run-4"), duration=400.0),
        _run(
            "run-5",
            _scorecard("run-5", unscored=True, unscored_reasons=["provider_rate_limit"]),
            duration=10.0,
        ),
    ]
    summary = {
        "aggregate": {"composite_score": {"stddev": 0.25}},
        "sample": {"minimum_met": False, "achieved_scored_runs": 4, "minimum_scored_runs": 5},
        "rerun": {"target_met": False, "achieved_scored_runs": 4, "target_scored_runs": 5},
    }

    findings = experiment_findings(runs, summary)
    grouped = _by_category(findings)

    assert grouped["unscored-run"][0].title == "Run run-5 is unscored"
    assert grouped["repeat-variance"][0].kind == "note"
    assert "run-4" in grouped["resource-outlier"][0].title
    assert grouped["sample-adequacy"][0].kind == "issue"
    assert grouped["rerun-target"][0].kind == "issue"
    assert all(finding.id.startswith("experiment-finding-") for finding in findings)


def test_quiet_run_produces_no_issue_findings():
    findings = run_findings(_run("run-quiet", _scorecard("run-quiet")))

    assert [finding for finding in findings if finding.kind == "issue"] == []
