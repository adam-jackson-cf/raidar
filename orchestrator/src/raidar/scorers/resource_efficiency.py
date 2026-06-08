"""Resource-efficiency scorer implementation."""

from __future__ import annotations

from raidar.schemas.scorecard import MetricScore
from raidar.scorers.base import BaseScorer, ScorerContext, ScorerEvidence, register_scorer
from raidar.scorers.common import metric


@register_scorer(scorer_id="resource-efficiency", version=1)
class ResourceEfficiency(BaseScorer):
    """Shared resource-efficiency scorer."""

    status = "active"
    category = "efficiency"
    description = (
        "Scores token, command, failure, and verification-round efficiency after "
        "a valid run completes."
    )
    metrics = (
        metric(
            "resource-efficiency",
            "core",
            1.0,
            evidence="Token, command, failure, and verification-round counts.",
            score_derivation="Uses the normalized resource-efficiency score for the run.",
            pass_fail="Passes when the run is valid; lower resource use improves ranking.",
        ),
    )

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
