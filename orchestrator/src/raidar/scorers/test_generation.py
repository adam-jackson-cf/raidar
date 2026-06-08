"""Test-generation scorer definition."""

from __future__ import annotations

from pathlib import Path

from raidar.scorers.base import BaseScorer, ScorerContext, ScorerEvidence, register_scorer
from raidar.scorers.common import (
    assertion_strength_metric_score,
    coverage_lift_metric_score,
    metric,
    production_code_guardrail_metric_score,
    requirement_mapping_metric_score,
    verification_stability_score,
)


@register_scorer(scorer_id="test-generation", version=1)
class TestGeneration(BaseScorer):
    """Test-generation scorer retained as a proposed code-backed definition."""

    status = "proposed"
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

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        workspace = Path(context.workspace)
        outputs = context.execution.outputs
        return ScorerEvidence(
            metric_scores=(
                requirement_mapping_metric_score(workspace, context.scenario),
                assertion_strength_metric_score(workspace),
                coverage_lift_metric_score(outputs),
                production_code_guardrail_metric_score(workspace, context.workspace_changes),
                verification_stability_score(outputs.verification_stability),
            )
        )
