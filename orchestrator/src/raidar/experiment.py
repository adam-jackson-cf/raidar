"""Experiment helpers for aggregate baseline runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean, median, pstdev

from .schemas.scorecard import EvalRun


@dataclass(frozen=True, slots=True)
class ExperimentSummaryInput:
    """Canonical input contract for experiment summary aggregation."""

    scenario_name: str
    scenario_revision: str
    harness: str
    model: str
    evaluation_profile: str
    metrics: list[str]
    repeats: int
    repeat_parallel: int
    runs: list[EvalRun]
    started_at: datetime
    rerun_unscored_limit: int = 0
    reruns_used: int = 0
    unresolved_unscored_count: int = 0


@dataclass(frozen=True, slots=True)
class _ExperimentSummaryContext:
    started_utc: datetime
    finished_utc: datetime
    experiment_id: str
    unscored_runs: list[EvalRun]
    scored_runs: list[EvalRun]
    valid_runs: list[EvalRun]
    run_pointers: list[dict[str, object]]
    starter_root: str | None
    starter_fingerprint: object
    sample_policy: dict[str, object]
    sample_class: str
    achieved_scored_runs: int


def experiment_workspace(base_workspace: Path, run_index: int) -> Path:
    """Return an isolated run workspace path for one run under an experiment root."""

    return base_workspace / "runs" / f"run-{run_index:02d}" / "workspace"


def _run_pointer(run: EvalRun) -> dict[str, object]:
    run_meta = run.scores.metadata.get("run", {})
    canonical_run_dir = run_meta.get("canonical_run_dir")
    run_json_path = run_meta.get("run_json_path")
    return {
        "run_id": run.id,
        "timestamp": run.timestamp,
        "unscored": run.scores.unscored,
        "unscored_reasons": run.scores.unscored_reasons,
        "run_valid": run.scores.execution_validity.passed,
        "performance_gates_passed": run.scores.performance_gates.passed,
        "composite_score": run.scores.composite_score,
        "diagnostic_score": run.scores.diagnostic_score,
        "quality_score": run.scores.quality_score,
        "duration_sec": run.duration_sec,
        "terminated_early": run.terminated_early,
        "termination_reason": run.termination_reason,
        "canonical_run_dir": canonical_run_dir if isinstance(canonical_run_dir, str) else None,
        "run_json_path": run_json_path if isinstance(run_json_path, str) else None,
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


def _experiment_id(
    scenario_name: str,
    harness: str,
    model: str,
    repeats: int,
    started_utc: datetime,
) -> str:
    return (
        f"{started_utc.strftime('%Y%m%d-%H%M%SZ')}__"
        f"{scenario_name.lower().replace(' ', '-')}__"
        f"{harness}__"
        f"{model.replace('/', '-')}"
        f"__x{repeats}"
    )


def _partition_runs(runs: list[EvalRun]) -> tuple[list[EvalRun], list[EvalRun], list[EvalRun]]:
    unscored_runs = [run for run in runs if run.scores.unscored]
    scored_runs = [run for run in runs if not run.scores.unscored]
    valid_scored = [run for run in scored_runs if run.scores.execution_validity.passed]
    return unscored_runs, scored_runs, valid_scored


def _aggregate_metric_outcomes(runs: list[EvalRun]) -> dict[str, dict[str, float | int]]:
    by_metric: dict[str, dict[str, int]] = {}
    for run in runs:
        for metric in run.scores.metric_results:
            counts = by_metric.setdefault(metric.metric_id, {"pass_count": 0, "fail_count": 0})
            if metric.passed:
                counts["pass_count"] += 1
            else:
                counts["fail_count"] += 1

    outcomes: dict[str, dict[str, float | int]] = {}
    for metric_id, counts in sorted(by_metric.items()):
        sample_size = counts["pass_count"] + counts["fail_count"]
        outcomes[metric_id] = {
            "pass_count": counts["pass_count"],
            "fail_count": counts["fail_count"],
            "sample_size": sample_size,
            "pass_rate": round(counts["pass_count"] / max(1, sample_size), 6),
        }
    return outcomes


def _aggregate_block(
    runs: list[EvalRun],
    unscored_runs: list[EvalRun],
    scored_runs: list[EvalRun],
    valid_runs: list[EvalRun],
) -> dict[str, object]:
    metric_outcomes = _aggregate_metric_outcomes(scored_runs)
    composite_scores = [run.scores.composite_score for run in scored_runs]
    quality_scores = [run.scores.quality_score for run in scored_runs]
    diagnostic_scores = [run.scores.diagnostic_score for run in scored_runs]
    durations = [run.duration_sec for run in scored_runs]
    tokens = [float(_uncached_tokens(run)) for run in scored_runs]
    scored_count = len(scored_runs)
    total_count = len(runs)
    valid_count = len(valid_runs)
    performance_pass_count = sum(1 for run in scored_runs if run.scores.performance_gates.passed)
    return {
        "run_count_total": total_count,
        "run_count_scored": scored_count,
        "unscored_count": len(unscored_runs),
        "rerun_required_count": len(unscored_runs),
        "valid_count": valid_count,
        "validity_rate": round(valid_count / max(1, scored_count), 6),
        "validity_rate_total": round(valid_count / max(1, total_count), 6),
        "performance_pass_count": performance_pass_count,
        "performance_pass_rate": round(performance_pass_count / max(1, scored_count), 6),
        "run_count": scored_count,
        "composite_score": _stat_summary(composite_scores),
        "quality_score": _stat_summary(quality_scores),
        "diagnostic_score": _stat_summary(diagnostic_scores),
        "duration_sec": _stat_summary(durations),
        "uncached_input_tokens": _stat_summary(tokens),
        "metric_outcomes": metric_outcomes,
    }


def _sample_policy(metrics: list[str]) -> dict[str, object]:
    if "visual-regression" in metrics:
        return {
            "scenario_family": "visual-ui-implementation",
            "minimum_scored_runs": 3,
            "preferred_scored_runs": 5,
        }
    return {
        "scenario_family": "code-delivery-nonvisual",
        "minimum_scored_runs": 3,
        "preferred_scored_runs": 5,
    }


def _summary_context(summary_input: ExperimentSummaryInput) -> _ExperimentSummaryContext:
    started_utc = summary_input.started_at.astimezone(UTC)
    finished_utc = datetime.now(UTC)
    experiment_id = _experiment_id(
        summary_input.scenario_name,
        summary_input.harness,
        summary_input.model,
        summary_input.repeats,
        started_utc,
    )
    unscored_runs, scored_runs, valid_runs = _partition_runs(summary_input.runs)
    run_pointers = [_run_pointer(run) for run in summary_input.runs]
    first_run = summary_input.runs[0] if summary_input.runs else None
    starter_meta = first_run.scores.metadata.get("starter", {}) if first_run else {}
    starter_root = first_run.config.starter_root if first_run is not None else None
    starter_fingerprint = (
        starter_meta.get("fingerprint") if isinstance(starter_meta, dict) else None
    )
    sample_policy = _sample_policy(summary_input.metrics)
    preferred_scored_runs = int(sample_policy["preferred_scored_runs"])
    achieved_scored_runs = len(scored_runs)
    sample_class = "review" if summary_input.repeats >= preferred_scored_runs else "smoke"
    return _ExperimentSummaryContext(
        started_utc=started_utc,
        finished_utc=finished_utc,
        experiment_id=experiment_id,
        unscored_runs=unscored_runs,
        scored_runs=scored_runs,
        valid_runs=valid_runs,
        run_pointers=run_pointers,
        starter_root=starter_root,
        starter_fingerprint=starter_fingerprint,
        sample_policy=sample_policy,
        sample_class=sample_class,
        achieved_scored_runs=achieved_scored_runs,
    )


def _summary_config(
    summary_input: ExperimentSummaryInput, context: _ExperimentSummaryContext
) -> dict[str, object]:
    return {
        "scenario_name": summary_input.scenario_name,
        "scenario_revision": summary_input.scenario_revision,
        "harness": summary_input.harness,
        "model": summary_input.model,
        "evaluation_profile": summary_input.evaluation_profile,
        "metrics": summary_input.metrics,
        "repeats": summary_input.repeats,
        "repeat_parallel": summary_input.repeat_parallel,
        "rerun_unscored_limit": summary_input.rerun_unscored_limit,
        "reruns_used": summary_input.reruns_used,
        "starter_root": context.starter_root,
        "starter_fingerprint": context.starter_fingerprint,
        "sample_class": context.sample_class,
    }


def _summary_sample(context: _ExperimentSummaryContext) -> dict[str, object]:
    preferred_scored_runs = int(context.sample_policy["preferred_scored_runs"])
    minimum_scored_runs = int(context.sample_policy["minimum_scored_runs"])
    sample_adequacy = round(
        min(context.achieved_scored_runs / max(1, preferred_scored_runs), 1.0), 6
    )
    return {
        **context.sample_policy,
        "sample_class": context.sample_class,
        "achieved_scored_runs": context.achieved_scored_runs,
        "minimum_met": context.achieved_scored_runs >= minimum_scored_runs,
        "preferred_met": context.achieved_scored_runs >= preferred_scored_runs,
        "sample_adequacy": sample_adequacy,
    }


def _summary_rerun(
    summary_input: ExperimentSummaryInput, context: _ExperimentSummaryContext
) -> dict[str, object]:
    return {
        "target_scored_runs": summary_input.repeats,
        "achieved_scored_runs": context.achieved_scored_runs,
        "target_met": context.achieved_scored_runs >= summary_input.repeats,
        "unresolved_unscored_count": summary_input.unresolved_unscored_count,
    }


def create_experiment_summary(summary_input: ExperimentSummaryInput) -> dict[str, object]:
    """Build deterministic summary metrics for an experiment."""

    context = _summary_context(summary_input)

    return {
        "experiment_id": context.experiment_id,
        "created_at_utc": context.finished_utc.isoformat(),
        "started_at_utc": context.started_utc.isoformat(),
        "completed_at_utc": context.finished_utc.isoformat(),
        "config": _summary_config(summary_input, context),
        "aggregate": _aggregate_block(
            summary_input.runs,
            context.unscored_runs,
            context.scored_runs,
            context.valid_runs,
        ),
        "runs": context.run_pointers,
        "sample": _summary_sample(context),
        "rerun": _summary_rerun(summary_input, context),
    }


def _experiment_summary_payload(experiment: dict[str, object]) -> dict[str, object]:
    return {
        "experiment_id": experiment.get("experiment_id"),
        "created_at_utc": experiment.get("created_at_utc"),
        "started_at_utc": experiment.get("started_at_utc"),
        "completed_at_utc": experiment.get("completed_at_utc"),
        "config": experiment.get("config"),
        "aggregate": experiment.get("aggregate"),
        "sample": experiment.get("sample"),
        "rerun": experiment.get("rerun"),
        "run_count": (
            len(experiment.get("runs", [])) if isinstance(experiment.get("runs"), list) else 0
        ),
    }


def _append_sample_lines(lines: list[str], sample: object) -> None:
    if not isinstance(sample, dict):
        return
    lines.extend(
        [
            "",
            "## Sample",
            f"- scenario_family: `{sample.get('scenario_family')}`",
            f"- minimum_scored_runs: `{sample.get('minimum_scored_runs')}`",
            f"- preferred_scored_runs: `{sample.get('preferred_scored_runs')}`",
            f"- sample_class: `{sample.get('sample_class')}`",
            f"- achieved_scored_runs: `{sample.get('achieved_scored_runs')}`",
            f"- minimum_met: `{sample.get('minimum_met')}`",
            f"- preferred_met: `{sample.get('preferred_met')}`",
            f"- sample_adequacy: `{sample.get('sample_adequacy')}`",
        ]
    )


def _append_metric_outcome_lines(lines: list[str], metric_outcomes: object) -> None:
    if not isinstance(metric_outcomes, dict):
        return
    lines.extend(["", "## metric_outcomes"])
    if not metric_outcomes:
        lines.append("- metric_outcomes: `{}`")
        return
    for metric_id, outcome in sorted(metric_outcomes.items()):
        if not isinstance(outcome, dict):
            continue
        lines.append(
            f"- {metric_id}: pass_count=`{outcome.get('pass_count')}` "
            f"fail_count=`{outcome.get('fail_count')}` "
            f"sample_size=`{outcome.get('sample_size')}` "
            f"pass_rate=`{outcome.get('pass_rate')}`"
        )


def _append_run_lines(lines: list[str], runs: object) -> None:
    if not isinstance(runs, list):
        return
    lines.extend(["", "## Runs"])
    if not runs:
        lines.append("- runs: `[]`")
        return
    for run in runs:
        if not isinstance(run, dict):
            continue
        lines.append(
            f"- {run.get('run_id')}: unscored=`{run.get('unscored')}` "
            f"run_valid=`{run.get('run_valid')}` "
            f"performance_gates_passed=`{run.get('performance_gates_passed')}` "
            f"composite_score=`{run.get('composite_score')}` "
            f"duration_sec=`{run.get('duration_sec')}` "
            f"canonical_run_dir=`{run.get('canonical_run_dir')}`"
        )


def _report_header_lines(experiment_id: str, config: dict[str, object]) -> list[str]:
    return [
        "# Experiment Summary",
        "",
        f"- experiment_id: `{experiment_id}`",
        f"- scenario: `{config.get('scenario_name')}`",
        f"- scenario_revision: `{config.get('scenario_revision')}`",
        f"- harness: `{config.get('harness')}`",
        f"- model: `{config.get('model')}`",
        f"- evaluation_profile: `{config.get('evaluation_profile')}`",
        f"- metrics: `{config.get('metrics')}`",
        f"- repeats: `{config.get('repeats')}`",
        f"- repeat_parallel: `{config.get('repeat_parallel')}`",
        f"- rerun_unscored_limit: `{config.get('rerun_unscored_limit')}`",
        f"- reruns_used: `{config.get('reruns_used')}`",
        f"- sample_class: `{config.get('sample_class')}`",
    ]


def _report_aggregate_lines(aggregate: dict[str, object], rerun: dict[str, object]) -> list[str]:
    return [
        "",
        "## Aggregate",
        f"- run_count_total: `{aggregate.get('run_count_total')}`",
        f"- run_count_scored: `{aggregate.get('run_count_scored')}`",
        f"- unscored_count: `{aggregate.get('unscored_count')}`",
        f"- rerun_required_count: `{aggregate.get('rerun_required_count')}`",
        f"- valid_count: `{aggregate.get('valid_count')}`",
        f"- validity_rate_scored: `{aggregate.get('validity_rate')}`",
        f"- validity_rate_total: `{aggregate.get('validity_rate_total')}`",
        f"- performance_pass_count: `{aggregate.get('performance_pass_count')}`",
        f"- performance_pass_rate: `{aggregate.get('performance_pass_rate')}`",
        f"- target_scored_runs: `{rerun.get('target_scored_runs')}`",
        f"- achieved_scored_runs: `{rerun.get('achieved_scored_runs')}`",
        f"- target_met: `{rerun.get('target_met')}`",
        f"- unresolved_unscored_count: `{rerun.get('unresolved_unscored_count')}`",
        (
            "- composite_mean: "
            f"`{(aggregate.get('composite_score', {}) or {}).get('mean', 0.0):.6f}`"
        ),
        f"- quality_mean: `{(aggregate.get('quality_score', {}) or {}).get('mean', 0.0):.6f}`",
        (
            "- diagnostic_mean: "
            f"`{(aggregate.get('diagnostic_score', {}) or {}).get('mean', 0.0):.6f}`"
        ),
    ]


def _experiment_report_lines(experiment_summary: dict[str, object]) -> list[str]:
    experiment_id = str(experiment_summary["experiment_id"])
    aggregate = experiment_summary.get("aggregate", {})
    config = experiment_summary.get("config", {})
    rerun = experiment_summary.get("rerun", {})
    runs = experiment_summary.get("runs", [])
    metric_outcomes = aggregate.get("metric_outcomes", {}) if isinstance(aggregate, dict) else {}
    sample = experiment_summary.get("sample", {})
    typed_aggregate = aggregate if isinstance(aggregate, dict) else {}
    typed_config = config if isinstance(config, dict) else {}
    typed_rerun = rerun if isinstance(rerun, dict) else {}

    lines = _report_header_lines(experiment_id, typed_config)
    lines.extend(_report_aggregate_lines(typed_aggregate, typed_rerun))
    _append_sample_lines(lines, sample)
    _append_metric_outcome_lines(lines, metric_outcomes)
    _append_run_lines(lines, runs)
    return lines


def persist_experiment(
    results_dir: Path, experiment_summary: dict[str, object]
) -> tuple[Path, Path, Path]:
    """Write experiment artifacts and return the three canonical output paths."""

    experiment_dir = results_dir
    experiment_dir.mkdir(parents=True, exist_ok=True)

    experiment_json_path = experiment_dir / "experiment.json"
    experiment_json_path.write_text(json.dumps(experiment_summary, indent=2))

    summary_path = experiment_dir / "experiment-summary.json"
    summary_path.write_text(json.dumps(_experiment_summary_payload(experiment_summary), indent=2))

    report_path = experiment_dir / "report.md"
    report_path.write_text("\n".join(_experiment_report_lines(experiment_summary)) + "\n")
    return experiment_json_path, summary_path, report_path
