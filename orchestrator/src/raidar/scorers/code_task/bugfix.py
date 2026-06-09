"""Bugfix scorer definition for the code-task family."""

from __future__ import annotations

from pathlib import Path

from raidar.schemas.scorecard import MetricScore
from raidar.scorers.base import BaseScorer, ScorerContext, ScorerEvidence, register_scorer
from raidar.scorers.common import (
    change_containment_metric_score,
    metric,
    source_and_test_files,
    valid_changed_file_paths,
    verification_stability_score,
)


@register_scorer(scorer_id="bugfix", version=1)
class Bugfix(BaseScorer):
    """Bugfix scorer promoted to an active code-backed definition."""

    status = "active"
    category = "quality"
    description = (
        "Scores targeted defect fixes with regression coverage, minimal unrelated "
        "drift, and clean verification."
    )
    metrics = (
        metric(
            "defect-resolution",
            "core",
            0.30,
            evidence=(
                "Requirement checks, failing-case reproduction evidence, final test and gate "
                "outcomes."
            ),
            score_derivation=(
                "Scores defect-linked check pass rate, capped by functional execution score."
            ),
            pass_fail=(
                "Passes when the defect-linked behavior now passes and functional execution passed."
            ),
        ),
        metric(
            "regression-protection",
            "core",
            0.25,
            evidence=(
                "Added or changed tests, test names or assertions near defect behavior, "
                "optional starter replay when available."
            ),
            score_derivation=(
                "Scores presence of behavior-specific regression tests; starter replay "
                "upgrades proxy confidence when available."
            ),
            pass_fail="Passes when regression evidence protects the fixed behavior.",
        ),
        metric(
            "change-containment",
            "core",
            0.20,
            evidence=(
                "Changed files, production/test diff ratio, touched modules, and "
                "scenario-declared expected paths."
            ),
            score_derivation="Starts at 1.0 and subtracts unrelated production churn penalties.",
            pass_fail="Passes when unrelated drift is absent or explicitly justified.",
        ),
        metric(
            "verification-stability",
            "core",
            0.15,
            evidence="Verification gate failure count across the run.",
            score_derivation="Uses the verification stability score computed from gate history.",
            pass_fail="Passes when verification stability is greater than zero.",
        ),
        metric(
            "defect-evidence-completeness",
            "core",
            0.10,
            evidence=(
                "Reproduction note, test evidence, verification evidence, and changed file "
                "evidence."
            ),
            score_derivation=(
                "Scores required defect evidence fields present in scorer metadata or run "
                "artifacts."
            ),
            pass_fail="Passes when defect, regression, and verification evidence are all present.",
        ),
    )

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        workspace = Path(context.workspace)
        outputs = context.execution.outputs
        sources, tests = source_and_test_files(workspace)
        return ScorerEvidence(
            metric_scores=(
                _defect_resolution_score(outputs),
                _regression_protection_score(tests),
                change_containment_metric_score(
                    workspace=workspace,
                    workspace_changes=context.workspace_changes,
                ),
                verification_stability_score(outputs.verification_stability),
                _defect_evidence_completeness_score(
                    outputs=outputs,
                    retained_evidence=context.retained_evidence,
                    workspace_changes=context.workspace_changes,
                ),
            ),
            metadata={"source_files": len(sources), "test_files": len(tests)},
        )


def _defect_resolution_score(outputs) -> MetricScore:
    functional = outputs.functional
    checks = getattr(outputs, "requirements_coverage", None)
    requirement_total = _numeric_requirement_value(checks, "total_requirements")
    satisfied_total = _numeric_requirement_value(checks, "satisfied_requirements")
    if requirement_total <= 0:
        return MetricScore(
            metric_id="defect-resolution",
            score=0.0,
            passed=False,
            missing_patterns=["defect-linked requirement evidence"],
            evidence=(
                f"direct: missing defect-linked requirement checks; functional={functional.score}"
            ),
        )
    requirement_score = min(1.0, satisfied_total / requirement_total)
    score = round(min(functional.score, requirement_score), 3)
    return MetricScore(
        metric_id="defect-resolution",
        score=score,
        passed=functional.passed and score >= 1.0,
        evidence=(
            "direct: functional execution capped by defect-linked requirement checks, "
            f"functional={functional.score}, requirement_ratio={requirement_score}"
        ),
    )


def _numeric_requirement_value(checks, field_name: str) -> float:
    value = getattr(checks, field_name, 0)
    if isinstance(value, bool):
        return 0.0
    return float(value) if isinstance(value, int | float) else 0.0


def _regression_protection_score(tests: list[Path]) -> MetricScore:
    behavior_specific_tests = [
        path
        for path in tests
        if any(term in path.as_posix().lower() for term in ("bug", "defect", "regression", "repro"))
    ]
    score = 1.0 if behavior_specific_tests else (0.5 if tests else 0.0)
    return MetricScore(
        metric_id="regression-protection",
        score=score,
        passed=bool(behavior_specific_tests),
        missing_patterns=[] if behavior_specific_tests else ["behavior-specific regression tests"],
        evidence=(
            "proxy: test file inventory cannot prove failure-before-pass replay, "
            f"test_files={len(tests)}, behavior_specific_tests={len(behavior_specific_tests)}"
        ),
    )


def _defect_evidence_completeness_score(
    *,
    outputs,
    retained_evidence: dict,
    workspace_changes: dict,
) -> MetricScore:
    reproduction_present = _has_text_evidence(
        retained_evidence, ("reproduction_note", "defect_reproduction", "reproduction")
    )
    regression_present = _has_collection_evidence(
        retained_evidence, ("regression_tests", "test_evidence", "passing_tests")
    )
    verification_present = bool(getattr(outputs.functional, "passed", False)) and (
        getattr(outputs.verification_stability, "score", 0.0) > 0
        or _has_text_evidence(retained_evidence, ("verification_evidence", "verification"))
    )
    changed_file_present = bool(valid_changed_file_paths(workspace_changes))
    checks = [
        reproduction_present,
        regression_present,
        verification_present,
        changed_file_present,
    ]
    score = round(sum(1 for check in checks if check) / len(checks), 3)
    missing = [
        label
        for label, present in (
            ("reproduction note", reproduction_present),
            ("regression test evidence", regression_present),
            ("verification evidence", verification_present),
            ("changed-file evidence", changed_file_present),
        )
        if not present
    ]
    return MetricScore(
        metric_id="defect-evidence-completeness",
        score=score,
        passed=score >= 1.0,
        missing_patterns=missing,
        evidence=(
            "direct: required defect evidence fields evaluated, "
            f"reproduction_note={reproduction_present}, "
            f"regression_tests={regression_present}, "
            f"verification_evidence={verification_present}, "
            f"changed_files={changed_file_present}"
        ),
    )


def _has_text_evidence(evidence: dict, keys: tuple[str, ...]) -> bool:
    for key in keys:
        value = evidence.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _has_collection_evidence(evidence: dict, keys: tuple[str, ...]) -> bool:
    for key in keys:
        value = evidence.get(key)
        if isinstance(value, list | tuple | set | dict) and len(value) > 0:
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False
