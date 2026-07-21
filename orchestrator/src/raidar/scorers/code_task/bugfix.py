"""Bugfix scorer definition for the code-task family."""

from __future__ import annotations

from pathlib import Path

from raidar.schemas.scorecard import MetricScore
from raidar.scorers.base import BaseScorer, ScorerContext, ScorerEvidence, register_scorer
from raidar.scorers.common import (
    change_containment_metric_score,
    functional_score_from_evidence,
    is_test_path,
    metric,
    source_and_test_files,
    valid_changed_file_paths,
    verification_stability_from_evidence,
)
from raidar.scorers.requirements import _requirements_coverage_score
from raidar.scorers.verification import (
    EvidenceDependency,
    VerificationEvidence,
    VerificationPlan,
    VerificationPlanContext,
    scenario_command_plan,
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
    evidence_dependencies = (EvidenceDependency(kind="command"),)

    def verification_plan(self, context: VerificationPlanContext) -> VerificationPlan:
        """Declare scenario verification commands owned by this scorer."""

        return scenario_command_plan(self.id, context)

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        workspace = Path(context.workspace)
        evidence = context.verification_evidence.for_owner(self.id)
        functional = functional_score_from_evidence(evidence)
        stability = verification_stability_from_evidence(evidence)
        requirements = _requirements_coverage_score(workspace, context.scenario)
        sources, tests = source_and_test_files(workspace)
        return ScorerEvidence(
            metric_scores=(
                _defect_resolution_score(functional, requirements),
                _regression_protection_score(tests),
                change_containment_metric_score(
                    workspace=workspace,
                    workspace_changes=context.workspace_changes,
                ),
                stability,
                _defect_evidence_completeness_score(
                    functional=functional,
                    stability=stability,
                    retained_evidence=context.retained_evidence,
                    workspace_changes=context.workspace_changes,
                ),
            ),
            metadata={"source_files": len(sources), "test_files": len(tests)},
        )


@register_scorer(scorer_id="bugfix", version=2)
class BugfixV2(BaseScorer):
    """Bugfix scorer for the direct-evidence calibration contract."""

    status = "active"
    category = "quality"
    description = (
        "Scores targeted defect fixes with regression coverage, minimal unrelated "
        "drift, and clean verification."
    )
    metrics = (
        metric(
            "bugfix-defect-resolution-v2",
            "core",
            0.30,
            evidence="Paired bugfix-owned starter command failure and final command success.",
            score_derivation=(
                "Passes only when the same declared bugfix command fails on the starter "
                "workspace and succeeds after submission."
            ),
            pass_fail="Passes when at least one paired defect command resolves.",
        ),
        metric(
            "bugfix-regression-protection-v2",
            "core",
            0.25,
            evidence=(
                "Paired bugfix command outcomes and retained regression-test paths linked "
                "to changed test files."
            ),
            score_derivation=(
                "Passes only when a resolved command pair and a retained changed-test "
                "linkage are both present."
            ),
            pass_fail="Passes when retained regression evidence identifies a changed test.",
        ),
        metric(
            "bugfix-change-containment-v2",
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
            "bugfix-verification-stability-v2",
            "core",
            0.15,
            evidence="Verification gate failure count across the run.",
            score_derivation="Uses the verification stability score computed from gate history.",
            pass_fail="Passes when verification stability is greater than zero.",
        ),
        metric(
            "bugfix-defect-evidence-completeness-v2",
            "core",
            0.10,
            evidence=(
                "Paired bugfix command outcomes and retained regression-test paths linked "
                "to changed test files."
            ),
            score_derivation=(
                "Passes only when direct paired outcome evidence and retained changed-test "
                "linkage are both present."
            ),
            pass_fail="Passes when paired defect evidence and changed-test linkage are present.",
        ),
    )
    evidence_dependencies = (EvidenceDependency(kind="command"),)

    def verification_plan(self, context: VerificationPlanContext) -> VerificationPlan:
        """Declare scenario verification commands owned by this scorer."""

        return scenario_command_plan("bugfix-v2", context)

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        workspace = Path(context.workspace)
        final_evidence = context.verification_evidence
        baseline_evidence = context.baseline_verification_evidence
        paired_outcomes = _paired_command_outcomes(
            baseline_evidence,
            final_evidence,
            owner="bugfix-v2",
        )
        changed_test_links = _changed_test_links(
            context.retained_evidence,
            context.workspace_changes,
        )
        stability = verification_stability_from_evidence(final_evidence.for_owner("bugfix-v2"))
        return ScorerEvidence(
            metric_scores=(
                _v2_metric_score(_direct_defect_resolution_score(paired_outcomes)),
                _v2_metric_score(
                    _direct_regression_protection_score(paired_outcomes, changed_test_links)
                ),
                _v2_metric_score(
                    change_containment_metric_score(
                        workspace=workspace,
                        workspace_changes=context.workspace_changes,
                    )
                ),
                _v2_metric_score(stability),
                _v2_metric_score(
                    _direct_defect_evidence_completeness_score(paired_outcomes, changed_test_links)
                ),
            ),
            metadata={
                "paired_command_count": len(paired_outcomes),
                "changed_test_link_count": len(changed_test_links),
            },
        )


def _v2_metric_score(score: MetricScore) -> MetricScore:
    if score.metric_id.startswith("bugfix-"):
        return score
    metric_id = score.metric_id.removesuffix("-v2")
    return score.model_copy(update={"metric_id": f"bugfix-{metric_id}-v2"})


def _defect_resolution_score(functional: MetricScore, requirements: MetricScore) -> MetricScore:
    requirement_total = requirements.matched_count + len(requirements.missing_patterns)
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
    requirement_score = requirements.score
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
    functional: MetricScore,
    stability: MetricScore,
    retained_evidence: dict,
    workspace_changes: dict,
) -> MetricScore:
    reproduction_present = _has_text_evidence(
        retained_evidence, ("reproduction_note", "defect_reproduction", "reproduction")
    )
    regression_present = _has_collection_evidence(
        retained_evidence, ("regression_tests", "test_evidence", "passing_tests")
    )
    verification_present = functional.passed and (
        stability.score > 0
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


def _paired_command_outcomes(
    baseline_evidence: VerificationEvidence,
    final_evidence: VerificationEvidence,
    *,
    owner: str,
) -> tuple[tuple[str, bool, bool], ...]:
    baseline_actions = _owner_command_actions(baseline_evidence, owner)
    final_actions = _owner_command_actions(final_evidence, owner)
    return tuple(
        (
            local_id,
            _command_failed(baseline_actions[local_id]),
            _command_succeeded(final_actions[local_id]),
        )
        for local_id in sorted(set(baseline_actions) & set(final_actions))
    )


def _owner_command_actions(evidence: VerificationEvidence, owner: str) -> dict[str, object]:
    prefix = f"{owner}."
    actions: dict[str, object] = {}
    for alias, canonical_id in evidence.aliases.items():
        if not alias.startswith(prefix):
            continue
        action = evidence.actions.get(canonical_id)
        if action is not None and action.kind == "command":
            actions[alias.removeprefix(prefix)] = action
    return actions


def _command_failed(action: object) -> bool:
    return (
        getattr(action, "status", None) == "ok"
        and getattr(action, "kind", None) == "command"
        and getattr(getattr(action, "result", None), "exit_code", 0) != 0
    )


def _command_succeeded(action: object) -> bool:
    return (
        getattr(action, "status", None) == "ok"
        and getattr(action, "kind", None) == "command"
        and getattr(getattr(action, "result", None), "exit_code", None) == 0
    )


def _direct_defect_resolution_score(
    paired_outcomes: tuple[tuple[str, bool, bool], ...],
) -> MetricScore:
    resolved_ids = [
        local_id
        for local_id, starter_failed, final_succeeded in paired_outcomes
        if starter_failed and final_succeeded
    ]
    missing = _direct_defect_resolution_missing(paired_outcomes, resolved_ids)
    return MetricScore(
        metric_id="defect-resolution-v2",
        score=1.0 if resolved_ids else 0.0,
        passed=bool(resolved_ids),
        matched_count=len(resolved_ids),
        missing_patterns=missing,
        evidence=(
            "paired baseline/final command outcomes: "
            f"paired_commands={len(paired_outcomes)}, resolved_commands={len(resolved_ids)}"
        ),
    )


def _direct_defect_resolution_missing(
    paired_outcomes: tuple[tuple[str, bool, bool], ...], resolved_ids: list[str]
) -> list[str]:
    starter_failure = any(starter_failed for _, starter_failed, _ in paired_outcomes)
    final_success = any(final_succeeded for _, _, final_succeeded in paired_outcomes)
    missing = []
    if not paired_outcomes:
        missing.append("paired bugfix command evidence")
    if not starter_failure:
        missing.append("starter command failure")
    if not final_success:
        missing.append("matching final command success")
    if starter_failure and final_success and not resolved_ids:
        missing.append("paired starter command failure and matching final command success")
    return missing


def _direct_regression_protection_score(
    paired_outcomes: tuple[tuple[str, bool, bool], ...],
    changed_test_links: tuple[str, ...],
) -> MetricScore:
    resolved = any(
        starter_failed and final_succeeded for _, starter_failed, final_succeeded in paired_outcomes
    )
    missing = []
    if not resolved:
        missing.append("paired starter failure and final success")
    if not changed_test_links:
        missing.append("retained regression-test linkage to changed test files")
    passed = resolved and bool(changed_test_links)
    return MetricScore(
        metric_id="regression-protection-v2",
        score=1.0 if passed else 0.0,
        passed=passed,
        matched_count=len(changed_test_links) if passed else 0,
        missing_patterns=missing,
        evidence=(
            "paired baseline/final command outcomes and retained changed-test linkage: "
            f"resolved_commands={int(resolved)}, changed_test_links={len(changed_test_links)}"
        ),
    )


def _direct_defect_evidence_completeness_score(
    paired_outcomes: tuple[tuple[str, bool, bool], ...],
    changed_test_links: tuple[str, ...],
) -> MetricScore:
    resolved = any(
        starter_failed and final_succeeded for _, starter_failed, final_succeeded in paired_outcomes
    )
    linkage_present = bool(changed_test_links)
    missing = [
        label
        for label, present in (
            ("paired starter failure and final success", resolved),
            ("retained regression-test linkage to changed test files", linkage_present),
        )
        if not present
    ]
    passed = resolved and linkage_present
    return MetricScore(
        metric_id="defect-evidence-completeness-v2",
        score=1.0 if passed else 0.0,
        passed=passed,
        matched_count=int(resolved) + int(linkage_present),
        missing_patterns=missing,
        evidence=(
            "paired baseline/final command outcomes and retained changed-test linkage: "
            f"resolved_commands={int(resolved)}, changed_test_links={len(changed_test_links)}"
        ),
    )


def _changed_test_links(
    retained_evidence: dict,
    workspace_changes: dict,
) -> tuple[str, ...]:
    changed_tests = {
        _normalized_path(path)
        for path in valid_changed_file_paths(workspace_changes)
        if is_test_path(path)
    }
    retained_tests = {
        _normalized_path(Path(path))
        for key in ("regression_tests", "test_evidence", "passing_tests")
        for path in _string_values(retained_evidence.get(key))
    }
    return tuple(sorted(changed_tests & retained_tests))


def _normalized_path(path: Path) -> str:
    return path.as_posix().lstrip("./")


def _string_values(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, dict):
        return tuple(item for entry in value.values() for item in _string_values(entry))
    if isinstance(value, list | tuple | set):
        return tuple(item for entry in value for item in _string_values(entry))
    return ()
