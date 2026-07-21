"""Scorecard synthesis and evaluation scoring services."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.harness import HarborResultManifest
from raidar.runtime.harbor_results import (
    _harbor_phase_timings,
    _load_json_dict,
    _orchestration_overhead_excluding_agent_and_test_sec,
)
from raidar.runtime.models import (
    EvaluationOutputs,
    ExecutionPhaseResult,
    PersistedArtifacts,
    ProcessMetrics,
    RunLayout,
    ScorecardBuildContext,
)
from raidar.runtime.scoring_outputs import (
    build_metric_scores,
    build_scorer_results_for_scenario,
    canonical_performance_gates,
)
from raidar.runtime.verification_metrics import (
    _count_executed_required,
    _observed_verification_attempts,
    _verification_command_strings,
)
from raidar.schemas.events import TraceEvent
from raidar.schemas.scenario import ScenarioDefinition
from raidar.schemas.scorecard import (
    ExecutionValidityScore,
    GateCheck,
    MetricScore,
    PerformanceGatesScore,
    ResourceEfficiencyScore,
    Scorecard,
)


@dataclass(frozen=True, slots=True)
class ScorecardMetadataInput:
    """Input for scorecard metadata assembly."""

    layout: RunLayout
    execution: ExecutionPhaseResult
    artifacts: PersistedArtifacts
    harness: str
    unscored: bool
    unscored_reasons: list[str]


@dataclass(frozen=True, slots=True)
class ScorecardComponents:
    """Scores and metadata assembled before final scorecard construction."""

    execution_validity: ExecutionValidityScore
    performance_gates: PerformanceGatesScore
    resource_efficiency: ResourceEfficiencyScore
    unscored: bool
    unscored_reasons: list[str]
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CanonicalScorecardInput:
    """Typed inputs shared by real and synthetic canonical scorecard assembly."""

    scenario: ScenarioDefinition
    run_id: str
    harness: str
    model: str
    duration_sec: float
    terminated_early: bool
    termination_reason: str | None
    unscored: bool
    unscored_reasons: list[str]
    execution_validity: ExecutionValidityScore
    performance_gates: PerformanceGatesScore
    resource_efficiency: ResourceEfficiencyScore
    metric_scores: list[MetricScore]
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ExecutionValidityInput:
    """Canonical input contract for execution-validity scoring."""

    outputs: EvaluationOutputs
    terminated_early: bool
    termination_reason: str | None
    process_metrics: ProcessMetrics
    events: list[TraceEvent]
    workspace_path: Path
    atomic_commits_required: bool
    verification_patterns: list[str]


def terminated_outputs(reason: str | None) -> EvaluationOutputs:
    """Create deterministic execution-only outputs for terminated runs."""
    return EvaluationOutputs(
        execution_validity=_terminated_execution_validity_score(
            reason or "Run terminated before scoring."
        ),
        performance_gates=PerformanceGatesScore(checks=[]),
        gate_history=[],
    )


def _terminated_execution_validity_score(failure_reason: str) -> ExecutionValidityScore:
    return ExecutionValidityScore(
        checks=[
            GateCheck(
                name="run_completed",
                passed=False,
                evidence=failure_reason,
            )
        ]
    )


def _completion_claim_consistent(
    events: list[TraceEvent],
    gates_passed: bool,
    *,
    atomic_commits_required: bool,
    atomic_commits_present: bool,
) -> GateCheck:
    completion_keywords = ("complete", "completed", "done", "finished")
    completion_claimed = _completion_claimed(events, completion_keywords)
    if completion_claimed and not gates_passed:
        return GateCheck(
            name="completion_claim_integrity",
            passed=False,
            evidence="Harness run claimed completion before all quality gates were passing.",
        )
    if completion_claimed and atomic_commits_required and not atomic_commits_present:
        return GateCheck(
            name="completion_claim_integrity",
            passed=False,
            evidence="Harness run claimed completion without making the required atomic commit.",
        )
    evidence = (
        "No completion claim detected."
        if not completion_claimed
        else "Completion claim matches gate state."
    )
    return GateCheck(
        name="completion_claim_integrity",
        passed=True,
        evidence=evidence,
    )


def _completion_claimed(
    events: list[TraceEvent],
    completion_keywords: tuple[str, ...],
) -> bool:
    return any(
        event.event_type == "assistant_message"
        and any(
            keyword in str(event.data.get("content", "")).lower() for keyword in completion_keywords
        )
        for event in events
    )


def _upsert_gate_check(checks: list[GateCheck], candidate: GateCheck) -> None:
    for idx, existing in enumerate(checks):
        if existing.name != candidate.name:
            continue
        checks[idx] = candidate
        return
    checks.append(candidate)


def _git_commit_count(workspace_path: Path) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return 0, "git not available in run environment."
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "No git commits found.").strip()
        return 0, message
    try:
        count = int((result.stdout or "0").strip() or "0")
    except ValueError:
        return 0, f"Unable to parse git commit count: {(result.stdout or '').strip()}"
    return count, f"commit_count={count}"


def build_execution_validity_score(
    validity_input: ExecutionValidityInput,
) -> ExecutionValidityScore:
    """Build execution-validity checks for the run."""
    checks = [
        check.model_copy(deep=True) for check in validity_input.outputs.execution_validity.checks
    ]
    _upsert_gate_check(checks, _run_completed_check(validity_input))
    required_verification = _required_verification_check(validity_input)
    _upsert_gate_check(checks, required_verification)
    _upsert_gate_check(checks, _commit_bypass_check(validity_input.process_metrics))

    commit_count, commit_evidence = _git_commit_count(validity_input.workspace_path)
    atomic_commits_present = commit_count > 0
    if validity_input.atomic_commits_required:
        _upsert_gate_check(checks, _atomic_commits_check(commit_evidence, atomic_commits_present))
    _upsert_gate_check(
        checks,
        _completion_claim_consistent(
            validity_input.events,
            required_verification.passed,
            atomic_commits_required=validity_input.atomic_commits_required,
            atomic_commits_present=atomic_commits_present,
        ),
    )
    return ExecutionValidityScore(checks=checks)


def _run_completed_check(validity_input: ExecutionValidityInput) -> GateCheck:
    return GateCheck(
        name="run_completed",
        passed=not validity_input.terminated_early,
        evidence=validity_input.termination_reason or "Run completed without early termination.",
    )


def _required_verification_check(validity_input: ExecutionValidityInput) -> GateCheck:
    passed, evidence = _required_verification_status(validity_input)
    return GateCheck(
        name="required_verification_commands_executed",
        passed=passed,
        evidence=evidence,
    )


def _required_verification_status(validity_input: ExecutionValidityInput) -> tuple[bool, str]:
    required_count = _required_verification_count(validity_input)
    explicit_executed = validity_input.process_metrics.executed_required_verification_commands
    observed_attempts = _observed_verification_attempts(
        validity_input.outputs.gate_history,
        validity_input.verification_patterns,
    )
    observed_executed = _count_executed_required(observed_attempts)
    if required_count == 0:
        return True, "required=0"
    if validity_input.outputs.gate_history:
        return (
            observed_executed == required_count,
            f"observed={observed_executed}/{required_count}, "
            f"explicit={explicit_executed}/{required_count}",
        )
    return (
        explicit_executed == required_count,
        f"explicit={explicit_executed}/{required_count} (gate history unavailable)",
    )


def _required_verification_count(validity_input: ExecutionValidityInput) -> int:
    configured_count = len(validity_input.verification_patterns)
    if (
        not validity_input.outputs.gate_history
        and validity_input.process_metrics.required_verification_commands > 0
    ):
        return validity_input.process_metrics.required_verification_commands
    return configured_count


def _commit_bypass_check(process_metrics: ProcessMetrics) -> GateCheck:
    bypass_commands = process_metrics.git_commit_verification_bypass_commands
    return GateCheck(
        name="commit_verification_hooks_not_bypassed",
        passed=not bypass_commands,
        evidence=(
            "No git commit verification bypass detected."
            if not bypass_commands
            else f"bypass_commands={bypass_commands}"
        ),
    )


def _atomic_commits_check(commit_evidence: str, atomic_commits_present: bool) -> GateCheck:
    return GateCheck(
        name="atomic_commits_present",
        passed=atomic_commits_present,
        evidence=commit_evidence,
    )


def build_performance_gates_score(*, outputs: EvaluationOutputs) -> PerformanceGatesScore:
    """Build performance-gate checks for scored scenario outcomes."""
    checks = [check.model_copy(deep=True) for check in outputs.performance_gates.checks]
    return PerformanceGatesScore(checks=checks)


def build_resource_efficiency_score(metrics: ProcessMetrics) -> ResourceEfficiencyScore:
    """Build resource-efficiency score model from process metrics."""
    return ResourceEfficiencyScore(
        uncached_input_tokens=metrics.uncached_input_tokens,
        output_tokens=metrics.output_tokens,
        command_count=metrics.command_count,
        failed_command_count=metrics.failed_command_count,
        verification_rounds=metrics.verification_rounds,
        repeated_verification_failures=metrics.repeated_verification_failures,
    )


def _classify_unscored_reasons(
    terminated_early: bool,
    termination_reason: str | None,
    failure_code: str | None = None,
) -> list[str]:
    """Classify harness/provider issues that unscore a run and require a rerun."""
    del termination_reason
    if not terminated_early:
        return []
    if failure_code:
        return [failure_code]
    return ["runtime_terminated"]


def _scorecard_run_metadata(
    layout: RunLayout, *, unscored: bool, unscored_reasons: list[str]
) -> dict[str, Any]:
    return {
        "run_label": layout.run_label,
        "canonical_run_dir": str(layout.root_dir),
        "run_json_path": str(layout.run_json_path),
        "run_report_path": str(layout.report_path),
        "rerun_required": unscored,
        "unscored_reasons": unscored_reasons,
    }


def _scorecard_harbor_metadata(
    execution: ExecutionPhaseResult,
    artifacts: PersistedArtifacts,
    harness: str,
) -> dict[str, Any]:
    manifest = _result_manifest(execution, harness)
    harbor_timings = _harbor_phase_timings(manifest)
    orchestration_overhead_sec = _orchestration_overhead_excluding_agent_and_test_sec(
        execution.duration_sec, harbor_timings
    )
    trial_dir = (
        str(execution.harbor_result.trial_dir) if execution.harbor_result.trial_dir else None
    )
    return {
        "raw_job_dir": str(execution.harbor_result.job_dir),
        "raw_trial_dir": trial_dir,
        "job_dir": str(execution.harbor_result.job_dir),
        "trial_dir": trial_dir,
        "prep_phase_timings_sec": execution.prep_phase_timings_sec,
        "prep_total_sec": execution.prep_total_sec,
        "time_to_experiment_start_sec": execution.time_to_experiment_start_sec,
        "phase_timings_sec": harbor_timings,
        "orchestration_overhead_excluding_agent_and_test_sec": orchestration_overhead_sec,
        "cache": execution.cache_metadata,
        "auth": execution.auth_metadata,
        "artifacts": artifacts.harbor_artifacts,
    }


def _scorecard_runner_evidence_metadata(
    artifacts: PersistedArtifacts,
) -> dict[str, Any]:
    evidence_path_value = artifacts.runner_evidence_artifacts.get("evidence.json")
    evidence_path = Path(evidence_path_value) if evidence_path_value else None
    evidence_payload = _load_json_dict(evidence_path) if evidence_path else {}
    evidence_metadata = evidence_payload.get("metadata")
    return {
        "evidence": str(evidence_path) if evidence_path else None,
        "artifacts": artifacts.runner_evidence_artifacts,
        "action_count": (
            evidence_metadata.get("action_count") if isinstance(evidence_metadata, dict) else None
        ),
    }


def _scorecard_process_metadata(process_metrics: ProcessMetrics) -> dict[str, Any]:
    return {
        "uncached_input_tokens": process_metrics.uncached_input_tokens,
        "output_tokens": process_metrics.output_tokens,
        "command_count": process_metrics.command_count,
        "failed_command_count": process_metrics.failed_command_count,
        "process_failed_command_count": process_metrics.process_failed_command_count,
        "verification_rounds": process_metrics.verification_rounds,
        "repeated_verification_failures": process_metrics.repeated_verification_failures,
        "required_verification_commands": process_metrics.required_verification_commands,
        "executed_required_verification_commands": (
            process_metrics.executed_required_verification_commands
        ),
        "failed_command_categories": process_metrics.failed_command_categories,
        "required_verification_first_pass": process_metrics.required_verification_first_pass,
        "first_pass_verification_successes": process_metrics.first_pass_verification_successes,
        "first_pass_verification_failures": process_metrics.first_pass_verification_failures,
        "missing_required_verification_commands": (
            process_metrics.missing_required_verification_commands
        ),
        "git_commit_verification_bypass_commands": (
            process_metrics.git_commit_verification_bypass_commands
        ),
    }


def _scorecard_metadata(request: ScorecardMetadataInput) -> dict[str, Any]:
    return {
        "run": _scorecard_run_metadata(
            request.layout,
            unscored=request.unscored,
            unscored_reasons=request.unscored_reasons,
        ),
        "starter": request.artifacts.starter_meta,
        "scenario": request.artifacts.scenario_revision_meta,
        "harbor": _scorecard_harbor_metadata(
            request.execution,
            request.artifacts,
            request.harness,
        ),
        "harness": {"artifacts": request.artifacts.harness_artifacts},
        "runner_evidence": _scorecard_runner_evidence_metadata(request.artifacts),
        "process": _scorecard_process_metadata(request.execution.process_metrics),
        "evidence": request.artifacts.evidence_artifacts,
        "workspace": {
            "prune": request.artifacts.workspace_prune,
            "changes": request.artifacts.workspace_changes,
        },
    }


def _result_manifest(
    execution: ExecutionPhaseResult,
    harness: str,
) -> HarborResultManifest | None:
    trial_dir = execution.harbor_result.trial_dir
    if trial_dir is None:
        return None
    return HarborResultManifest.for_trial(trial_dir, harness)


def build_scorecard(context: ScorecardBuildContext) -> Scorecard:
    """Create scorecard with populated metrics and metadata."""

    execution = context.execution
    execution_validity = build_execution_validity_score(_execution_validity_input(context))
    performance_gates = build_performance_gates_score(outputs=execution.outputs)
    resource_efficiency = build_resource_efficiency_score(execution.process_metrics)
    unscored_reasons = _classify_unscored_reasons(
        execution.terminated_early,
        execution.termination_reason,
        execution.failure_code,
    )
    unscored = len(unscored_reasons) > 0
    metadata = _scorecard_metadata(_scorecard_metadata_input(context, unscored, unscored_reasons))
    return _scorecard_from_context(
        context,
        ScorecardComponents(
            execution_validity=execution_validity,
            performance_gates=performance_gates,
            resource_efficiency=resource_efficiency,
            unscored=unscored,
            unscored_reasons=unscored_reasons,
            metadata=metadata,
        ),
    )


def _execution_validity_input(context: ScorecardBuildContext) -> ExecutionValidityInput:
    return ExecutionValidityInput(
        outputs=context.execution.outputs,
        terminated_early=context.execution.terminated_early,
        termination_reason=context.execution.termination_reason,
        process_metrics=context.execution.process_metrics,
        events=context.execution.events,
        workspace_path=context.context.workspace,
        atomic_commits_required=context.request.scenario.verification.workflow.atomic_commits_required,
        verification_patterns=_verification_command_strings(context.request.scenario),
    )


def _scorecard_metadata_input(
    context: ScorecardBuildContext,
    unscored: bool,
    unscored_reasons: list[str],
) -> ScorecardMetadataInput:
    return ScorecardMetadataInput(
        layout=context.layout,
        execution=context.execution,
        artifacts=context.artifacts,
        harness=context.request.config.harness.value,
        unscored=unscored,
        unscored_reasons=unscored_reasons,
    )


def _scorecard_from_context(
    context: ScorecardBuildContext,
    components: ScorecardComponents,
) -> Scorecard:
    request = context.request
    layout = context.layout
    execution = context.execution
    metric_scores = build_metric_scores(
        context,
        execution_validity=components.execution_validity,
        resource_efficiency=components.resource_efficiency,
    )
    return assemble_scorecard(
        CanonicalScorecardInput(
            scenario=request.scenario,
            run_id=layout.run_id,
            harness=request.config.harness.value,
            model=request.config.model.qualified_name,
            duration_sec=execution.duration_sec,
            terminated_early=execution.terminated_early,
            termination_reason=execution.termination_reason,
            unscored=components.unscored,
            unscored_reasons=components.unscored_reasons,
            execution_validity=components.execution_validity,
            performance_gates=components.performance_gates,
            resource_efficiency=components.resource_efficiency,
            metric_scores=metric_scores,
            metadata=components.metadata,
        )
    )


def assemble_scorecard(request: CanonicalScorecardInput) -> Scorecard:
    """Assemble a scorecard using canonical resolved-scorer weighting."""

    scorer_results = build_scorer_results_for_scenario(request.scenario, request.metric_scores)
    performance_gates = canonical_performance_gates(
        request.performance_gates,
        quality_score=_quality_score_from_scorers(scorer_results),
        min_quality_score=request.scenario.verification.min_quality_score,
    )
    return Scorecard(
        run_id=request.run_id,
        scenario_name=request.scenario.name,
        scenario_revision=request.scenario.scenario_revision,
        harness=request.harness,
        model=request.model,
        starter_root=request.scenario.starter.root,
        duration_sec=request.duration_sec,
        terminated_early=request.terminated_early,
        termination_reason=request.termination_reason,
        unscored=request.unscored,
        unscored_reasons=request.unscored_reasons,
        execution_validity=request.execution_validity,
        performance_gates=performance_gates,
        resource_efficiency=request.resource_efficiency,
        metric_scores=request.metric_scores,
        scorer_results=scorer_results,
        metadata=request.metadata,
    )


def _quality_score_from_scorers(scorer_results) -> float:
    quality_results = [result for result in scorer_results if result.category == "quality"]
    total_weight = sum(result.weight for result in quality_results if result.weight > 0)
    if total_weight <= 0:
        return 0.0
    return (
        sum(result.score * result.weight for result in quality_results if result.weight > 0)
        / total_weight
    )
