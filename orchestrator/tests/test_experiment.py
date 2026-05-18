"""Tests for experiment aggregation artifacts."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from raidar.experiment import (
    ExperimentSummaryInput,
    create_experiment_summary,
    experiment_workspace,
    persist_experiment,
)
from raidar.schemas.scorecard import EvalConfig, EvalRun, GateCheck, MetricResult, Scorecard


def _run(
    run_id: str,
    *,
    run_valid: bool,
    duration: float,
    unscored: bool = False,
    artifact_checks_passed: bool | None = None,
) -> EvalRun:
    scorecard = _scorecard(
        run_id,
        run_valid=run_valid,
        duration=duration,
        unscored=unscored,
        artifact_checks_passed=artifact_checks_passed,
    )
    return EvalRun(
        id=run_id,
        timestamp=datetime.now(UTC).isoformat(),
        config=_eval_config(),
        duration_sec=duration,
        terminated_early=False,
        scores=scorecard,
    )


def _eval_config() -> EvalConfig:
    return EvalConfig(
        model="codex/gpt-5.4-mini",
        harness="codex-cli",
        scenario_name="homepage",
        scenario_revision="v001",
        starter_root="starter",
        evaluation_profile=(
            "functional+acceptance+verification-stability+execution-validity+resource-efficiency"
        ),
    )


def _scorecard(
    run_id: str,
    *,
    run_valid: bool,
    duration: float,
    unscored: bool,
    artifact_checks_passed: bool | None,
) -> Scorecard:
    scorecard = Scorecard(
        run_id=run_id,
        scenario_name="homepage",
        scenario_revision="v001",
        harness="codex-cli",
        model="codex/gpt-5.4-mini",
        starter_root="starter",
        duration_sec=duration,
        metadata={
            "run": {
                "canonical_run_dir": f"/tmp/canonical/{run_id}",
                "run_json_path": f"/tmp/canonical/{run_id}/run.json",
            },
            "process": {"uncached_input_tokens": 1000},
        },
        unscored=unscored,
        unscored_reasons=["provider_rate_limit"] if unscored else [],
        metric_results=(
            [
                MetricResult(
                    metric_id="artifact-checks",
                    passed=artifact_checks_passed,
                    matched_count=1 if artifact_checks_passed else 0,
                    missing_patterns=[] if artifact_checks_passed else ["src/components/**/*.tsx"],
                    evidence="test",
                )
            ]
            if artifact_checks_passed is not None
            else []
        ),
    )
    scorecard.execution_validity.checks = [
        GateCheck(name="run_completed", passed=run_valid, evidence=None)
    ]
    scorecard.resource_efficiency.command_count = 1
    scorecard.resource_efficiency.uncached_input_tokens = 10 if run_valid else 250_000
    return scorecard


def _summary_input(
    *,
    runs: list[EvalRun],
    metrics: list[str] | None = None,
    repeats: int = 2,
    repeat_parallel: int = 1,
    started_at: datetime | None = None,
    rerun_unscored_limit: int = 0,
    reruns_used: int = 0,
    unresolved_unscored_count: int = 0,
) -> ExperimentSummaryInput:
    resolved_metrics = metrics or [
        "functional",
        "acceptance",
        "verification-stability",
        "execution-validity",
        "resource-efficiency",
    ]
    return ExperimentSummaryInput(
        scenario_name="Homepage Scenario",
        scenario_revision="v001",
        harness="codex-cli",
        model="codex/gpt-5.4-mini",
        evaluation_profile="+".join(resolved_metrics),
        metrics=resolved_metrics,
        repeats=repeats,
        repeat_parallel=repeat_parallel,
        runs=runs,
        started_at=started_at or datetime.now(UTC),
        rerun_unscored_limit=rerun_unscored_limit,
        reruns_used=reruns_used,
        unresolved_unscored_count=unresolved_unscored_count,
    )


def _experiment_summary_payload() -> dict[str, object]:
    return {
        "experiment_id": "test-experiment",
        "aggregate": _summary_aggregate_payload(),
        "config": _summary_config_payload(),
        "rerun": {
            "target_scored_runs": 1,
            "achieved_scored_runs": 1,
            "target_met": True,
            "unresolved_unscored_count": 0,
        },
        "runs": [
            {
                "run_id": "run-1",
                "unscored": False,
                "unscored_reasons": [],
                "run_valid": True,
                "performance_gates_passed": True,
                "composite_score": 0.9,
                "duration_sec": 90.0,
                "canonical_run_dir": "/tmp/canonical/run-1",
            }
        ],
    }


def _summary_aggregate_payload() -> dict[str, object]:
    return {
        "run_count_total": 1,
        "run_count_scored": 1,
        "unscored_count": 0,
        "rerun_required_count": 0,
        "valid_count": 1,
        "validity_rate": 1.0,
        "validity_rate_total": 1.0,
        "performance_pass_count": 1,
        "performance_pass_rate": 1.0,
        "composite_score": {"mean": 0.9},
        "quality_score": {"mean": 1.0},
        "diagnostic_score": {"mean": 1.0},
        "metric_outcomes": {
            "artifact-checks": {
                "pass_count": 1,
                "fail_count": 0,
                "sample_size": 1,
                "pass_rate": 1.0,
            }
        },
    }


def _summary_config_payload() -> dict[str, object]:
    return {
        "scenario_name": "homepage",
        "harness": "codex-cli",
        "model": "codex/gpt-5.4-mini",
        "evaluation_profile": (
            "functional+acceptance+verification-stability+execution-validity+resource-efficiency"
        ),
        "metrics": [
            "functional",
            "acceptance",
            "verification-stability",
            "execution-validity",
            "resource-efficiency",
        ],
        "repeats": 1,
        "repeat_parallel": 1,
        "rerun_unscored_limit": 2,
        "reruns_used": 1,
    }


def test_experiment_workspace_isolated_path() -> None:
    base = Path("/tmp/experiments/experiment-01")
    assert experiment_workspace(base, 3) == Path(
        "/tmp/experiments/experiment-01/runs/run-03/workspace"
    )


def test_create_experiment_summary_aggregates() -> None:
    run_a = _run("run-a", run_valid=True, duration=120.0)
    run_b = _run("run-b", run_valid=False, duration=160.0)
    started_at = datetime.now(UTC) - timedelta(minutes=5)
    summary = create_experiment_summary(
        _summary_input(
            runs=[run_a, run_b],
            repeat_parallel=2,
            started_at=started_at,
        )
    )

    assert summary["aggregate"]["run_count_total"] == 2
    assert summary["aggregate"]["run_count_scored"] == 2
    assert summary["aggregate"]["valid_count"] == 1
    assert summary["aggregate"]["validity_rate"] == 0.5
    assert summary["rerun"]["target_scored_runs"] == 2
    assert summary["rerun"]["achieved_scored_runs"] == 2
    assert summary["rerun"]["target_met"] is True
    assert len(summary["runs"]) == 2
    assert str(summary["experiment_id"]).endswith("__codex-gpt-5.4-mini__x2")
    assert summary["config"]["scenario_revision"] == "v001"
    assert summary["config"]["metrics"] == [
        "functional",
        "acceptance",
        "verification-stability",
        "execution-validity",
        "resource-efficiency",
    ]
    assert summary["config"]["sample_class"] == "smoke"
    assert summary["config"]["starter_root"] == "starter"
    assert summary["config"]["starter_fingerprint"] is None
    assert summary["sample"]["scenario_family"] == "code-delivery-nonvisual"
    assert summary["sample"]["minimum_scored_runs"] == 3
    assert summary["sample"]["preferred_scored_runs"] == 5
    assert summary["sample"]["sample_adequacy"] == 0.4


def test_create_experiment_summary_excludes_unscored_runs_from_stats() -> None:
    run_a = _run("run-a", run_valid=True, duration=120.0)
    run_b = _run(
        "run-b",
        run_valid=False,
        duration=160.0,
        unscored=True,
        artifact_checks_passed=False,
    )
    summary = create_experiment_summary(_summary_input(runs=[run_a, run_b]))

    assert summary["aggregate"]["run_count_total"] == 2
    assert summary["aggregate"]["run_count_scored"] == 1
    assert summary["aggregate"]["unscored_count"] == 1
    assert summary["aggregate"]["rerun_required_count"] == 1
    assert summary["aggregate"]["valid_count"] == 1
    assert summary["aggregate"]["validity_rate"] == 1.0
    assert summary["rerun"]["target_scored_runs"] == 2
    assert summary["rerun"]["achieved_scored_runs"] == 1
    assert summary["rerun"]["target_met"] is False
    assert summary["aggregate"]["metric_outcomes"] == {}


def test_create_experiment_summary_includes_rerun_metadata() -> None:
    run_a = _run("run-a", run_valid=True, duration=120.0, artifact_checks_passed=True)
    summary = create_experiment_summary(
        _summary_input(
            runs=[run_a],
            metrics=[
                "functional",
                "acceptance",
                "verification-stability",
                "execution-validity",
                "resource-efficiency",
                "artifact-checks",
            ],
            repeats=1,
            rerun_unscored_limit=3,
            reruns_used=1,
            unresolved_unscored_count=0,
        )
    )
    assert summary["config"]["rerun_unscored_limit"] == 3
    assert summary["config"]["reruns_used"] == 1
    assert summary["rerun"]["unresolved_unscored_count"] == 0
    assert summary["aggregate"]["metric_outcomes"]["artifact-checks"]["pass_count"] == 1
    assert summary["aggregate"]["metric_outcomes"]["artifact-checks"]["pass_rate"] == 1.0


def test_create_experiment_summary_marks_visual_review_samples() -> None:
    run_a = _run("run-a", run_valid=True, duration=120.0)
    summary = create_experiment_summary(
        _summary_input(
            runs=[run_a],
            metrics=[
                "functional",
                "acceptance",
                "verification-stability",
                "execution-validity",
                "resource-efficiency",
                "visual-regression",
            ],
            repeats=5,
        )
    )

    assert summary["config"]["sample_class"] == "review"
    assert summary["sample"]["scenario_family"] == "visual-ui-implementation"
    assert summary["sample"]["minimum_scored_runs"] == 3
    assert summary["sample"]["preferred_scored_runs"] == 5
    assert summary["sample"]["minimum_met"] is False
    assert summary["sample"]["preferred_met"] is False
    assert summary["sample"]["sample_adequacy"] == 0.2


def test_persist_experiment_writes_experiment_summary_and_report(tmp_path: Path) -> None:
    summary = _experiment_summary_payload()
    experiment_json_path, summary_path, report_path = persist_experiment(tmp_path, summary)
    assert experiment_json_path.exists()
    assert summary_path.exists()
    assert report_path.exists()
    assert "test-experiment" in experiment_json_path.read_text()
    assert "run-1" in report_path.read_text()
    assert "metric_outcomes" in report_path.read_text()
