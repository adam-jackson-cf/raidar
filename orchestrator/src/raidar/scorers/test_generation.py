"""Test-generation scorer definition."""

from __future__ import annotations

from pathlib import Path

from raidar.schemas.scorecard import CoverageScore
from raidar.scorers.base import BaseScorer, ScorerContext, ScorerEvidence, register_scorer
from raidar.scorers.common import (
    assertion_strength_metric_score,
    coverage_lift_metric_score,
    json_action_content,
    metric,
    production_code_guardrail_metric_score,
    requirement_mapping_metric_score,
    verification_stability_from_evidence,
)
from raidar.scorers.verification import (
    EvidenceDependency,
    VerificationPlan,
    VerificationPlanContext,
    read_json,
    scenario_command_plan,
)


@register_scorer(scorer_id="test-generation", version=1)
class TestGeneration(BaseScorer):
    """Test-generation scorer promoted to an active code-backed definition."""

    status = "active"
    category = "quality"
    description = (
        "Scores test-generation tasks by coverage lift, meaningful requirement "
        "mapping, and production-code guardrails."
    )
    metrics = (
        metric(
            "requirement-mapping",
            "core",
            0.25,
            evidence=(
                "Scenario requirements, changed or added tests, test names, assertions, "
                "and requirement IDs where present."
            ),
            score_derivation=(
                "Scores tests mapped to requirements or changed behavior; unmapped tests "
                "count as proxy blind spots."
            ),
            pass_fail=(
                "Passes when generated tests cover declared requirements or changed behavior."
            ),
        ),
        metric(
            "assertion-strength",
            "core",
            0.25,
            evidence=(
                "Assertion count, assertion diversity, snapshot-only checks, empty test "
                "bodies, and skipped tests."
            ),
            score_derivation=(
                "Scores non-empty test bodies, meaningful assertions, assertion diversity, "
                "and absence of skipped or focused tests."
            ),
            pass_fail=(
                "Passes when tests assert behavior with sufficient strength and no "
                "skipped-test shortcuts."
            ),
        ),
        metric(
            "coverage-lift",
            "core",
            0.25,
            evidence=(
                "Starter or baseline coverage, final coverage, threshold, and coverage source."
            ),
            score_derivation=(
                "Scores coverage delta toward threshold; without baseline, final coverage "
                "is capped and labelled proxy."
            ),
            pass_fail="Passes when coverage improves or meets threshold with generated tests.",
        ),
        metric(
            "production-code-guardrail",
            "core",
            0.15,
            evidence=(
                "Production file changes, test file changes, and scenario-declared "
                "allowed production edits."
            ),
            score_derivation=(
                "Scores 1.0 when production code is unchanged or explicitly allowed and "
                "penalizes unapproved production edits."
            ),
            pass_fail=(
                "Passes when test-generation work does not hide behavior changes in "
                "production code."
            ),
        ),
        metric(
            "verification-stability",
            "core",
            0.10,
            evidence="Verification gate failure count across the run.",
            score_derivation="Uses the verification stability score computed from gate history.",
            pass_fail="Passes when verification stability is greater than zero.",
        ),
    )
    evidence_dependencies = (
        EvidenceDependency(kind="command"),
        EvidenceDependency(kind="read_json", minimum=2),
    )

    def verification_plan(self, context: VerificationPlanContext) -> VerificationPlan:
        """Declare verification commands and common coverage report inputs."""

        base = scenario_command_plan(self.id, context)
        return VerificationPlan(
            owner=self.id,
            actions=(
                *base.actions,
                read_json("coverage.python", ".raidar/coverage.json", required=False),
                read_json(
                    "coverage.typescript",
                    "coverage/coverage-summary.json",
                    required=False,
                ),
            ),
        )

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        workspace = Path(context.workspace)
        evidence = context.verification_evidence.for_owner(self.id)
        return ScorerEvidence(
            metric_scores=(
                requirement_mapping_metric_score(workspace, context.scenario),
                assertion_strength_metric_score(workspace),
                coverage_lift_metric_score(_coverage_from_evidence(evidence, context.scenario)),
                production_code_guardrail_metric_score(workspace, context.workspace_changes),
                verification_stability_from_evidence(evidence),
            )
        )


def _coverage_from_evidence(evidence, scenario) -> CoverageScore:
    measured = _coverage_value(json_action_content(evidence, "coverage.python"))
    source = "test-generation.coverage.python"
    if measured is None:
        measured = _coverage_value(json_action_content(evidence, "coverage.typescript"))
        source = "test-generation.coverage.typescript"
    threshold = scenario.verification.coverage_threshold
    passed = threshold is None or threshold <= 0 or (measured is not None and measured >= threshold)
    return CoverageScore(
        threshold=threshold,
        measured=measured,
        source=source if measured is not None else None,
        passed=passed,
    )


def _coverage_value(content) -> float | None:
    if not isinstance(content, dict):
        return None
    totals = content.get("totals")
    value = totals.get("percent_covered") if isinstance(totals, dict) else None
    if isinstance(value, int | float) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value) / 100.0))
    total = content.get("total")
    lines = total.get("lines") if isinstance(total, dict) else None
    pct = lines.get("pct") if isinstance(lines, dict) else None
    if isinstance(pct, int | float) and not isinstance(pct, bool):
        return max(0.0, min(1.0, float(pct) / 100.0))
    return None
