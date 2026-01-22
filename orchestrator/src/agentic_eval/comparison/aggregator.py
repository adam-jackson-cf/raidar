"""Results aggregation and comparison."""

import csv
import json
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any

from ..schemas.scorecard import Scorecard


@dataclass
class ComparisonRow:
    """A row in the comparison table."""

    run_id: str
    task: str
    agent: str
    model: str
    rules: str
    functional: float
    compliance: float
    visual: float
    efficiency: float
    composite: float
    gates_passed: int
    gates_total: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ComparisonReport:
    """Comparison report with aggregated statistics."""

    rows: list[ComparisonRow]
    task: str

    @property
    def best_composite(self) -> ComparisonRow | None:
        """Get the row with the best composite score."""
        if not self.rows:
            return None
        return max(self.rows, key=lambda r: r.composite)

    @property
    def best_by_dimension(self) -> dict[str, ComparisonRow | None]:
        """Get best row for each scoring dimension."""
        if not self.rows:
            return {}
        return {
            "functional": max(self.rows, key=lambda r: r.functional),
            "compliance": max(self.rows, key=lambda r: r.compliance),
            "visual": max(self.rows, key=lambda r: r.visual),
            "efficiency": max(self.rows, key=lambda r: r.efficiency),
            "composite": max(self.rows, key=lambda r: r.composite),
        }

    def averages_by_agent(self) -> dict[str, dict[str, float]]:
        """Calculate average scores grouped by agent."""
        by_agent: dict[str, list[ComparisonRow]] = {}
        for row in self.rows:
            if row.agent not in by_agent:
                by_agent[row.agent] = []
            by_agent[row.agent].append(row)

        result: dict[str, dict[str, float]] = {}
        for agent, rows in by_agent.items():
            result[agent] = {
                "functional": sum(r.functional for r in rows) / len(rows),
                "compliance": sum(r.compliance for r in rows) / len(rows),
                "visual": sum(r.visual for r in rows) / len(rows),
                "efficiency": sum(r.efficiency for r in rows) / len(rows),
                "composite": sum(r.composite for r in rows) / len(rows),
            }
        return result

    def averages_by_rules(self) -> dict[str, dict[str, float]]:
        """Calculate average scores grouped by rules variant."""
        by_rules: dict[str, list[ComparisonRow]] = {}
        for row in self.rows:
            if row.rules not in by_rules:
                by_rules[row.rules] = []
            by_rules[row.rules].append(row)

        result: dict[str, dict[str, float]] = {}
        for rules, rows in by_rules.items():
            result[rules] = {
                "functional": sum(r.functional for r in rows) / len(rows),
                "compliance": sum(r.compliance for r in rows) / len(rows),
                "visual": sum(r.visual for r in rows) / len(rows),
                "efficiency": sum(r.efficiency for r in rows) / len(rows),
                "composite": sum(r.composite for r in rows) / len(rows),
            }
        return result


class ResultsAggregator:
    """Aggregates and compares evaluation results."""

    def __init__(self, results_dir: Path) -> None:
        self.results_dir = results_dir

    def load_results(self, task_filter: str | None = None) -> list[Scorecard]:
        """Load all scorecards from results directory.

        Args:
            task_filter: Optional task name to filter by

        Returns:
            List of Scorecard objects
        """
        scorecards: list[Scorecard] = []

        for json_file in self.results_dir.glob("*.json"):
            try:
                data = json.loads(json_file.read_text())
                scorecard = Scorecard.model_validate(data)
                if task_filter is None or scorecard.task_name == task_filter:
                    scorecards.append(scorecard)
            except Exception:
                continue

        return scorecards

    def scorecards_to_rows(self, scorecards: list[Scorecard]) -> list[ComparisonRow]:
        """Convert scorecards to comparison rows."""
        return [
            ComparisonRow(
                run_id=sc.run_id,
                task=sc.task_name,
                agent=sc.agent,
                model=sc.model,
                rules=sc.rules_variant,
                functional=sc.functional.score,
                compliance=sc.compliance.score,
                visual=sc.visual.score,
                efficiency=sc.efficiency.score,
                composite=sc.composite_score,
                gates_passed=sc.functional.gates_passed,
                gates_total=sc.functional.gates_total,
                metadata=sc.metadata,
            )
            for sc in scorecards
        ]

    def generate_report(
        self,
        task_filter: str | None = None,
    ) -> ComparisonReport:
        """Generate a comparison report.

        Args:
            task_filter: Optional task name to filter by

        Returns:
            ComparisonReport with aggregated data
        """
        scorecards = self.load_results(task_filter)
        rows = self.scorecards_to_rows(scorecards)

        return ComparisonReport(
            rows=rows,
            task=task_filter or "all",
        )

    def export_csv(
        self,
        report: ComparisonReport,
        output_path: Path | None = None,
    ) -> str:
        """Export comparison report to CSV.

        Args:
            report: ComparisonReport to export
            output_path: Optional path to save CSV file

        Returns:
            CSV string
        """
        output = StringIO()
        writer = csv.writer(output)

        # Header
        writer.writerow(
            [
                "run_id",
                "task",
                "agent",
                "model",
                "rules",
                "functional",
                "compliance",
                "visual",
                "efficiency",
                "composite",
                "gates_passed",
                "gates_total",
            ]
        )

        # Data rows
        for row in sorted(report.rows, key=lambda r: -r.composite):
            writer.writerow(
                [
                    row.run_id,
                    row.task,
                    row.agent,
                    row.model,
                    row.rules,
                    f"{row.functional:.2f}",
                    f"{row.compliance:.2f}",
                    f"{row.visual:.2f}",
                    f"{row.efficiency:.2f}",
                    f"{row.composite:.2f}",
                    row.gates_passed,
                    row.gates_total,
                ]
            )

        csv_str = output.getvalue()

        if output_path:
            output_path.write_text(csv_str)

        return csv_str


def aggregate_results(
    results_dir: Path,
    task_filter: str | None = None,
) -> ComparisonReport:
    """Convenience function to aggregate results.

    Args:
        results_dir: Path to results directory
        task_filter: Optional task name to filter by

    Returns:
        ComparisonReport
    """
    aggregator = ResultsAggregator(results_dir)
    return aggregator.generate_report(task_filter)
