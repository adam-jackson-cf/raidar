"""Canonical scorer implementation registration."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

from raidar.schemas.scenario import ScorerMetricDefinition
from raidar.schemas.scorecard import MetricScore
from raidar.scorers.base import BaseScorer, ScorerContext, ScorerEvidence, register_scorer
from raidar.scoring.acceptance import evaluate_llm_as_judge_metric

import_module("raidar.scorers.code_task")


def _metric(
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


def _functional_metric_score(outputs) -> MetricScore:
    return MetricScore(
        metric_id="functional",
        score=outputs.functional.score,
        passed=outputs.functional.passed,
        evidence=(
            f"build={outputs.functional.build_succeeded}, "
            f"tests={outputs.functional.tests_passed}/{outputs.functional.tests_total}"
        ),
    )


def _verification_stability_metric_score(outputs) -> MetricScore:
    score = outputs.verification_stability
    return MetricScore(
        metric_id="verification-stability",
        score=score.score,
        passed=score.score > 0,
        evidence=f"failures={score.total_gate_failures}",
    )


def _test_coverage_metric_score(outputs) -> MetricScore:
    coverage = outputs.test_coverage
    return MetricScore(
        metric_id="test-coverage",
        score=_coverage_metric_score(coverage),
        passed=coverage.passed,
        evidence=(
            f"threshold={coverage.threshold}, "
            f"measured={coverage.measured}, "
            f"source={coverage.source}"
        ),
    )


def _coverage_metric_score(score) -> float:
    if score.threshold is None:
        return 1.0 if score.passed else 0.0
    if score.measured is None:
        return 0.0
    return min(1.0, score.measured / score.threshold)


def _visual_regression_metric_score(outputs) -> MetricScore:
    if outputs.visual is None:
        return MetricScore(
            metric_id="visual-regression",
            score=0.0,
            passed=False,
            evidence="Visual threshold not configured.",
        )
    return MetricScore(
        metric_id="visual-regression",
        score=outputs.visual.score,
        passed=bool(outputs.visual.passed),
        evidence=f"similarity={outputs.visual.score}, passed={outputs.visual.passed}",
    )


def _artifact_metric_score(workspace: Path, required_artifacts: tuple[str, ...]) -> MetricScore:
    missing = _missing_required_artifacts(workspace, required_artifacts)
    matched_count = len(required_artifacts) - len(missing)
    score = 1.0 if not required_artifacts else matched_count / len(required_artifacts)
    return MetricScore(
        metric_id="artifact-checks",
        score=round(score, 3),
        passed=score >= 1.0,
        matched_count=matched_count,
        missing_patterns=missing,
        evidence=f"required_artifacts={len(required_artifacts)}, matched={matched_count}",
    )


def _required_artifact_patterns(scenario, scorer_id: str) -> tuple[str, ...]:
    patterns: list[str] = []
    for scorer_ref in getattr(scenario, "scorers", ()):
        if getattr(scorer_ref, "id", None) != scorer_id:
            continue
        artifact_config = getattr(scorer_ref, "config", {}).get("artifact-checks", {})
        required_paths = artifact_config.get("required_paths", [])
        if isinstance(required_paths, list):
            patterns.extend(path for path in required_paths if isinstance(path, str))
    return tuple(dict.fromkeys(patterns))


def _missing_required_artifacts(workspace: Path, patterns: tuple[str, ...]) -> list[str]:
    return [
        pattern
        for pattern in patterns
        if not any(path.is_file() for path in workspace.glob(pattern))
    ]


@register_scorer(id="resource-efficiency", version=1)
class ResourceEfficiency(BaseScorer):
    """Shared resource-efficiency scorer."""

    status = "active"
    category = "efficiency"
    description = (
        "Scores token, command, failure, and verification-round efficiency after "
        "a valid run completes."
    )
    metrics = (_metric("resource-efficiency", "core", 1.0),)

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
        _metric(
            "requirements-adherence",
            "llm-as-judge",
            1.0,
            config={"judge": "judges/requirements-adherence.toml"},
        ),
    )

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        metric = self.metrics[0]
        return ScorerEvidence(
            metric_scores=(
                evaluate_llm_as_judge_metric(
                    workspace=Path(context.workspace),
                    scenario_dir=Path(context.scenario_dir),
                    scenario=context.scenario,
                    metric_id=metric.id,
                    judge_path=metric.config["judge"],
                ),
            )
        )


@register_scorer(id="design-to-code", version=1)
class DesignToCode(BaseScorer):
    """Design-to-code scorer retained as a code-backed definition."""

    status = "active"
    category = "quality"
    description = (
        "Scores visual design implementation tasks against reference evidence, "
        "functional evidence, artifacts, and verification quality."
    )
    metrics = (
        _metric("visual-regression", "core", 0.34),
        _metric("functional", "core", 0.24),
        _metric("test-coverage", "core", 0.15),
        _metric("verification-stability", "core", 0.10),
        _metric(
            "artifact-checks",
            "artifact-checks",
            0.17,
            config={"required_paths": ["src/app/page.tsx"], "path_match": "glob"},
        ),
    )

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        outputs = context.execution.outputs
        return ScorerEvidence(
            metric_scores=(
                _visual_regression_metric_score(outputs),
                _functional_metric_score(outputs),
                _test_coverage_metric_score(outputs),
                _verification_stability_metric_score(outputs),
                _artifact_metric_score(
                    Path(context.workspace),
                    _required_artifact_patterns(context.scenario, self.id),
                ),
            )
        )


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
        _metric(
            "plan-quality",
            "llm-as-judge",
            0.45,
            config={"judge": "judges/plan-judge.toml"},
        ),
        _metric("functional", "core", 0.20),
        _metric("verification-stability", "core", 0.15),
        _metric(
            "artifact-checks",
            "artifact-checks",
            0.20,
            config={"required_paths": ["src/**"], "path_match": "glob"},
        ),
    )


@register_scorer(id="bugfix", version=1)
class Bugfix(BaseScorer):
    """Bugfix scorer retained as a proposed code-backed definition."""

    status = "proposed"
    category = "quality"
    description = (
        "Scores targeted defect fixes with regression coverage, minimal unrelated "
        "drift, and clean verification."
    )
    metrics = (
        _metric("functional", "core", 0.40),
        _metric("test-coverage", "core", 0.30),
        _metric("verification-stability", "core", 0.30),
    )


@register_scorer(id="refactor", version=1)
class Refactor(BaseScorer):
    """Refactor scorer retained as a proposed code-backed definition."""

    status = "proposed"
    category = "quality"
    description = (
        "Scores behavior-preserving refactors with structural improvement and "
        "verification confidence."
    )
    metrics = (
        _metric("functional", "core", 0.40),
        _metric("test-coverage", "core", 0.25),
        _metric("verification-stability", "core", 0.35),
    )


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
        _metric("test-coverage", "core", 0.50),
        _metric("functional", "core", 0.30),
        _metric("verification-stability", "core", 0.20),
    )
