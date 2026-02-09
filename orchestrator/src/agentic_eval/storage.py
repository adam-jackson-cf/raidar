"""Run storage and aggregation for evaluation results."""

import csv
import json
from datetime import datetime
from pathlib import Path

from .schemas.scorecard import EvalRun


def save_run(run: EvalRun, results_dir: Path) -> Path:
    """Save an evaluation run to JSON file.

    Args:
        run: Evaluation run to save
        results_dir: Directory to store results

    Returns:
        Path to saved file
    """
    results_dir.mkdir(parents=True, exist_ok=True)
    output_path = results_dir / "runs" / run.id / "summary" / "result.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        f.write(run.model_dump_json(indent=2))

    return output_path


def load_run(path: Path) -> EvalRun:
    """Load an evaluation run from JSON file."""
    with open(path) as f:
        return EvalRun.model_validate_json(f.read())


def load_all_runs(results_dir: Path) -> list[EvalRun]:
    """Load all evaluation runs from a results directory."""
    runs = []

    # Canonical layout: results/runs/<instance>/summary/result.json
    for json_file in results_dir.glob("runs/*/summary/result.json"):
        try:
            runs.append(load_run(json_file))
        except Exception:
            continue  # Skip invalid files
    return sorted(runs, key=lambda r: r.timestamp)


def _variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def _uncached_tokens(run: EvalRun) -> int:
    process = run.scores.metadata.get("process", {})
    if not isinstance(process, dict):
        return 0
    return int(process.get("uncached_input_tokens", 0) or 0)


def _empty_group_stats() -> dict[str, float | int]:
    return {
        "count": 0,
        "scored_count": 0,
        "void_count": 0,
        "void_rate": 0.0,
        "avg_score": 0.0,
        "score_variance": 0.0,
        "pass_rate": 0.0,
        "avg_diagnostic_score": 0.0,
        "diagnostic_variance": 0.0,
        "avg_duration_sec": 0.0,
        "duration_variance_sec": 0.0,
    }


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _safe_average(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _scored_runs(runs_list: list[EvalRun]) -> list[EvalRun]:
    return [run for run in runs_list if not run.scores.voided]


def _group_stats(runs_list: list[EvalRun]) -> dict[str, float | int]:
    if not runs_list:
        return _empty_group_stats()

    scored_runs = _scored_runs(runs_list)
    void_count = len(runs_list) - len(scored_runs)
    composite_scores = [run.scores.composite_score for run in scored_runs]
    diagnostic_scores = [run.scores.diagnostic_score for run in scored_runs]
    durations = [run.duration_sec for run in scored_runs]
    qualified = [run for run in scored_runs if run.scores.qualification.passed]
    scored_count = len(scored_runs)
    return {
        "count": len(runs_list),
        "scored_count": scored_count,
        "void_count": void_count,
        "void_rate": _safe_ratio(void_count, len(runs_list)),
        "avg_score": _safe_average(composite_scores),
        "score_variance": _variance(composite_scores),
        "pass_rate": _safe_ratio(len(qualified), scored_count),
        "avg_diagnostic_score": _safe_average(diagnostic_scores),
        "diagnostic_variance": _variance(diagnostic_scores),
        "avg_duration_sec": _safe_average(durations),
        "duration_variance_sec": _variance(durations),
    }


def aggregate_results(runs: list[EvalRun]) -> dict:
    """Aggregate results across multiple runs.

    Args:
        runs: List of evaluation runs

    Returns:
        Aggregated statistics
    """
    if not runs:
        return {"total_runs": 0}

    # Group by configuration
    by_harness: dict[str, list[EvalRun]] = {}
    by_model: dict[str, list[EvalRun]] = {}
    by_rules: dict[str, list[EvalRun]] = {}
    by_scaffold: dict[str, list[EvalRun]] = {}
    by_config: dict[str, list[EvalRun]] = {}

    for run in runs:
        harness = run.config.harness
        model = run.config.model
        rules = run.config.rules_variant
        scaffold_key = f"{run.config.scaffold_template}@{run.config.scaffold_version}"
        config_key = (
            f"{harness}|{model}|{rules}|{run.config.task_name}|"
            f"{run.config.scaffold_template}@{run.config.scaffold_version}"
        )

        by_harness.setdefault(harness, []).append(run)
        by_model.setdefault(model, []).append(run)
        by_rules.setdefault(rules, []).append(run)
        by_scaffold.setdefault(scaffold_key, []).append(run)
        by_config.setdefault(config_key, []).append(run)

    return {
        "total_runs": len(runs),
        "by_harness": {h: _group_stats(r) for h, r in by_harness.items()},
        "by_model": {m: _group_stats(r) for m, r in by_model.items()},
        "by_rules": {v: _group_stats(r) for v, r in by_rules.items()},
        "by_scaffold": {key: _group_stats(r) for key, r in by_scaffold.items()},
        "by_config": {key: _group_stats(r) for key, r in by_config.items()},
    }


def export_to_csv(runs: list[EvalRun], output_path: Path) -> None:
    """Export runs to CSV for further analysis.

    Args:
        runs: List of evaluation runs
        output_path: Path to CSV output file
    """
    if not runs:
        return

    fieldnames = [
        "run_id",
        "timestamp",
        "harness",
        "model",
        "rules_variant",
        "scaffold_template",
        "scaffold_version",
        "task_name",
        "duration_sec",
        "terminated_early",
        "functional_passed",
        "build_succeeded",
        "tests_passed",
        "tests_total",
        "compliance_score",
        "visual_similarity",
        "efficiency_score",
        "quality_score",
        "diagnostic_score",
        "voided",
        "void_reasons",
        "qualified",
        "optimization_score",
        "gate_failures",
        "repeat_failures",
        "failed_command_categories",
        "first_pass_verification_successes",
        "first_pass_verification_failures",
        "missing_required_verification_commands",
        "coverage_threshold",
        "coverage_measured",
        "coverage_passed",
        "requirement_presence_ratio",
        "requirement_mapping_ratio",
        "requirement_pattern_gap_count",
        "requirement_pattern_gaps",
        "composite_score",
        "trial_total_sec",
        "environment_setup_sec",
        "agent_setup_sec",
        "agent_execution_sec",
        "verifier_sec",
        "harness_overhead_sec",
        "scaffold_changes",
    ]

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for run in runs:
            process_meta = run.scores.metadata.get("process", {})
            if not isinstance(process_meta, dict):
                process_meta = {}
            harbor_meta = run.scores.metadata.get("harbor", {})
            if not isinstance(harbor_meta, dict):
                harbor_meta = {}
            phase_timings = harbor_meta.get("phase_timings_sec", {})
            if not isinstance(phase_timings, dict):
                phase_timings = {}
            row = {
                "run_id": run.id,
                "timestamp": run.timestamp,
                "harness": run.config.harness,
                "model": run.config.model,
                "rules_variant": run.config.rules_variant,
                "scaffold_template": run.config.scaffold_template,
                "scaffold_version": run.config.scaffold_version,
                "task_name": run.config.task_name,
                "duration_sec": run.duration_sec,
                "terminated_early": run.terminated_early,
                "functional_passed": run.scores.functional.passed,
                "build_succeeded": run.scores.functional.build_succeeded,
                "tests_passed": run.scores.functional.tests_passed,
                "tests_total": run.scores.functional.tests_total,
                "compliance_score": run.scores.compliance.score,
                "visual_similarity": run.scores.visual.similarity if run.scores.visual else None,
                "efficiency_score": run.scores.efficiency.score,
                "quality_score": run.scores.quality_score,
                "diagnostic_score": run.scores.diagnostic_score,
                "voided": run.scores.voided,
                "void_reasons": json.dumps(run.scores.void_reasons),
                "qualified": run.scores.qualification.passed,
                "optimization_score": run.scores.optimization.score,
                "gate_failures": run.scores.efficiency.total_gate_failures,
                "repeat_failures": run.scores.efficiency.repeat_failures,
                "failed_command_categories": json.dumps(
                    process_meta.get("failed_command_categories", {}),
                    sort_keys=True,
                ),
                "first_pass_verification_successes": process_meta.get(
                    "first_pass_verification_successes"
                ),
                "first_pass_verification_failures": process_meta.get(
                    "first_pass_verification_failures"
                ),
                "missing_required_verification_commands": process_meta.get(
                    "missing_required_verification_commands"
                ),
                "coverage_threshold": run.scores.coverage.threshold,
                "coverage_measured": run.scores.coverage.measured,
                "coverage_passed": run.scores.coverage.passed,
                "requirement_presence_ratio": run.scores.requirements.presence_ratio,
                "requirement_mapping_ratio": run.scores.requirements.mapping_ratio,
                "requirement_pattern_gap_count": len(
                    run.scores.requirements.requirement_pattern_gaps
                ),
                "requirement_pattern_gaps": json.dumps(
                    run.scores.requirements.requirement_pattern_gaps,
                    sort_keys=True,
                ),
                "composite_score": run.scores.composite_score,
                "trial_total_sec": phase_timings.get("trial_total_sec"),
                "environment_setup_sec": phase_timings.get("environment_setup_sec"),
                "agent_setup_sec": phase_timings.get("agent_setup_sec"),
                "agent_execution_sec": phase_timings.get("agent_execution_sec"),
                "verifier_sec": phase_timings.get("verifier_sec"),
                "harness_overhead_sec": harbor_meta.get("harness_overhead_sec"),
                "scaffold_changes": len(run.scores.scaffold_audit.changes_from_baseline)
                if run.scores.scaffold_audit
                else 0,
            }
            writer.writerow(row)


def _ranked_runs(runs: list[EvalRun]) -> list[EvalRun]:
    return sorted(
        runs,
        key=lambda run: (run.scores.composite_score, run.scores.diagnostic_score),
        reverse=True,
    )


def _visual_value(run: EvalRun) -> str:
    if not run.scores.visual:
        return "N/A"
    return f"{run.scores.visual.similarity:.2f}"


def _failed_categories(run: EvalRun) -> dict:
    process = run.scores.metadata.get("process", {})
    if not isinstance(process, dict):
        return {}
    categories = process.get("failed_command_categories", {})
    return categories if isinstance(categories, dict) else {}


def _append_summary_table(lines: list[str], runs: list[EvalRun]) -> None:
    lines.extend(
        [
            "\n## Summary Table\n",
            (
                "| Harness | Model | Rules | Void | Qualified | Composite | Diagnostic | "
                "Functional | Compliance | Visual | Efficiency |"
            ),
            (
                "|---------|-------|-------|------|-----------|-----------|------------|"
                "------------|------------|--------|------------|"
            ),
        ]
    )
    for run in _ranked_runs(runs):
        func_status = "PASS" if run.scores.functional.passed else "FAIL"
        lines.append(
            f"| {run.config.harness} | {run.config.model} | {run.config.rules_variant} | "
            f"{run.scores.voided} | {run.scores.qualification.passed} | "
            f"{run.scores.composite_score:.3f} | "
            f"{run.scores.diagnostic_score:.3f} | {func_status} | "
            f"{run.scores.compliance.score:.2f} | {_visual_value(run)} | "
            f"{run.scores.efficiency.score:.2f} |"
        )
        lines.append(
            f"  - run_id={run.id}, optimization={run.scores.optimization.score:.3f}, "
            f"void_reasons={run.scores.void_reasons}, "
            f"coverage_passed={run.scores.coverage.passed}, "
            f"requirement_presence={run.scores.requirements.presence_ratio:.2f}, "
            f"requirement_mapping={run.scores.requirements.mapping_ratio:.2f}, "
            f"failed_categories={_failed_categories(run)}"
        )


def _append_aggregate_section(
    lines: list[str], title: str, groups: dict[str, dict[str, float | int]]
) -> None:
    lines.append(f"\n## {title}")
    for group_name, stats in groups.items():
        lines.append(
            f"- **{group_name}**: {stats['count']} total, {stats['scored_count']} scored, "
            f"{stats['void_count']} void ({stats['void_rate']:.2f}), "
            f"avg score={stats['avg_score']:.3f}, pass_rate={stats['pass_rate']:.2f}, "
            f"score_var={stats['score_variance']:.4f}"
        )


def _append_scaffold_section(lines: list[str], groups: dict[str, dict[str, float | int]]) -> None:
    lines.append("\n## By Scaffold")
    for scaffold_key, stats in groups.items():
        template, version = scaffold_key.split("@", maxsplit=1)
        lines.append(
            f"- **{template} ({version})**: {stats['count']} total, "
            f"{stats['void_count']} void ({stats['void_rate']:.2f}), "
            f"avg score={stats['avg_score']:.3f}, pass_rate={stats['pass_rate']:.2f}, "
            f"score_var={stats['score_variance']:.4f}"
        )


def _append_stability_section(lines: list[str], groups: dict[str, dict[str, float | int]]) -> None:
    lines.append("\n## Stability By Config")
    for config_key, stats in groups.items():
        lines.append(
            f"- `{config_key}`: runs={stats['count']}, scored={stats['scored_count']}, "
            f"void={stats['void_count']} ({stats['void_rate']:.2f}), "
            f"pass_rate={stats['pass_rate']:.2f}, "
            f"score_var={stats['score_variance']:.4f}, "
            f"duration_var={stats['duration_variance_sec']:.3f}"
        )


def _normalized_lower_better(value: float, lower: float, upper: float) -> float:
    if upper <= lower:
        return 1.0
    return 1.0 - ((value - lower) / (upper - lower))


def _append_qualified_cost_time(lines: list[str], runs: list[EvalRun]) -> None:
    qualified_runs = [
        run for run in runs if run.scores.qualification.passed and not run.scores.voided
    ]
    lines.append("\n## Qualified Cost-Time Index")
    if not qualified_runs:
        lines.append("- No qualified runs; cost/time normalization skipped.")
        return

    duration_values = [run.duration_sec for run in qualified_runs]
    token_values = [_uncached_tokens(run) for run in qualified_runs]
    min_duration = min(duration_values)
    max_duration = max(duration_values)
    min_tokens = min(token_values)
    max_tokens = max(token_values)

    ranked = sorted(qualified_runs, key=lambda item: item.scores.optimization.score, reverse=True)
    for run in ranked:
        duration_norm = _normalized_lower_better(run.duration_sec, min_duration, max_duration)
        token_norm = _normalized_lower_better(_uncached_tokens(run), min_tokens, max_tokens)
        index = round((duration_norm + token_norm) / 2, 3)
        lines.append(
            f"- run_id={run.id}, model={run.config.model}, index={index:.3f}, "
            f"duration={run.duration_sec:.1f}s, uncached_tokens={_uncached_tokens(run)}"
        )


def _append_unqualified_diagnostics(lines: list[str], runs: list[EvalRun]) -> None:
    unqualified = [
        run for run in runs if not run.scores.qualification.passed and not run.scores.voided
    ]
    lines.append("\n## Diagnostic Ranking (Unqualified)")
    if not unqualified:
        lines.append("- No unqualified runs.")
        return

    ranked = sorted(unqualified, key=lambda item: item.scores.diagnostic_score, reverse=True)
    for run in ranked:
        lines.append(
            f"- run_id={run.id}, model={run.config.model}, "
            f"diagnostic={run.scores.diagnostic_score:.3f}, "
            f"gaps={run.scores.requirements.requirement_gap_ids}, "
            f"pattern_gaps={run.scores.requirements.requirement_pattern_gaps}"
        )


def _append_void_runs(lines: list[str], runs: list[EvalRun]) -> None:
    voided = [run for run in runs if run.scores.voided]
    lines.append("\n## Void Runs (Repeat Required)")
    if not voided:
        lines.append("- No void runs.")
        return
    for run in voided:
        lines.append(
            f"- run_id={run.id}, model={run.config.model}, reasons={run.scores.void_reasons}, "
            f"termination_reason={run.termination_reason}"
        )


def generate_comparison_report(runs: list[EvalRun]) -> str:
    """Generate a markdown comparison report."""
    if not runs:
        return "# Evaluation Report\n\nNo runs to report."

    lines = [
        "# Evaluation Comparison Report",
        f"\nGenerated: {datetime.now().isoformat()}",
        f"\nTotal runs: {len(runs)}",
    ]
    _append_summary_table(lines, runs)

    agg = aggregate_results(runs)
    _append_aggregate_section(lines, "By Harness", agg.get("by_harness", {}))
    _append_aggregate_section(lines, "By Model", agg.get("by_model", {}))
    _append_aggregate_section(lines, "By Rules Variant", agg.get("by_rules", {}))
    _append_scaffold_section(lines, agg.get("by_scaffold", {}))
    _append_stability_section(lines, agg.get("by_config", {}))
    _append_void_runs(lines, runs)
    _append_qualified_cost_time(lines, runs)
    _append_unqualified_diagnostics(lines, runs)
    return "\n".join(lines)
