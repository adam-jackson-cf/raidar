"""Plan-to-code scorer definition."""

from __future__ import annotations

from raidar.scorers.base import BaseScorer, register_scorer
from raidar.scorers.common import metric


@register_scorer(id="plan-to-code", version=1)
class PlanToCode(BaseScorer):
    """Plan-to-code scorer retained as a proposed code-backed definition."""

    status = "proposed"
    category = "quality"
    description = (
        "Scores implementation against an approved plan, including plan quality "
        "and implementation drift."
    )
    metrics = (
        metric(
            "plan-quality",
            "llm-as-judge",
            0.45,
            config={"judge": "judges/plan-judge.toml"},
        ),
        metric("functional", "core", 0.20),
        metric("verification-stability", "core", 0.15),
        metric(
            "artifact-checks",
            "artifact-checks",
            0.20,
            config={"required_paths": ["src/**"], "path_match": "glob"},
        ),
    )
