"""Test-generation scorer definition."""

from __future__ import annotations

from raidar.scorers.base import BaseScorer, register_scorer
from raidar.scorers.common import metric


@register_scorer(id="test-generation", version=1)
class TestGeneration(BaseScorer):
    """Test-generation scorer retained as a proposed code-backed definition."""

    status = "proposed"
    category = "quality"
    description = (
        "Scores test-generation tasks by coverage lift, meaningful requirement "
        "mapping, and production-code guardrails."
    )
    metrics = (
        metric("test-coverage", "core", 0.50),
        metric("functional", "core", 0.30),
        metric("verification-stability", "core", 0.20),
    )
