"""Base definitions for the code-task scorer family."""

from __future__ import annotations

from typing import ClassVar

from raidar.schemas.scenario import ScorerMetricDefinition
from raidar.scorers.base import BaseScorer, register_scorer


def metric(
    metric_id: str,
    metric_type: str,
    weight: float,
    *,
    config: dict | None = None,
) -> ScorerMetricDefinition:
    return ScorerMetricDefinition(
        id=metric_id,
        type=metric_type,
        weight=weight,
        config=config or {},
    )


class CodeTaskScorer(BaseScorer):
    """Base class for scorers in the code-task family."""

    family: ClassVar[str] = "code-task"


CODE_TASK_METRICS = (
    metric("functional", "core", 0.30),
    metric("code-quality", "core", 0.25),
    metric("test-coverage", "core", 0.20),
    metric(
        "artifact-checks",
        "artifact-checks",
        0.15,
        config={"required_paths": ["src/**"], "path_match": "glob"},
    ),
    metric("verification-stability", "core", 0.10),
)


@register_scorer(id="code-task", version=1)
class CodeTask(CodeTaskScorer):
    """Generic code-task scorer family and default metric interface."""

    status = "proposed"
    category = "quality"
    description = (
        "Scores code tasks against functional correctness, code quality, coverage, "
        "artifacts, and verification stability."
    )
    metrics = CODE_TASK_METRICS
