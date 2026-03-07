"""Tests for repeat-suite aggregation artifacts."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from raidar.repeat_suite import (
    create_repeat_suite_summary,
    persist_repeat_suite,
    repeat_workspace,
)
from raidar.schemas.scorecard import EvalConfig, EvalRun, GateCheck, ModuleResult, Scorecard


def _run(
    run_id: str,
    *,
    run_valid: bool,
    duration: float,
    voided: bool = False,
    artifact_presence_passed: bool | None = None,
) -> EvalRun:
    scorecard = Scorecard(
        run_id=run_id,
        task_name="homepage",
        task_version="v001",
        agent="codex-cli",
        model="codex/gpt-5.2-low",
        scaffold_root="scaffold",
        duration_sec=duration,
        metadata={
            "run": {
                "canonical_run_dir": f"/tmp/canonical/{run_id}",
                "run_json_path": f"/tmp/canonical/{run_id}/run.json",
            },
            "process": {"uncached_input_tokens": 1000},
        },
        voided=voided,
        void_reasons=["provider_rate_limit"] if voided else [],
        modules=(
            [
                ModuleResult(
                    module_id="artifact_presence",
                    passed=artifact_presence_passed,
                    matched_count=1 if artifact_presence_passed else 0,
                    missing_patterns=[]
                    if artifact_presence_passed
                    else ["src/components/**/*.tsx"],
                    evidence="test",
                )
            ]
            if artifact_presence_passed is not None
            else []
        ),
    )
    scorecard.run_validity.checks = [
        GateCheck(name="run_completed", passed=run_valid, evidence=None)
    ]
    scorecard.optimization.command_count = 1
    scorecard.optimization.uncached_input_tokens = 10 if run_valid else 250_000
    return EvalRun(
        id=run_id,
        timestamp=datetime.now(UTC).isoformat(),
        config=EvalConfig(
            model="codex/gpt-5.2-low",
            harness="codex-cli",
            task_name="homepage",
            task_version="v001",
            scaffold_root="scaffold",
            metric_profile="v2:functional+compliance+efficiency+run-validity+optimization",
        ),
        duration_sec=duration,
        terminated_early=False,
        scores=scorecard,
    )


def test_repeat_workspace_isolated_path():
    base = Path("/tmp/evals/suite-01")
    assert repeat_workspace(base, 3) == Path("/tmp/evals/suite-01/runs/run-03/workspace")


def test_create_repeat_suite_summary_aggregates():
    run_a = _run("run-a", run_valid=True, duration=120.0)
    run_b = _run("run-b", run_valid=False, duration=160.0)
    started_at = datetime.now(UTC) - timedelta(minutes=5)
    summary = create_repeat_suite_summary(
        task_name="Homepage Task",
        task_version="v001",
        harness="codex-cli",
        model="codex/gpt-5.2-low",
        metric_profile="v2:functional+compliance+efficiency+run-validity+optimization",
        metric_modules=["functional", "compliance", "efficiency", "run-validity", "optimization"],
        repeats=2,
        repeat_parallel=2,
        runs=[run_a, run_b],
        started_at=started_at,
    )

    assert summary["aggregate"]["run_count_total"] == 2
    assert summary["aggregate"]["run_count_scored"] == 2
    assert summary["aggregate"]["valid_count"] == 1
    assert summary["aggregate"]["validity_rate"] == 0.5
    assert summary["retry"]["target_scored_runs"] == 2
    assert summary["retry"]["achieved_scored_runs"] == 2
    assert summary["retry"]["target_met"] is True
    assert len(summary["runs"]) == 2
    assert str(summary["suite_id"]).endswith("__codex-gpt-5.2-low__x2")
    assert summary["config"]["task_version"] == "v001"
    assert summary["config"]["metric_modules"] == [
        "functional",
        "compliance",
        "efficiency",
        "run-validity",
        "optimization",
    ]
    assert summary["config"]["scaffold_root"] == "scaffold"
    assert summary["config"]["scaffold_fingerprint"] is None


def test_create_repeat_suite_summary_excludes_void_runs_from_stats():
    run_a = _run("run-a", run_valid=True, duration=120.0)
    run_b = _run(
        "run-b",
        run_valid=False,
        duration=160.0,
        voided=True,
        artifact_presence_passed=False,
    )
    summary = create_repeat_suite_summary(
        task_name="Homepage Task",
        task_version="v001",
        harness="codex-cli",
        model="codex/gpt-5.2-low",
        metric_profile="v2:functional+compliance+efficiency+run-validity+optimization",
        metric_modules=["functional", "compliance", "efficiency", "run-validity", "optimization"],
        repeats=2,
        repeat_parallel=1,
        runs=[run_a, run_b],
        started_at=datetime.now(UTC),
    )

    assert summary["aggregate"]["run_count_total"] == 2
    assert summary["aggregate"]["run_count_scored"] == 1
    assert summary["aggregate"]["void_count"] == 1
    assert summary["aggregate"]["repeat_required_count"] == 1
    assert summary["aggregate"]["valid_count"] == 1
    assert summary["aggregate"]["validity_rate"] == 1.0
    assert summary["retry"]["target_scored_runs"] == 2
    assert summary["retry"]["achieved_scored_runs"] == 1
    assert summary["retry"]["target_met"] is False
    assert summary["aggregate"]["module_outcomes"] == {}


def test_create_repeat_suite_summary_includes_retry_metadata():
    run_a = _run("run-a", run_valid=True, duration=120.0, artifact_presence_passed=True)
    summary = create_repeat_suite_summary(
        task_name="Homepage Task",
        task_version="v001",
        harness="codex-cli",
        model="codex/gpt-5.2-low",
        metric_profile="v2:functional+compliance+efficiency+run-validity+optimization+artifact_presence",
        metric_modules=[
            "functional",
            "compliance",
            "efficiency",
            "run-validity",
            "optimization",
            "artifact_presence",
        ],
        repeats=1,
        repeat_parallel=1,
        runs=[run_a],
        started_at=datetime.now(UTC),
        retry_void_limit=3,
        retries_used=1,
        unresolved_void_count=0,
    )
    assert summary["config"]["retry_void_limit"] == 3
    assert summary["config"]["retries_used"] == 1
    assert summary["retry"]["unresolved_void_count"] == 0
    assert summary["aggregate"]["module_outcomes"]["artifact_presence"]["pass_count"] == 1
    assert summary["aggregate"]["module_outcomes"]["artifact_presence"]["pass_rate"] == 1.0


def test_persist_repeat_suite_writes_suite_summary_and_analysis(tmp_path: Path):
    summary = {
        "suite_id": "test-suite",
        "aggregate": {
            "run_count_total": 1,
            "run_count_scored": 1,
            "void_count": 0,
            "repeat_required_count": 0,
            "valid_count": 1,
            "validity_rate": 1.0,
            "validity_rate_total": 1.0,
            "performance_pass_count": 1,
            "performance_pass_rate": 1.0,
            "composite_score": {"mean": 0.9},
            "quality_score": {"mean": 1.0},
            "diagnostic_score": {"mean": 1.0},
            "module_outcomes": {
                "artifact_presence": {
                    "pass_count": 1,
                    "fail_count": 0,
                    "sample_size": 1,
                    "pass_rate": 1.0,
                }
            },
        },
        "config": {
            "task_name": "homepage",
            "harness": "codex-cli",
            "model": "codex/gpt-5.2-low",
            "metric_profile": "v2:functional+compliance+efficiency+run-validity+optimization",
            "metric_modules": [
                "functional",
                "compliance",
                "efficiency",
                "run-validity",
                "optimization",
            ],
            "repeats": 1,
            "repeat_parallel": 1,
            "retry_void_limit": 2,
            "retries_used": 1,
        },
        "retry": {
            "target_scored_runs": 1,
            "achieved_scored_runs": 1,
            "target_met": True,
            "unresolved_void_count": 0,
        },
        "runs": [
            {
                "run_id": "run-1",
                "voided": False,
                "void_reasons": [],
                "run_valid": True,
                "performance_gates_passed": True,
                "composite_score": 0.9,
                "duration_sec": 90.0,
                "canonical_run_dir": "/tmp/canonical/run-1",
            }
        ],
    }
    suite_json_path, summary_path, analysis_path = persist_repeat_suite(tmp_path, summary)
    assert suite_json_path.exists()
    assert summary_path.exists()
    assert analysis_path.exists()
    assert "test-suite" in suite_json_path.read_text()
    assert "run-1" in analysis_path.read_text()
    assert "module_outcomes" in analysis_path.read_text()
