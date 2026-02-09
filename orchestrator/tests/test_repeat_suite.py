"""Tests for repeat-suite aggregation artifacts."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentic_eval.repeat_suite import (
    create_repeat_suite_summary,
    persist_repeat_suite,
    repeat_workspace,
)
from agentic_eval.schemas.scorecard import EvalConfig, EvalRun, QualificationCheck, Scorecard


def _run(run_id: str, *, qualified: bool, duration: float, voided: bool = False) -> EvalRun:
    scorecard = Scorecard(
        run_id=run_id,
        task_name="homepage",
        agent="codex-cli",
        model="codex/gpt-5.2-low",
        rules_variant="strict",
        duration_sec=duration,
        metadata={
            "run": {
                "canonical_run_dir": f"/tmp/canonical/{run_id}",
                "summary_result_json": f"/tmp/canonical/{run_id}/summary/result.json",
            },
            "process": {"uncached_input_tokens": 1000},
        },
        voided=voided,
        void_reasons=["provider_rate_limit"] if voided else [],
    )
    scorecard.qualification.checks = [
        QualificationCheck(name="run_completed", passed=qualified, evidence=None)
    ]
    scorecard.optimization.command_count = 1
    scorecard.optimization.uncached_input_tokens = 10 if qualified else 250_000
    return EvalRun(
        id=run_id,
        timestamp=datetime.now(UTC).isoformat(),
        config=EvalConfig(
            model="codex/gpt-5.2-low",
            harness="codex-cli",
            rules_variant="strict",
            task_name="homepage",
            scaffold_template="next-shadcn-starter",
            scaffold_version="v2025.01",
        ),
        duration_sec=duration,
        terminated_early=False,
        scores=scorecard,
    )


def test_repeat_workspace_isolated_path():
    base = Path("/tmp/workspace")
    assert repeat_workspace(base, 3) == Path("/tmp/workspace-repeat-03")


def test_create_repeat_suite_summary_aggregates():
    run_a = _run("run-a", qualified=True, duration=120.0)
    run_b = _run("run-b", qualified=False, duration=160.0)
    started_at = datetime.now(UTC) - timedelta(minutes=5)
    summary = create_repeat_suite_summary(
        task_name="Homepage Task",
        harness="codex-cli",
        model="codex/gpt-5.2-low",
        rules_variant="strict",
        repeats=2,
        repeat_parallel=2,
        runs=[run_a, run_b],
        started_at=started_at,
    )

    assert summary["aggregate"]["run_count_total"] == 2
    assert summary["aggregate"]["run_count_scored"] == 2
    assert summary["aggregate"]["qualified_count"] == 1
    assert summary["aggregate"]["qualification_rate"] == 0.5
    assert summary["retry"]["target_scored_runs"] == 2
    assert summary["retry"]["achieved_scored_runs"] == 2
    assert summary["retry"]["target_met"] is True
    assert len(summary["runs"]) == 2
    assert str(summary["suite_id"]).endswith("__codex-gpt-5.2-low__x2")


def test_create_repeat_suite_summary_excludes_void_runs_from_stats():
    run_a = _run("run-a", qualified=True, duration=120.0)
    run_b = _run("run-b", qualified=False, duration=160.0, voided=True)
    summary = create_repeat_suite_summary(
        task_name="Homepage Task",
        harness="codex-cli",
        model="codex/gpt-5.2-low",
        rules_variant="strict",
        repeats=2,
        repeat_parallel=1,
        runs=[run_a, run_b],
        started_at=datetime.now(UTC),
    )

    assert summary["aggregate"]["run_count_total"] == 2
    assert summary["aggregate"]["run_count_scored"] == 1
    assert summary["aggregate"]["void_count"] == 1
    assert summary["aggregate"]["repeat_required_count"] == 1
    assert summary["aggregate"]["qualified_count"] == 1
    assert summary["aggregate"]["qualification_rate"] == 1.0
    assert summary["retry"]["target_scored_runs"] == 2
    assert summary["retry"]["achieved_scored_runs"] == 1
    assert summary["retry"]["target_met"] is False


def test_create_repeat_suite_summary_includes_retry_metadata():
    run_a = _run("run-a", qualified=True, duration=120.0)
    summary = create_repeat_suite_summary(
        task_name="Homepage Task",
        harness="codex-cli",
        model="codex/gpt-5.2-low",
        rules_variant="strict",
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


def test_persist_repeat_suite_writes_summary_and_readme(tmp_path: Path):
    summary = {
        "suite_id": "test-suite",
        "aggregate": {
            "run_count_total": 1,
            "run_count_scored": 1,
            "void_count": 0,
            "repeat_required_count": 0,
            "qualified_count": 1,
            "qualification_rate": 1.0,
            "qualification_rate_total": 1.0,
            "composite_score": {"mean": 0.9},
            "quality_score": {"mean": 1.0},
            "diagnostic_score": {"mean": 1.0},
        },
        "config": {
            "task_name": "homepage",
            "harness": "codex-cli",
            "model": "codex/gpt-5.2-low",
            "rules_variant": "strict",
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
                "qualified": True,
                "composite_score": 0.9,
                "duration_sec": 90.0,
                "canonical_run_dir": "/tmp/canonical/run-1",
            }
        ],
    }
    summary_path, readme_path = persist_repeat_suite(tmp_path, summary)
    assert summary_path.exists()
    assert readme_path.exists()
    assert "test-suite" in summary_path.read_text()
    assert "Run" in readme_path.read_text()
