"""Repeat-suite helpers for aggregate baseline runs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean, median, pstdev

from .schemas.scorecard import EvalRun


def repeat_workspace(base_workspace: Path, repeat_index: int) -> Path:
    """Return an isolated workspace path for one repeat."""
    return base_workspace.parent / f"{base_workspace.name}-repeat-{repeat_index:02d}"


def _run_pointer(run: EvalRun) -> dict[str, object]:
    run_meta = run.scores.metadata.get("run", {})
    canonical_run_dir = run_meta.get("canonical_run_dir")
    summary_result_json = run_meta.get("summary_result_json")
    return {
        "run_id": run.id,
        "timestamp": run.timestamp,
        "voided": run.scores.voided,
        "void_reasons": run.scores.void_reasons,
        "qualified": run.scores.qualification.passed,
        "composite_score": run.scores.composite_score,
        "diagnostic_score": run.scores.diagnostic_score,
        "quality_score": run.scores.quality_score,
        "duration_sec": run.duration_sec,
        "terminated_early": run.terminated_early,
        "termination_reason": run.termination_reason,
        "canonical_run_dir": canonical_run_dir if isinstance(canonical_run_dir, str) else None,
        "summary_result_json": summary_result_json
        if isinstance(summary_result_json, str)
        else None,
    }


def _stat_summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "median": 0.0, "stddev": 0.0, "min": 0.0, "max": 0.0}
    return {
        "mean": round(fmean(values), 6),
        "median": round(median(values), 6),
        "stddev": round(pstdev(values), 6) if len(values) > 1 else 0.0,
        "min": round(min(values), 6),
        "max": round(max(values), 6),
    }


def _uncached_tokens(run: EvalRun) -> int:
    process = run.scores.metadata.get("process", {})
    if not isinstance(process, dict):
        return 0
    return int(process.get("uncached_input_tokens", 0) or 0)


def _suite_id(task_name: str, harness: str, model: str, repeats: int, started_utc: datetime) -> str:
    return (
        f"{started_utc.strftime('%Y%m%d-%H%M%SZ')}__"
        f"{task_name.lower().replace(' ', '-')}__"
        f"{harness}__"
        f"{model.replace('/', '-')}"
        f"__x{repeats}"
    )


def _partition_runs(runs: list[EvalRun]) -> tuple[list[EvalRun], list[EvalRun], list[EvalRun]]:
    void_runs = [run for run in runs if run.scores.voided]
    scored_runs = [run for run in runs if not run.scores.voided]
    qualified_scored = [run for run in scored_runs if run.scores.qualification.passed]
    return void_runs, scored_runs, qualified_scored


def _aggregate_block(
    runs: list[EvalRun],
    void_runs: list[EvalRun],
    scored_runs: list[EvalRun],
    qualified_runs: list[EvalRun],
) -> dict[str, object]:
    composite_scores = [run.scores.composite_score for run in scored_runs]
    quality_scores = [run.scores.quality_score for run in scored_runs]
    diagnostic_scores = [run.scores.diagnostic_score for run in scored_runs]
    durations = [run.duration_sec for run in scored_runs]
    tokens = [float(_uncached_tokens(run)) for run in scored_runs]
    scored_count = len(scored_runs)
    total_count = len(runs)
    qualified_count = len(qualified_runs)
    return {
        "run_count_total": total_count,
        "run_count_scored": scored_count,
        "void_count": len(void_runs),
        "repeat_required_count": len(void_runs),
        "qualified_count": qualified_count,
        "qualification_rate": round(qualified_count / max(1, scored_count), 6),
        "qualification_rate_total": round(qualified_count / max(1, total_count), 6),
        "run_count": scored_count,
        "composite_score": _stat_summary(composite_scores),
        "quality_score": _stat_summary(quality_scores),
        "diagnostic_score": _stat_summary(diagnostic_scores),
        "duration_sec": _stat_summary(durations),
        "uncached_input_tokens": _stat_summary(tokens),
    }


def create_repeat_suite_summary(
    *,
    task_name: str,
    harness: str,
    model: str,
    rules_variant: str,
    repeats: int,
    repeat_parallel: int,
    runs: list[EvalRun],
    started_at: datetime,
    retry_void_limit: int = 0,
    retries_used: int = 0,
    unresolved_void_count: int = 0,
) -> dict[str, object]:
    """Build deterministic summary metrics for a repeat suite."""
    started_utc = started_at.astimezone(UTC)
    finished_utc = datetime.now(UTC)
    suite_id = _suite_id(task_name, harness, model, repeats, started_utc)
    void_runs, scored_runs, qualified_runs = _partition_runs(runs)
    run_pointers = [_run_pointer(run) for run in runs]

    return {
        "suite_id": suite_id,
        "created_at_utc": finished_utc.isoformat(),
        "started_at_utc": started_utc.isoformat(),
        "completed_at_utc": finished_utc.isoformat(),
        "config": {
            "task_name": task_name,
            "harness": harness,
            "model": model,
            "rules_variant": rules_variant,
            "repeats": repeats,
            "repeat_parallel": repeat_parallel,
            "retry_void_limit": retry_void_limit,
            "retries_used": retries_used,
        },
        "aggregate": _aggregate_block(runs, void_runs, scored_runs, qualified_runs),
        "runs": run_pointers,
        "retry": {
            "target_scored_runs": repeats,
            "achieved_scored_runs": len(scored_runs),
            "target_met": len(scored_runs) >= repeats,
            "unresolved_void_count": unresolved_void_count,
        },
    }


def persist_repeat_suite(results_dir: Path, suite_summary: dict[str, object]) -> tuple[Path, Path]:
    """Write repeat-suite summary artifacts and return their paths."""
    suite_id = str(suite_summary["suite_id"])
    suite_dir = results_dir / "suites" / suite_id
    suite_dir.mkdir(parents=True, exist_ok=True)

    summary_path = suite_dir / "summary.json"
    summary_path.write_text(json.dumps(suite_summary, indent=2))

    aggregate = suite_summary.get("aggregate", {})
    config = suite_summary.get("config", {})
    retry = suite_summary.get("retry", {})
    lines = [
        "# Repeat Suite Summary",
        "",
        f"- suite_id: `{suite_id}`",
        f"- task: `{config.get('task_name')}`",
        f"- harness: `{config.get('harness')}`",
        f"- model: `{config.get('model')}`",
        f"- rules_variant: `{config.get('rules_variant')}`",
        f"- repeats: `{config.get('repeats')}`",
        f"- repeat_parallel: `{config.get('repeat_parallel')}`",
        f"- retry_void_limit: `{config.get('retry_void_limit')}`",
        f"- retries_used: `{config.get('retries_used')}`",
        "",
        "## Aggregate",
        f"- run_count_total: `{aggregate.get('run_count_total')}`",
        f"- run_count_scored: `{aggregate.get('run_count_scored')}`",
        f"- void_count: `{aggregate.get('void_count')}`",
        f"- repeat_required_count: `{aggregate.get('repeat_required_count')}`",
        f"- qualified_count: `{aggregate.get('qualified_count')}`",
        f"- qualification_rate_scored: `{aggregate.get('qualification_rate')}`",
        f"- qualification_rate_total: `{aggregate.get('qualification_rate_total')}`",
        f"- target_scored_runs: `{retry.get('target_scored_runs')}`",
        f"- achieved_scored_runs: `{retry.get('achieved_scored_runs')}`",
        f"- target_met: `{retry.get('target_met')}`",
        f"- unresolved_void_count: `{retry.get('unresolved_void_count')}`",
        (
            "- composite_mean: "
            f"`{(aggregate.get('composite_score', {}) or {}).get('mean', 0.0):.6f}`"
        ),
        (f"- quality_mean: `{(aggregate.get('quality_score', {}) or {}).get('mean', 0.0):.6f}`"),
        (
            "- diagnostic_mean: "
            f"`{(aggregate.get('diagnostic_score', {}) or {}).get('mean', 0.0):.6f}`"
        ),
        "",
        "## Runs",
    ]

    for run in suite_summary.get("runs", []):
        if not isinstance(run, dict):
            continue
        lines.append(
            f"- run_id=`{run.get('run_id')}`, voided=`{run.get('voided')}`, "
            f"void_reasons=`{run.get('void_reasons')}`, qualified=`{run.get('qualified')}`, "
            f"composite=`{run.get('composite_score')}`, "
            f"duration_sec=`{run.get('duration_sec')}`, "
            f"canonical=`{run.get('canonical_run_dir')}`"
        )

    readme_path = suite_dir / "README.md"
    readme_path.write_text("\n".join(lines) + "\n")
    return summary_path, readme_path
