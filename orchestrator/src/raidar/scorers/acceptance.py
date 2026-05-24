"""Acceptance scorer implementation."""

from __future__ import annotations

from pathlib import Path

from raidar.schemas.scorecard import MetricScore
from raidar.scorers.base import BaseScorer, ScorerContext, ScorerEvidence, register_scorer
from raidar.scorers.common import metric
from raidar.scorers.deterministic import run_deterministic_check, score_acceptance_checks


@register_scorer(id="acceptance", version=1)
class Acceptance(BaseScorer):
    """Scenario acceptance scorer backed by deterministic scenario checks."""

    status = "active"
    category = "quality"
    description = "Scores deterministic scenario acceptance checks."
    metrics = (metric("acceptance", "core", 1.0),)

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        checks = [
            run_deterministic_check(check, Path(context.workspace))
            for check in context.scenario.acceptance.deterministic_checks
        ]
        score = score_acceptance_checks(checks)
        return ScorerEvidence(
            metric_scores=(
                MetricScore(
                    metric_id="acceptance",
                    score=score,
                    passed=score >= 1.0,
                    matched_count=sum(1 for check in checks if check.passed),
                    missing_patterns=[check.rule for check in checks if not check.passed],
                    evidence=f"checks={sum(1 for check in checks if check.passed)}/{len(checks)}",
                ),
            )
        )
