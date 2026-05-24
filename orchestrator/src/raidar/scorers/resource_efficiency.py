"""Resource-efficiency scorer implementation."""

from __future__ import annotations

from raidar.schemas.scorecard import MetricScore
from raidar.scorers.base import BaseScorer, ScorerContext, ScorerEvidence, register_scorer
from raidar.scorers.common import metric


@register_scorer(id="resource-efficiency", version=1)
class ResourceEfficiency(BaseScorer):
    """Shared resource-efficiency scorer."""

    status = "active"
    category = "efficiency"
    description = (
        "Scores token, command, failure, and verification-round efficiency after "
        "a valid run completes."
    )
    metrics = (metric("resource-efficiency", "core", 1.0),)

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        score = context.resource_efficiency
        return ScorerEvidence(
            metric_scores=(
                MetricScore(
                    metric_id="resource-efficiency",
                    score=score.score,
                    passed=True,
                    evidence=(
                        f"uncached_input_tokens={score.uncached_input_tokens}, "
                        f"command_count={score.command_count}"
                    ),
                ),
            )
        )
