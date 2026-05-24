"""Bugfix scorer definition for the code-task family."""

from __future__ import annotations

from raidar.scorers.base import register_scorer
from raidar.scorers.code_task.base import CodeTaskScorer
from raidar.scorers.common import metric


@register_scorer(id="bugfix", version=1)
class Bugfix(CodeTaskScorer):
    """Bugfix scorer retained as a proposed code-backed definition."""

    status = "proposed"
    category = "quality"
    extends = "code-task"
    description = (
        "Scores targeted defect fixes with regression coverage, minimal unrelated "
        "drift, and clean verification."
    )
    metrics = (
        metric("functional", "core", 0.40),
        metric("test-coverage", "core", 0.30),
        metric("verification-stability", "core", 0.30),
    )
