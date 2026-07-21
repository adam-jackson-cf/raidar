"""Test-generation scorer definition."""

from __future__ import annotations

from pathlib import Path

from raidar.schemas.scorecard import MetricScore
from raidar.scorers.base import BaseScorer, ScorerContext, ScorerEvidence, register_scorer
from raidar.scorers.common import (
    assertion_strength_metric_score,
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
                "Paired starter and final coverage artifacts, threshold, and coverage source."
            ),
            score_derivation=(
                "Scores the measured starter-to-final coverage increase toward the threshold; "
                "both artifacts must use the same coverage source."
            ),
            pass_fail="Passes when final coverage is greater than paired starter coverage.",
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
        final_evidence = context.verification_evidence.for_owner(self.id)
        baseline_evidence = context.baseline_verification_evidence.for_owner(self.id)
        return ScorerEvidence(
            metric_scores=(
                requirement_mapping_metric_score(workspace, context.scenario),
                assertion_strength_metric_score(workspace),
                _coverage_lift_metric_score(
                    baseline_evidence,
                    final_evidence,
                    context.scenario.verification.coverage_threshold,
                ),
                _production_code_guardrail_metric_score(
                    workspace,
                    context.workspace_changes,
                ),
                verification_stability_from_evidence(final_evidence),
            )
        )


def _coverage_lift_metric_score(
    baseline_evidence,
    final_evidence,
    threshold: float | None,
) -> MetricScore:
    baseline = _coverage_from_evidence(baseline_evidence)
    final = _coverage_from_evidence(final_evidence)
    if baseline is None or final is None or baseline[0] != final[0]:
        return MetricScore(
            metric_id="coverage-lift",
            score=0.0,
            passed=False,
            missing_patterns=["paired baseline and final coverage artifacts"],
            evidence="paired baseline and final coverage artifacts are required",
        )

    source, baseline_measured = baseline
    _, final_measured = final
    lift = final_measured - baseline_measured
    if lift <= 0:
        return MetricScore(
            metric_id="coverage-lift",
            score=0.0,
            passed=False,
            evidence=(
                f"source={source}, baseline={baseline_measured}, final={final_measured}, "
                "coverage_lift=0.0"
            ),
        )

    if threshold is None or threshold <= baseline_measured:
        score = lift
    else:
        score = lift / (threshold - baseline_measured)
    return MetricScore(
        metric_id="coverage-lift",
        score=round(max(0.0, min(1.0, score)), 3),
        passed=True,
        evidence=(
            f"source={source}, baseline={baseline_measured}, final={final_measured}, "
            f"coverage_lift={lift}"
        ),
    )


def _production_code_guardrail_metric_score(
    workspace: Path,
    workspace_changes: dict | None,
) -> MetricScore:
    changed_files = (
        workspace_changes.get("changed_files") if isinstance(workspace_changes, dict) else None
    )
    has_direct_evidence = (
        isinstance(changed_files, list)
        and all(isinstance(path, str) and path for path in changed_files)
        and workspace_changes.get("error") in (None, "")
    )
    if not has_direct_evidence:
        return MetricScore(
            metric_id="production-code-guardrail",
            score=0.0,
            passed=False,
            missing_patterns=["changed-file evidence"],
            evidence="changed-file evidence is required to establish protected production integrity",
        )
    return production_code_guardrail_metric_score(workspace, workspace_changes)


def _coverage_from_evidence(evidence) -> tuple[str, float] | None:
    artifacts = (
        ("test-generation.coverage.python", json_action_content(evidence, "coverage.python")),
        (
            "test-generation.coverage.typescript",
            json_action_content(evidence, "coverage.typescript"),
        ),
    )
    available = [
        (source, _coverage_value(content)) for source, content in artifacts if content is not None
    ]
    if len(available) != 1:
        return None
    source, measured = available[0]
    return (source, measured) if measured is not None else None


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
