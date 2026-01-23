"""Run storage and aggregation for evaluation results."""

import csv
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
    output_path = results_dir / f"{run.id}.json"

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
    for json_file in results_dir.glob("*.json"):
        try:
            runs.append(load_run(json_file))
        except Exception:
            continue  # Skip invalid files
    return sorted(runs, key=lambda r: r.timestamp)


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

    for run in runs:
        harness = run.config.harness
        model = run.config.model
        rules = run.config.rules_variant
        scaffold_key = f"{run.config.scaffold_template}@{run.config.scaffold_version}"

        by_harness.setdefault(harness, []).append(run)
        by_model.setdefault(model, []).append(run)
        by_rules.setdefault(rules, []).append(run)
        by_scaffold.setdefault(scaffold_key, []).append(run)

    def avg_score(runs_list: list[EvalRun]) -> float:
        if not runs_list:
            return 0.0
        return sum(r.scores.composite_score for r in runs_list) / len(runs_list)

    return {
        "total_runs": len(runs),
        "by_harness": {
            h: {"count": len(r), "avg_score": avg_score(r)} for h, r in by_harness.items()
        },
        "by_model": {m: {"count": len(r), "avg_score": avg_score(r)} for m, r in by_model.items()},
        "by_rules": {v: {"count": len(r), "avg_score": avg_score(r)} for v, r in by_rules.items()},
        "by_scaffold": {
            key: {"count": len(r), "avg_score": avg_score(r)} for key, r in by_scaffold.items()
        },
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
        "gate_failures",
        "repeat_failures",
        "composite_score",
        "scaffold_changes",
    ]

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for run in runs:
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
                "gate_failures": run.scores.efficiency.total_gate_failures,
                "repeat_failures": run.scores.efficiency.repeat_failures,
                "composite_score": run.scores.composite_score,
                "scaffold_changes": len(run.scores.scaffold_audit.changes_from_baseline)
                if run.scores.scaffold_audit
                else 0,
            }
            writer.writerow(row)


def generate_comparison_report(runs: list[EvalRun]) -> str:
    """Generate a markdown comparison report.

    Args:
        runs: List of evaluation runs

    Returns:
        Markdown formatted report
    """
    if not runs:
        return "# Evaluation Report\n\nNo runs to report."

    lines = [
        "# Evaluation Comparison Report",
        f"\nGenerated: {datetime.now().isoformat()}",
        f"\nTotal runs: {len(runs)}",
        "\n## Summary Table\n",
        "| Harness | Model | Rules | Composite | Functional | Compliance | Visual | Efficiency |",
        "|---------|-------|-------|-----------|------------|------------|--------|------------|",
    ]

    for run in sorted(runs, key=lambda r: r.scores.composite_score, reverse=True):
        visual = run.scores.visual.similarity if run.scores.visual else "N/A"
        if isinstance(visual, float):
            visual = f"{visual:.2f}"
        func_status = "PASS" if run.scores.functional.passed else "FAIL"
        lines.append(
            f"| {run.config.harness} | {run.config.model} | {run.config.rules_variant} | "
            f"{run.scores.composite_score:.3f} | {func_status} | "
            f"{run.scores.compliance.score:.2f} | {visual} | {run.scores.efficiency.score:.2f} |"
        )

    # Aggregated stats
    agg = aggregate_results(runs)
    lines.extend(
        [
            "\n## By Harness",
        ]
    )
    for harness, stats in agg.get("by_harness", {}).items():
        lines.append(f"- **{harness}**: {stats['count']} runs, avg score: {stats['avg_score']:.3f}")

    lines.extend(
        [
            "\n## By Model",
        ]
    )
    for model, stats in agg.get("by_model", {}).items():
        lines.append(f"- **{model}**: {stats['count']} runs, avg score: {stats['avg_score']:.3f}")

    lines.extend(
        [
            "\n## By Rules Variant",
        ]
    )
    for rules, stats in agg.get("by_rules", {}).items():
        lines.append(f"- **{rules}**: {stats['count']} runs, avg score: {stats['avg_score']:.3f}")

    lines.extend(
        [
            "\n## By Scaffold",
        ]
    )
    for scaffold, stats in agg.get("by_scaffold", {}).items():
        template, version = scaffold.split("@", maxsplit=1)
        lines.append(
            f"- **{template} ({version})**: {stats['count']} runs, "
            f"avg score: {stats['avg_score']:.3f}"
        )

    return "\n".join(lines)
