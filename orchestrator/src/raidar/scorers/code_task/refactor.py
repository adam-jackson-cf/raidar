"""Refactor scorer definition for the code-task family."""

from pathlib import Path

from raidar.schemas.scorecard import MetricScore
from raidar.scorers.base import BaseScorer, ScorerContext, ScorerEvidence, register_scorer
from raidar.scorers.common import (
    change_containment_metric_score,
    functional_score_from_evidence,
    metric,
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
            evidence="Paired starter and submitted behavior verification outcomes.",
            score_derivation=(
                "Uses paired starter and submitted behavior verification outcomes; "
                "missing paired evidence fails closed."
            ),
            pass_fail="Passes when paired behavior verification outcomes pass.",
        ),
        metric(
            "structural-improvement",
            "core",
            0.25,
            evidence="Paired starter and submitted structural verification outcomes.",
            score_derivation=(
                "Uses paired starter and submitted structural verification outcomes; "
                "missing paired evidence fails closed."
            ),
            pass_fail="Passes when paired structural verification outcomes pass.",
        ),
        metric(
            "public-contract-stability",
            "core",
            0.15,
            evidence="Paired starter and submitted public-contract verification outcomes.",
            score_derivation=(
                "Uses paired starter and submitted public-contract verification outcomes; "
                "missing paired evidence fails closed."
            ),
            pass_fail="Passes when paired public-contract verification outcomes pass.",
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
        """Declare scenario verification commands and selected snapshots."""

        actions = list(scenario_command_plan(self.id, context).actions)
        structural_paths = _metric_paths(context, "structural-improvement")
        if structural_paths is not None:
            actions.append(structural_snapshot("structural-improvement", structural_paths))
        public_contract_paths = _metric_paths(context, "public-contract-stability")
        if public_contract_paths is not None:
            actions.append(
                public_contract_snapshot("public-contract-stability", public_contract_paths)
            )
        return VerificationPlan(owner=self.id, actions=tuple(actions))

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        workspace = Path(context.workspace)
        final_evidence = context.verification_evidence.for_owner(self.id)
        baseline_evidence = context.baseline_verification_evidence.for_owner(self.id)
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
                _behavior_preservation_score(
                    baseline_functional,
                    final_functional,
                    baseline_has_outcome,
                    final_has_outcome,
                ),
                _structural_improvement_score(
                    baseline_evidence,
                    final_evidence,
                    _metric_paths_from_scenario(context, "structural-improvement"),
                ),
                _public_contract_stability_score(
                    baseline_evidence,
                    final_evidence,
                    _metric_paths_from_scenario(context, "public-contract-stability"),
                ),
                change_containment_metric_score(
                    workspace=workspace,
                    workspace_changes=context.workspace_changes,
                ),
                verification_stability_from_evidence(final_evidence),
            )
        )


def _behavior_preservation_score(
    baseline: MetricScore,
    final: MetricScore,
    baseline_has_outcome: bool,
    final_has_outcome: bool,
) -> MetricScore:
    missing_patterns: list[str] = []
    if not baseline_has_outcome:
        missing_patterns.append("baseline required verification commands")
    if not final_has_outcome:
        missing_patterns.append("final required verification commands")
    if missing_patterns:
        return MetricScore(
            metric_id="behavior-preservation",
            score=0.0,
            passed=False,
            missing_patterns=missing_patterns,
            evidence="missing: paired baseline and final verification outcomes are required",
        )
    return MetricScore(
        metric_id="behavior-preservation",
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


def _structural_improvement_score(
    baseline_evidence, final_evidence, paths: list[str] | None
) -> MetricScore:
    baseline, final, missing_patterns = _paired_snapshot_results(
        baseline_evidence,
        final_evidence,
        "structural-improvement",
        StructuralSnapshotEvidenceResult,
        paths,
    )
    if missing_patterns:
        return _missing_snapshot_metric("structural-improvement", missing_patterns, "structural")
    assert isinstance(baseline, StructuralSnapshotEvidenceResult)
    assert isinstance(final, StructuralSnapshotEvidenceResult)
    baseline_code_lines = sum(file.code_line_count for file in baseline.files)
    final_code_lines = sum(file.code_line_count for file in final.files)
    improved = baseline_code_lines > 0 and final_code_lines < baseline_code_lines
    score = (baseline_code_lines - final_code_lines) / baseline_code_lines if improved else 0.0
    return MetricScore(
        metric_id="structural-improvement",
        score=round(score, 3),
        passed=improved,
        evidence=(
            "direct: paired structural snapshots, "
            f"baseline_digest={baseline.digest}, final_digest={final.digest}, "
            f"code_lines={baseline_code_lines}->{final_code_lines}"
        ),
    )


def _public_contract_stability_score(
    baseline_evidence, final_evidence, paths: list[str] | None
) -> MetricScore:
    baseline, final, missing_patterns = _paired_snapshot_results(
        baseline_evidence,
        final_evidence,
        "public-contract-stability",
        PublicContractSnapshotEvidenceResult,
        paths,
    )
    if missing_patterns:
        return _missing_snapshot_metric(
            "public-contract-stability", missing_patterns, "public-contract"
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
        metric_id="public-contract-stability",
        score=1.0 if passed else 0.0,
        passed=passed,
        evidence=(
            "direct: paired public-contract snapshots, "
            f"baseline_digest={baseline.digest}, final_digest={final.digest}, "
            f"drifted_paths={drifted_paths}"
        ),
    )


def _paired_snapshot_results(
    baseline_evidence,
    final_evidence,
    action_id: str,
    result_type: type[StructuralSnapshotEvidenceResult]
    | type[PublicContractSnapshotEvidenceResult],
    paths: list[str] | None,
) -> tuple[
    StructuralSnapshotEvidenceResult | PublicContractSnapshotEvidenceResult | None,
    StructuralSnapshotEvidenceResult | PublicContractSnapshotEvidenceResult | None,
    list[str],
]:
    missing_patterns: list[str] = []
    if paths is None:
        missing_patterns.append("scenario-declared snapshot paths")
        return None, None, missing_patterns
    baseline_action = baseline_evidence.action(action_id)
    final_action = final_evidence.action(action_id)
    baseline = baseline_action.result if baseline_action is not None else None
    final = final_action.result if final_action is not None else None
    if not isinstance(baseline, result_type):
        missing_patterns.append(f"baseline {action_id} result")
    if not isinstance(final, result_type):
        missing_patterns.append(f"final {action_id} result")
    if missing_patterns:
        return None, None, missing_patterns
    assert isinstance(baseline, result_type)
    assert isinstance(final, result_type)
    if tuple(file.path for file in baseline.files) != tuple(paths):
        missing_patterns.append(f"baseline {action_id} selected paths")
    if tuple(file.path for file in final.files) != tuple(paths):
        missing_patterns.append(f"final {action_id} selected paths")
    if missing_patterns:
        return None, None, missing_patterns
    return baseline, final, missing_patterns


def _missing_snapshot_metric(
    metric_id: str, missing_patterns: list[str], snapshot_name: str
) -> MetricScore:
    return MetricScore(
        metric_id=metric_id,
        score=0.0,
        passed=False,
        missing_patterns=missing_patterns,
        evidence=f"missing: paired {snapshot_name} snapshot results are required",
    )
