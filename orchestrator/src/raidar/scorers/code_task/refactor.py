"""Refactor scorer definition for the code-task family."""

from __future__ import annotations

from raidar.scorers.base import register_scorer
from raidar.scorers.code_task.base import CodeTaskScorer
from raidar.scorers.common import metric


@register_scorer(id="refactor", version=1)
class Refactor(CodeTaskScorer):
    """Refactor scorer retained as a proposed code-backed definition."""

    status = "proposed"
    category = "quality"
    extends = "code-task"
    description = (
        "Scores behavior-preserving refactors with structural improvement and "
        "verification confidence."
    )
    metrics = (
        metric("functional", "core", 0.40),
        metric("test-coverage", "core", 0.25),
        metric("verification-stability", "core", 0.35),
    )
