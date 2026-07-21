"""Refactor scorer definition for the code-task family."""

from __future__ import annotations

from pathlib import Path

from raidar.schemas.scorecard import MetricScore
from raidar.scorers.base import BaseScorer, ScorerContext, ScorerEvidence, register_scorer
from raidar.scorers.common import (
    change_containment_metric_score,
    functional_score_from_evidence,
    metric,
    source_and_test_files,
    verification_stability_from_evidence,
)
from raidar.scorers.evidence_contract import (
    PublicContractSnapshotEvidenceResult,
    StructuralSnapshotEvidenceResult,
)
from raidar.scorers.verification import (
    EvidenceDependency,
    VerificationPlan,
    VerificationPlanContext,
    public_contract_snapshot,
    scenario_command_plan,
    structural_snapshot,
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
    evidence_dependencies = (EvidenceDependency(kind="command"),)

    def verification_plan(self, context: VerificationPlanContext) -> VerificationPlan:
        """Declare scenario verification commands owned by this scorer."""

        return scenario_command_plan(self.id, context)

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        workspace = Path(context.workspace)
        evidence = context.verification_evidence.for_owner(self.id)
        sources, tests = source_and_test_files(workspace)
        return ScorerEvidence(
            metric_scores=(
                _behavior_preservation_score(functional_score_from_evidence(evidence)),
                _structural_improvement_score(sources),
                _public_contract_stability_score(sources, tests),
                change_containment_metric_score(
                    workspace=workspace,
                    workspace_changes=context.workspace_changes,
                ),
                verification_stability_from_evidence(evidence),
            ),
            metadata={"source_files": len(sources), "test_files": len(tests)},
        )


@register_scorer(scorer_id="refactor", version=2)
class RefactorV2(BaseScorer):
    """Refactor scorer for the direct-evidence calibration contract."""

    status = "active"
    category = "quality"
    description = (
        "Scores behavior-preserving refactors with structural improvement and "
        "verification confidence."
    )
    metrics = (
        metric(
            "refactor-behavior-preservation-v2",
            "core",
            0.30,
            evidence="Paired starter and submitted behavior verification outcomes.",
            score_derivation="Uses paired starter and submitted behavior verification outcomes; missing paired evidence fails closed.",
            pass_fail="Passes when paired behavior verification outcomes pass.",
        ),
        metric(
            "refactor-structural-improvement-v2",
            "core",
            0.25,
            evidence="Paired starter and submitted structural verification outcomes.",
            score_derivation="Uses paired starter and submitted structural verification outcomes; missing paired evidence fails closed.",
            pass_fail="Passes when paired structural verification outcomes pass.",
        ),
        metric(
            "refactor-public-contract-stability-v2",
            "core",
            0.15,
            evidence="Paired starter and submitted public-contract verification outcomes.",
            score_derivation="Uses paired starter and submitted public-contract verification outcomes; missing paired evidence fails closed.",
            pass_fail="Passes when paired public-contract verification outcomes pass.",
        ),
        metric(
            "refactor-change-containment-v2",
            "core",
            0.15,
            evidence="Changed files, production/test diff ratio, expected paths, and generated artifact exclusions.",
            score_derivation="Scores focused refactor scope with penalties for unrelated behavior-bearing edits.",
            pass_fail="Passes when refactor changes stay within declared surfaces.",
        ),
        metric(
            "refactor-verification-stability-v2",
            "core",
            0.15,
            evidence="Verification gate failure count across the run.",
            score_derivation="Uses the verification stability score computed from gate history.",
            pass_fail="Passes when verification stability is greater than zero.",
        ),
    )
    evidence_dependencies = (EvidenceDependency(kind="command"),)

    def verification_plan(self, context: VerificationPlanContext) -> VerificationPlan:
        owner = "refactor-v2"
        actions = list(scenario_command_plan(owner, context).actions)
        structural_paths = _metric_paths(context, "refactor-structural-improvement-v2")
        if structural_paths is not None:
            actions.append(
                structural_snapshot("refactor-structural-improvement-v2", structural_paths)
            )
        public_contract_paths = _metric_paths(context, "refactor-public-contract-stability-v2")
        if public_contract_paths is not None:
            actions.append(
                public_contract_snapshot(
                    "refactor-public-contract-stability-v2", public_contract_paths
                )
            )
        return VerificationPlan(owner=owner, actions=tuple(actions))

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        workspace = Path(context.workspace)
        owner = "refactor-v2"
        final_evidence = context.verification_evidence.for_owner(owner)
        baseline_evidence = context.baseline_verification_evidence.for_owner(owner)
        final_functional = functional_score_from_evidence(final_evidence)
        baseline_functional = functional_score_from_evidence(baseline_evidence)
        final_has_outcome = any(
            action.required for action in final_evidence.actions(kind="command")
        )
        baseline_has_outcome = any(
            action.required for action in baseline_evidence.actions(kind="command")
        )
        return ScorerEvidence(
            metric_scores=(
                _v2_behavior_preservation_score(
                    baseline_functional, final_functional, baseline_has_outcome, final_has_outcome
                ),
                _v2_structural_improvement_score(
                    baseline_evidence,
                    final_evidence,
                    _metric_paths_from_scenario(context, "refactor-structural-improvement-v2"),
                ),
                _v2_public_contract_stability_score(
                    baseline_evidence,
                    final_evidence,
                    _metric_paths_from_scenario(context, "refactor-public-contract-stability-v2"),
                ),
                change_containment_metric_score(
                    workspace=workspace,
                    workspace_changes=context.workspace_changes,
                    metric_id="refactor-change-containment-v2",
                ),
                verification_stability_from_evidence(final_evidence).model_copy(
                    update={"metric_id": "refactor-verification-stability-v2"}
                ),
            )
        )


def _behavior_preservation_score(functional: MetricScore) -> MetricScore:
    return MetricScore(
        metric_id="behavior-preservation",
        score=functional.score,
        passed=functional.passed,
        evidence=(
            "direct: behavior preservation uses owner-declared verification actions, "
            f"{functional.evidence}"
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


def _v2_behavior_preservation_score(
    baseline: MetricScore, final: MetricScore, baseline_has_outcome: bool, final_has_outcome: bool
) -> MetricScore:
    missing_patterns: list[str] = []
    if not baseline_has_outcome:
        missing_patterns.append("baseline required verification commands")
    if not final_has_outcome:
        missing_patterns.append("final required verification commands")
    if missing_patterns:
        return MetricScore(
            metric_id="refactor-behavior-preservation-v2",
            score=0.0,
            passed=False,
            missing_patterns=missing_patterns,
            evidence="missing: paired baseline and final verification outcomes are required",
        )
    return MetricScore(
        metric_id="refactor-behavior-preservation-v2",
        score=min(baseline.score, final.score),
        passed=baseline.passed and final.passed,
        evidence=(
            "direct: paired baseline and final behavior verification outcomes, "
            f"baseline=({baseline.evidence}), final=({final.evidence})"
        ),
    )


def _valid_selected_paths(paths: object) -> bool:
    if not isinstance(paths, list) or not paths:
        return False
    if any(not isinstance(path, str) or not path for path in paths):
        return False
    if len(paths) != len(set(paths)):
        return False
    return all(not Path(path).is_absolute() and ".." not in Path(path).parts for path in paths)


def _metric_paths(context: VerificationPlanContext, metric_id: str) -> list[str] | None:
    for resolved_metric in context.resolved_scorer.metrics:
        if resolved_metric.id != metric_id:
            continue
        paths = resolved_metric.config.get("paths")
        if _valid_selected_paths(paths):
            return list(paths)
        return None
    return None


def _metric_paths_from_scenario(context: ScorerContext, metric_id: str) -> list[str] | None:
    for scorer in context.scenario.scorers:
        if scorer.id != "refactor":
            continue
        paths = scorer.config.get(metric_id, {}).get("paths")
        if _valid_selected_paths(paths):
            return list(paths)
        return None
    return None


def _v2_structural_improvement_score(
    baseline_evidence, final_evidence, paths: list[str] | None
) -> MetricScore:
    missing_patterns: list[str] = []
    if paths is None:
        missing_patterns.append("scenario-declared structural snapshot paths")
    baseline_action = baseline_evidence.action("refactor-structural-improvement-v2")
    final_action = final_evidence.action("refactor-structural-improvement-v2")
    baseline = baseline_action.result if baseline_action is not None else None
    final = final_action.result if final_action is not None else None
    if not isinstance(baseline, StructuralSnapshotEvidenceResult):
        missing_patterns.append("baseline refactor-structural-improvement-v2 result")
    if not isinstance(final, StructuralSnapshotEvidenceResult):
        missing_patterns.append("final refactor-structural-improvement-v2 result")
    if (
        paths is not None
        and isinstance(baseline, StructuralSnapshotEvidenceResult)
        and tuple(file.path for file in baseline.files) != tuple(paths)
    ):
        missing_patterns.append("baseline refactor-structural-improvement-v2 selected paths")
    if (
        paths is not None
        and isinstance(final, StructuralSnapshotEvidenceResult)
        and tuple(file.path for file in final.files) != tuple(paths)
    ):
        missing_patterns.append("final refactor-structural-improvement-v2 selected paths")
    if missing_patterns:
        return MetricScore(
            metric_id="refactor-structural-improvement-v2",
            score=0.0,
            passed=False,
            missing_patterns=missing_patterns,
            evidence="missing: paired structural snapshot results are required",
        )
    assert isinstance(baseline, StructuralSnapshotEvidenceResult)
    assert isinstance(final, StructuralSnapshotEvidenceResult)
    baseline_code_lines = sum(file.code_line_count for file in baseline.files)
    final_code_lines = sum(file.code_line_count for file in final.files)
    improved = baseline_code_lines > 0 and final_code_lines < baseline_code_lines
    score = (baseline_code_lines - final_code_lines) / baseline_code_lines if improved else 0.0
    return MetricScore(
        metric_id="refactor-structural-improvement-v2",
        score=round(score, 3),
        passed=improved,
        evidence=(
            "direct: paired structural snapshots, "
            f"baseline_digest={baseline.digest}, final_digest={final.digest}, "
            f"code_lines={baseline_code_lines}->{final_code_lines}"
        ),
    )


def _v2_public_contract_stability_score(
    baseline_evidence, final_evidence, paths: list[str] | None
) -> MetricScore:
    missing_patterns: list[str] = []
    if paths is None:
        missing_patterns.append("scenario-declared public-contract snapshot paths")
    baseline_action = baseline_evidence.action("refactor-public-contract-stability-v2")
    final_action = final_evidence.action("refactor-public-contract-stability-v2")
    baseline = baseline_action.result if baseline_action is not None else None
    final = final_action.result if final_action is not None else None
    if not isinstance(baseline, PublicContractSnapshotEvidenceResult):
        missing_patterns.append("baseline refactor-public-contract-stability-v2 result")
    if not isinstance(final, PublicContractSnapshotEvidenceResult):
        missing_patterns.append("final refactor-public-contract-stability-v2 result")
    if (
        paths is not None
        and isinstance(baseline, PublicContractSnapshotEvidenceResult)
        and tuple(file.path for file in baseline.files) != tuple(paths)
    ):
        missing_patterns.append("baseline refactor-public-contract-stability-v2 selected paths")
    if (
        paths is not None
        and isinstance(final, PublicContractSnapshotEvidenceResult)
        and tuple(file.path for file in final.files) != tuple(paths)
    ):
        missing_patterns.append("final refactor-public-contract-stability-v2 selected paths")
    if missing_patterns:
        return MetricScore(
            metric_id="refactor-public-contract-stability-v2",
            score=0.0,
            passed=False,
            missing_patterns=missing_patterns,
            evidence="missing: paired public-contract snapshot results are required",
        )
    assert isinstance(baseline, PublicContractSnapshotEvidenceResult)
    assert isinstance(final, PublicContractSnapshotEvidenceResult)
    baseline_declarations = {file.path: file.declarations for file in baseline.files}
    final_declarations = {file.path: file.declarations for file in final.files}
    drifted_paths = [
        path for path in paths if final_declarations[path] != baseline_declarations[path]
    ]
    passed = not drifted_paths
    return MetricScore(
        metric_id="refactor-public-contract-stability-v2",
        score=1.0 if passed else 0.0,
        passed=passed,
        evidence=(
            "direct: paired public-contract snapshots, "
            f"baseline_digest={baseline.digest}, final_digest={final.digest}, "
            f"drifted_paths={drifted_paths}"
        ),
    )
