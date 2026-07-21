"""Design-to-code scorer implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from raidar.schemas.scorecard import MetricScore
from raidar.scorers.base import BaseScorer, ScorerContext, ScorerEvidence, register_scorer
from raidar.scorers.common import (
    artifact_metric_score,
    functional_score_from_evidence,
    json_action_content,
    metric,
    required_artifact_patterns,
    verification_stability_from_evidence,
)
from raidar.scorers.evidence_contract import VisualDiffEvidenceResult
from raidar.scorers.verification import (
    EvidenceDependency,
    VerificationPlan,
    VerificationPlanContext,
    command,
    glob,
    read_json,
)


@register_scorer(scorer_id="design-to-code", version=1)
class DesignToCode(BaseScorer):
    """Design-to-code scorer retained as a code-backed definition."""

    status = "active"
    category = "quality"
    description = (
        "Scores visual design implementation tasks against reference evidence, "
        "functional evidence, artifacts, and verification quality."
    )
    metrics = (
        metric(
            "visual-regression",
            "core",
            0.34,
            evidence="Captured screenshot comparison against the reference image.",
            score_derivation="Uses the visual score computed from configured visual bands.",
            pass_fail="Passes when the visual pass policy accepts the captured output.",
        ),
        metric(
            "functional",
            "core",
            0.24,
            evidence="Build, test, and gate execution outcomes for the submitted code.",
            score_derivation="Uses the functional score computed from execution outputs.",
            pass_fail="Passes when functional execution passed.",
        ),
        metric(
            "test-coverage",
            "core",
            0.15,
            evidence="Coverage measurement and configured threshold.",
            score_derivation="Divides measured coverage by threshold, capped at 1.0.",
            pass_fail="Passes when the coverage output passed its configured threshold.",
        ),
        metric(
            "verification-stability",
            "core",
            0.10,
            evidence="Verification gate failure count across the run.",
            score_derivation="Uses the verification stability score computed from gate history.",
            pass_fail="Passes when verification stability is greater than zero.",
        ),
        metric(
            "artifact-checks",
            "artifact-checks",
            0.17,
            evidence="Required implementation artifacts in the workspace.",
            score_derivation="Divides matched required artifacts by configured required artifacts.",
            pass_fail="Passes when every required artifact exists.",
            config={"required_paths": ["src/app/page.tsx"], "path_match": "glob"},
        ),
    )
    evidence_dependencies = (
        EvidenceDependency(kind="command", minimum=4),
        EvidenceDependency(kind="read_json"),
    )

    def verification_plan(self, context: VerificationPlanContext) -> VerificationPlan:
        """Declare design-to-code raw evidence actions for this resolved scorer."""

        actions = [
            command("typecheck", ["bun", "run", "typecheck"], required=False),
            command("lint", ["bun", "run", "lint"], required=False),
            command("coverage", ["bun", "run", "test:coverage"], required=False),
            command("build", ["bun", "run", "build"]),
            read_json(
                "coverage.summary",
                "coverage/coverage-summary.json",
                required=False,
                depends_on=("coverage",),
            ),
        ]
        if context.scenario.visual is not None:
            visual = context.scenario.visual
            artifacts = _visual_artifact_paths(visual)
            actions.extend(
                [
                    command("capture", visual.screenshot_command, required=False),
                    context.visual_comparison.action(
                        "visual.homepage",
                        reference=visual.reference_image,
                        actual=artifacts["actual"],
                        diff=artifacts["diff"],
                        regions=[
                            {
                                "name": region.name,
                                "reference_image": region.reference_image,
                                "actual_image": region.actual_image,
                                "diff_image": region.diff_image,
                                "weight": region.weight,
                            }
                            for region in visual.regions
                        ],
                        required=False,
                        depends_on=("capture",),
                    ),
                ]
            )
        for resolved_metric in context.resolved_scorer.metrics:
            if resolved_metric.id == "artifact-checks":
                for index, required_path in enumerate(
                    resolved_metric.config.get("required_paths", [])
                ):
                    actions.append(
                        glob(
                            f"artifacts.required.{index}",
                            str(required_path),
                            required=False,
                        )
                    )
        return VerificationPlan(owner=self.id, actions=tuple(actions))

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        evidence = context.verification_evidence.for_owner(self.id)
        return ScorerEvidence(
            metric_scores=(
                visual_regression_metric_score(context, evidence),
                functional_score_from_evidence(evidence),
                design_to_code_coverage_metric_score(context, evidence),
                verification_stability_from_evidence(evidence),
                artifact_metric_score(
                    Path(context.workspace),
                    required_artifact_patterns(context.scenario, self.id),
                ),
            )
        )


def visual_regression_metric_score(context: ScorerContext, evidence) -> MetricScore:
    visual = getattr(context.scenario, "visual", None)
    if visual is None:
        return _missing_visual_metric()
    action = evidence.action("visual.homepage")
    if action is None or not isinstance(action.result, VisualDiffEvidenceResult):
        return MetricScore(
            metric_id="visual-regression",
            score=0.0,
            passed=False,
            evidence="Visual comparison evidence missing.",
        )
    visual_score = _visual_score_from_result(
        action.result.model_dump(mode="json", by_alias=True), visual
    )
    return MetricScore(
        metric_id="visual-regression",
        score=visual_score["similarity"],
        passed=bool(visual_score["passed"]),
        evidence=(
            f"global={visual_score['global_similarity']}, "
            f"worst_region={visual_score['worst_region_similarity']}, "
            f"policy_score={visual_score['policy_score']}, "
            f"tier={visual_score['fidelity_tier']}"
        ),
    )


def design_to_code_coverage_metric_score(context: ScorerContext, evidence) -> MetricScore:
    threshold = context.scenario.verification.coverage_threshold
    coverage = _coverage_from_evidence(json_action_content(evidence, "coverage.summary"))
    passed = threshold is None or threshold <= 0 or (coverage is not None and coverage >= threshold)
    score = (
        1.0
        if threshold is None or threshold <= 0
        else (0.0 if coverage is None else min(1.0, coverage / threshold))
    )
    return MetricScore(
        metric_id="test-coverage",
        score=round(score, 3),
        passed=passed,
        evidence=f"threshold={threshold}, measured={coverage}, source=evidence",
    )


def _missing_visual_metric() -> MetricScore:
    return MetricScore(
        metric_id="visual-regression",
        score=0.0,
        passed=False,
        evidence="Visual threshold not configured.",
    )


def _visual_artifact_paths(visual: Any) -> dict[str, str]:
    artifact_manifest = getattr(visual, "artifact_manifest", None)
    if artifact_manifest is not None:
        return {
            "actual": artifact_manifest.actual_image,
            "diff": artifact_manifest.diff_image,
        }
    artifacts = getattr(visual, "artifacts", {})
    if isinstance(artifacts, dict):
        return {
            "actual": str(artifacts.get("actual") or "artifacts/actual.png"),
            "diff": str(artifacts.get("diff") or "artifacts/diff.png"),
        }
    return {"actual": "artifacts/actual.png", "diff": "artifacts/diff.png"}


def _coverage_from_evidence(content: Any) -> float | None:
    totals = _coverage_totals(content)
    if not isinstance(totals, dict):
        return None
    return _coverage_pct_from_totals(totals)


def _coverage_totals(content: Any) -> dict[str, Any] | None:
    if not isinstance(content, dict):
        return None
    total = content.get("total")
    return total if isinstance(total, dict) else content.get("totals")


def _coverage_pct_from_totals(totals: dict[str, Any]) -> float | None:
    for key in ("lines", "statements"):
        value = _nested_pct(totals.get(key))
        if value is not None:
            return value
    return _pct_number(totals.get("percent_covered"))


def _nested_pct(value: Any) -> float | None:
    if isinstance(value, dict):
        return _pct_number(value.get("pct"))
    return None


def _pct_number(value: Any) -> float | None:
    if isinstance(value, int | float):
        return max(0.0, min(1.0, float(value) / 100.0))
    return None


def _visual_score_from_result(result: dict[str, Any], visual: Any) -> dict[str, Any]:
    global_compare = result.get("global") if isinstance(result.get("global"), dict) else {}
    global_similarity = _float_or_zero(global_compare.get("similarity"))
    region_scores = _visual_region_scores(
        result.get("regions"), visual.scoring.region_pass_threshold
    )
    expected_region_count = int(result.get("expected_region_count") or 0)
    if region_scores:
        regional_similarity = sum(
            score["normalized_weight"] * score["similarity"] for score in region_scores
        )
        worst_region_similarity = min(score["similarity"] for score in region_scores)
    else:
        regional_similarity = global_similarity
        worst_region_similarity = global_similarity
    passing_regions = sum(1 for score in region_scores if score["decent_pass"])
    region_pass_rate = (
        1.0 if expected_region_count == 0 else passing_regions / expected_region_count
    )
    policy_score = _visual_policy_score(
        global_similarity=global_similarity,
        regional_similarity=regional_similarity,
        worst_region_similarity=worst_region_similarity,
        region_pass_rate=region_pass_rate,
        scoring=visual.scoring,
    )
    passed, tier = _visual_policy_outcome(
        global_similarity=global_similarity,
        worst_region_similarity=worst_region_similarity,
        region_pass_rate=region_pass_rate,
        policy_score=policy_score,
        pass_policy=visual.pass_policy,
    )
    return {
        "similarity": round(max(0.0, min(1.0, policy_score / 100.0)), 3),
        "global_similarity": round(global_similarity, 3),
        "regional_similarity": round(regional_similarity, 3),
        "worst_region_similarity": round(worst_region_similarity, 3),
        "region_decent_pass_rate": round(region_pass_rate, 3),
        "policy_score": round(policy_score, 3),
        "passed": passed,
        "fidelity_tier": tier,
        "regional_scores": region_scores,
    }


def _visual_region_scores(regions: Any, threshold: float) -> list[dict[str, Any]]:
    if not isinstance(regions, list):
        return []
    scores = []
    for region in regions:
        if not isinstance(region, dict) or not isinstance(region.get("comparison"), dict):
            continue
        similarity = _float_or_zero(region["comparison"].get("similarity"))
        scores.append(
            {
                "name": str(region.get("name", "")),
                "weight": _float_or_zero(region.get("weight")),
                "normalized_weight": _float_or_zero(region.get("normalized_weight")),
                "similarity": similarity,
                "decent_pass": similarity >= threshold,
            }
        )
    return scores


def _visual_policy_score(
    *,
    global_similarity: float,
    regional_similarity: float,
    worst_region_similarity: float,
    region_pass_rate: float,
    scoring: Any,
) -> float:
    gamma = scoring.gamma
    return 100.0 * (
        _scoring_component(global_similarity, scoring.bands.global_band, gamma)
        * scoring.weights.global_weight
        + _scoring_component(regional_similarity, scoring.bands.regional, gamma)
        * scoring.weights.regional
        + _scoring_component(worst_region_similarity, scoring.bands.worst_region, gamma)
        * scoring.weights.worst_region
        + max(0.0, min(1.0, region_pass_rate)) * scoring.weights.region_pass_rate
    )


def _scoring_component(value: float, band: Any, gamma: float) -> float:
    normalized = (value - band.lower) / max(1e-9, band.upper - band.lower)
    return max(0.0, min(1.0, normalized)) ** gamma


def _visual_policy_outcome(
    *,
    global_similarity: float,
    worst_region_similarity: float,
    region_pass_rate: float,
    policy_score: float,
    pass_policy: Any,
) -> tuple[bool, str]:
    if (
        global_similarity < pass_policy.fail_if_global_below
        or worst_region_similarity < pass_policy.fail_if_worst_region_below
    ):
        return False, "failed"
    passed = (
        policy_score >= pass_policy.minimum_score
        and region_pass_rate >= pass_policy.minimum_region_pass_rate
        and worst_region_similarity >= pass_policy.minimum_worst_region
    )
    if not passed:
        return False, "failed"
    high_fidelity = (
        policy_score >= pass_policy.high_fidelity_score
        and global_similarity >= pass_policy.high_fidelity_global
        and worst_region_similarity >= pass_policy.high_fidelity_worst_region
    )
    return True, "high_fidelity" if high_fidelity else "passed"


def _float_or_zero(value: Any) -> float:
    return float(value) if isinstance(value, int | float) else 0.0
