"""Requirements scorer implementation."""

from __future__ import annotations

from pathlib import Path

from raidar.scorers.base import BaseScorer, ScorerContext, ScorerEvidence, register_scorer
from raidar.scorers.common import metric
from raidar.scoring.acceptance import evaluate_llm_as_judge_metric


@register_scorer(id="requirements", version=1)
class Requirements(BaseScorer):
    """Shared requirements-domain scorer backed by requirement-specific metrics."""

    status = "active"
    category = "quality"
    description = (
        "Scores the requirements domain, including semantic adherence when "
        "deterministic checks cannot fully prove intent."
    )
    metrics = (
        metric(
            "requirements-adherence",
            "llm-as-judge",
            1.0,
            config={"judge": "judges/requirements-adherence.toml"},
        ),
    )

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        scorer_metric = self.metrics[0]
        return ScorerEvidence(
            metric_scores=(
                evaluate_llm_as_judge_metric(
                    workspace=Path(context.workspace),
                    scenario_dir=Path(context.scenario_dir),
                    scenario=context.scenario,
                    metric_id=scorer_metric.id,
                    judge_path=scorer_metric.config["judge"],
                ),
            )
        )
