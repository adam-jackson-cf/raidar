"""Requirements scorer implementation."""

from __future__ import annotations

from pathlib import Path

from raidar.schemas.scorecard import MetricScore
from raidar.scorers.base import BaseScorer, ScorerContext, ScorerEvidence, register_scorer
from raidar.scorers.common import metric
from raidar.scorers.deterministic import run_deterministic_check
from raidar.scorers.llm_as_judge import evaluate_llm_as_judge_metric


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
        metric("requirements-coverage", "core", 0.35),
        metric(
            "requirements-adherence",
            "llm-as-judge",
            0.65,
            config={"judge": "judges/requirements-adherence.toml"},
        ),
    )

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        judge_metric = self.metrics[1]
        return ScorerEvidence(
            metric_scores=(
                _requirements_coverage_score(Path(context.workspace), context.scenario),
                evaluate_llm_as_judge_metric(
                    workspace=Path(context.workspace),
                    scenario_dir=Path(context.scenario_dir),
                    scenario=context.scenario,
                    metric_id=judge_metric.id,
                    judge_path=judge_metric.config["judge"],
                ),
            )
        )


def _requirements_coverage_score(workspace: Path, scenario) -> MetricScore:
    acceptance = getattr(scenario, "acceptance", None)
    requirements = list(getattr(acceptance, "requirements", ()))
    if not requirements:
        return MetricScore(
            metric_id="requirements-coverage",
            score=1.0,
            passed=True,
            evidence="requirements=0",
        )
    checks = [run_deterministic_check(requirement.check, workspace) for requirement in requirements]
    passed_count = sum(1 for check in checks if check.passed)
    score = passed_count / len(checks)
    return MetricScore(
        metric_id="requirements-coverage",
        score=score,
        passed=score >= 1.0,
        matched_count=passed_count,
        missing_patterns=[
            requirement.id
            for requirement, check in zip(requirements, checks, strict=True)
            if not check.passed
        ],
        evidence=f"requirements={passed_count}/{len(checks)}",
    )
