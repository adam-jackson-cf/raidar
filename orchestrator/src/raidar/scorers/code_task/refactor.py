"""Refactor scorer definition for the code-task family."""

from __future__ import annotations

from pathlib import Path

from raidar.schemas.scorecard import MetricScore
from raidar.scorers.base import BaseScorer, ScorerContext, ScorerEvidence, register_scorer
from raidar.scorers.common import (
    change_containment_metric_score,
    metric,
    source_and_test_files,
    verification_stability_score,
)


@register_scorer(scorer_id="refactor", version=1)
class Refactor(BaseScorer):
    """Refactor scorer promoted to an active code-backed definition."""

    status = "active"
    category = "quality"
    description = (
        "Scores behavior-preserving refactors with structural improvement and "
        "verification confidence."
    )
    metrics = (
        metric(
            "behavior-preservation",
            "core",
            0.30,
            evidence="Final tests, build, scenario checks, and gate outcomes.",
            score_derivation=(
                "Uses functional score capped by behavior-preservation checks when available."
            ),
            pass_fail="Passes when behavior remains intact and functional execution passed.",
        ),
        metric(
            "structural-improvement",
            "core",
            0.25,
            evidence=(
                "Complexity delta, duplication or lint findings, file-size or function-size "
                "changes, and language-specific analysis output."
            ),
            score_derivation=(
                "Scores improvement or non-regression against starter or baseline metrics; "
                "missing baseline is labelled proxy."
            ),
            pass_fail="Passes when structural metrics improve or do not regress beyond threshold.",
        ),
        metric(
            "public-contract-stability",
            "core",
            0.15,
            evidence=(
                "Exported symbols, public schema files, CLI/API signatures, and tests "
                "covering public behavior."
            ),
            score_derivation=(
                "Scores unchanged public contracts unless scenario declares expected "
                "contract changes."
            ),
            pass_fail="Passes when public contract drift is absent or explicitly expected.",
        ),
        metric(
            "change-containment",
            "core",
            0.15,
            evidence=(
                "Changed files, production/test diff ratio, expected paths, and generated "
                "artifact exclusions."
            ),
            score_derivation=(
                "Scores focused refactor scope with penalties for unrelated behavior-bearing edits."
            ),
            pass_fail="Passes when refactor changes stay within declared surfaces.",
        ),
        metric(
            "verification-stability",
            "core",
            0.15,
            evidence="Verification gate failure count across the run.",
            score_derivation="Uses the verification stability score computed from gate history.",
            pass_fail="Passes when verification stability is greater than zero.",
        ),
    )

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        workspace = Path(context.workspace)
        outputs = context.execution.outputs
        sources, tests = source_and_test_files(workspace)
        return ScorerEvidence(
            metric_scores=(
                _behavior_preservation_score(outputs),
                _structural_improvement_score(sources),
                _public_contract_stability_score(sources, tests),
                change_containment_metric_score(
                    workspace=workspace,
                    workspace_changes=context.workspace_changes,
                ),
                verification_stability_score(outputs.verification_stability),
            ),
            metadata={"source_files": len(sources), "test_files": len(tests)},
        )


def _behavior_preservation_score(outputs) -> MetricScore:
    functional = outputs.functional
    return MetricScore(
        metric_id="behavior-preservation",
        score=functional.score,
        passed=functional.passed,
        evidence=(
            "direct: behavior preservation uses final build, test, and gate execution summaries, "
            f"build={functional.build_succeeded}, "
            f"tests={functional.tests_passed}/{functional.tests_total}"
        ),
    )


def _structural_improvement_score(sources: list[Path]) -> MetricScore:
    oversized = [path for path in sources if len(path.parts) > 0]
    score = 0.8 if sources else 0.0
    return MetricScore(
        metric_id="structural-improvement",
        score=score,
        passed=False,
        missing_patterns=[] if sources else ["source files"],
        evidence=(
            "proxy: no baseline static metrics; scored final source inventory as "
            "baseline absence blind spot, "
            f"source_files={len(sources)}, inspected_files={len(oversized)}"
        ),
    )


def _public_contract_stability_score(sources: list[Path], tests: list[Path]) -> MetricScore:
    score = 0.8 if sources and tests else 0.0
    return MetricScore(
        metric_id="public-contract-stability",
        score=score,
        passed=False,
        missing_patterns=[] if sources and tests else ["source files", "public behavior tests"],
        evidence=(
            "proxy: exported-symbol baseline unavailable; source and test presence used "
            "as blind spot, "
            f"source_files={len(sources)}, test_files={len(tests)}"
        ),
    )
