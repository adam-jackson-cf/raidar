"""Scorecard synthesis and evaluation scoring services."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.config import settings
from raidar.runtime.harbor_results import (
    _harbor_phase_timings,
    _load_json_dict,
    _verifier_scorecard_path,
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
    build_scorer_results,
    canonical_performance_gates,
)
from raidar.runtime.verification_metrics import (
    _count_executed_required,
    _observed_verification_attempts,
    _verification_command_strings,
)
from raidar.schemas.events import GateEvent, TraceEvent
from raidar.schemas.scenario import RequirementSpec
from raidar.schemas.scorecard import (
    AcceptanceCheck,
    AcceptanceScore,
    CoverageScore,
    ExecutionValidityScore,
    FunctionalScore,
    GateCheck,
    PerformanceGatesScore,
    RequirementsCoverageScore,
    ResourceEfficiencyScore,
    Scorecard,
    VerificationStabilityScore,
)
from raidar.scorers.deterministic import run_deterministic_check


@dataclass(frozen=True, slots=True)
class ScorecardMetadataInput:
    """Input for scorecard metadata assembly."""

    layout: RunLayout
    execution: ExecutionPhaseResult
    artifacts: PersistedArtifacts
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


def _coverage_from_summary_file(workspace: Path) -> tuple[float | None, str | None]:
    summary_path = workspace / "coverage" / "coverage-summary.json"
    if not summary_path.exists():
        return None, None
    try:
        payload = json.loads(summary_path.read_text())
    except json.JSONDecodeError:
        return None, None
    total = payload.get("total")
    if not isinstance(total, dict):
        return None, None
    values: list[float] = []
    for key in ("lines", "statements", "functions", "branches"):
        metric = total.get(key)
        if not isinstance(metric, dict):
            continue
        pct = metric.get("pct")
        if isinstance(pct, (int, float)):
            values.append(float(pct))
    if not values:
        return None, None
    return min(values) / 100.0, str(summary_path)


def _parse_coverage_percent(output: str) -> float | None:
    values: list[float] = []
    for pattern in (
        r"Lines\s*:\s*([0-9]+(?:\.[0-9]+)?)%",
        r"Statements\s*:\s*([0-9]+(?:\.[0-9]+)?)%",
        r"Functions\s*:\s*([0-9]+(?:\.[0-9]+)?)%",
        r"Branches\s*:\s*([0-9]+(?:\.[0-9]+)?)%",
    ):
        values.extend(float(match) for match in re.findall(pattern, output, re.IGNORECASE))
    table_match = re.search(
        (
            r"All files\s*\|\s*([0-9]+(?:\.[0-9]+)?)\s*\|\s*([0-9]+(?:\.[0-9]+)?)\s*\|\s*"
            r"([0-9]+(?:\.[0-9]+)?)\s*\|\s*([0-9]+(?:\.[0-9]+)?)"
        ),
        output,
    )
    if table_match:
        values.extend(float(value) for value in table_match.groups())
    if not values:
        return None
    return min(values) / 100.0


def _coverage_from_gate_history(gate_history: list[GateEvent]) -> tuple[float | None, str | None]:
    for event in reversed(gate_history):
        gate_hint = f"{event.gate_name} {event.command}".lower()
        if "coverage" not in gate_hint:
            continue
        parsed = _parse_coverage_percent(f"{event.stdout}\n{event.stderr}")
        if parsed is not None:
            return parsed, f"gate:{event.gate_name}"
    return None, None


def evaluate_coverage(
    workspace: Path,
    gate_history: list[GateEvent],
    threshold: float | None,
) -> CoverageScore:
    """Evaluate whether measured test coverage meets the configured threshold."""
    measured, source = _coverage_from_summary_file(workspace)
    if measured is None:
        measured, source = _coverage_from_gate_history(gate_history)
    passed = threshold is None or (measured is not None and measured >= threshold)
    return CoverageScore(
        threshold=threshold,
        measured=measured,
        source=source,
        passed=passed,
    )


def _test_file_paths(workspace: Path) -> list[Path]:
    patterns = (
        "**/*.test.ts",
        "**/*.test.tsx",
        "**/*.spec.ts",
        "**/*.spec.tsx",
    )
    test_paths: list[Path] = []
    for pattern in patterns:
        test_paths.extend((workspace / "src").glob(pattern))
    return test_paths


def _test_evidence_label(evidence: dict[str, Any]) -> str:
    evidence_type = str(evidence.get("type", "unknown"))
    if evidence_type == "query_role":
        role = str(evidence.get("role", "unknown"))
        min_count = int(evidence.get("min_count", 1) or 1)
        parts = [role]
        if evidence.get("level") is not None:
            parts.append(f"level={evidence['level']}")
        if evidence.get("name"):
            parts.append(f"name={evidence['name']}")
        return f"query_role:{','.join(parts)} x{min_count}"
    if evidence_type == "query_text":
        pattern = str(evidence.get("pattern", "unknown"))
        min_count = int(evidence.get("min_count", 1) or 1)
        return f"query_text:{pattern} x{min_count}"
    return evidence_type


def _count_role_query_matches(test_sources: list[str], evidence: dict[str, Any]) -> int:
    role = re.escape(str(evidence.get("role", "")))
    if not role:
        return 0
    query_pattern = re.compile(
        r"(?:screen\.)?(?:get|find|query)(?:All)?ByRole\s*\(\s*(['\"])"
        + role
        + r"\1(?P<options>\s*,\s*\{[\s\S]*?\})?",
        re.MULTILINE,
    )
    level = evidence.get("level")
    name = evidence.get("name")
    count = 0
    for source in test_sources:
        for match in query_pattern.finditer(source):
            options = match.group("options") or ""
            if level is not None and not re.search(rf"level\s*:\s*{int(level)}\b", options):
                continue
            if name is not None and not re.search(re.escape(str(name)), options, re.IGNORECASE):
                continue
            count += 1
    return count


def _count_text_query_matches(test_sources: list[str], evidence: dict[str, Any]) -> int:
    pattern = str(evidence.get("pattern", ""))
    if not pattern:
        return 0
    count = 0
    query_pattern = re.compile(r"(?:screen\.)?(?:get|find|query)(?:All)?ByText\s*\(", re.MULTILINE)
    for source in test_sources:
        if not query_pattern.search(source):
            continue
        count += len(re.findall(pattern, source, re.MULTILINE | re.IGNORECASE))
    return count


def _missing_test_evidence(
    test_sources: list[str],
    required_test_evidence: list[Any],
) -> list[str]:
    missing: list[str] = []
    for evidence in required_test_evidence:
        payload = evidence.model_dump(mode="json") if hasattr(evidence, "model_dump") else evidence
        evidence_type = payload.get("type")
        min_count = int(payload.get("min_count", 1) or 1)
        if evidence_type == "query_role":
            matched = _count_role_query_matches(test_sources, payload)
        elif evidence_type == "query_text":
            matched = _count_text_query_matches(test_sources, payload)
        else:
            matched = 0
        if matched < min_count:
            missing.append(_test_evidence_label(payload))
    return missing


def evaluate_requirements(
    workspace: Path,
    requirements: list[RequirementSpec],
) -> RequirementsCoverageScore:
    """Evaluate requirement implementation and optional requirement-to-test mapping."""
    if not requirements:
        return RequirementsCoverageScore()

    test_sources = [path.read_text(errors="ignore") for path in _test_file_paths(workspace)]
    missing_ids: list[str] = []
    gap_ids: list[str] = []
    evidence_gaps: dict[str, list[str]] = {}
    satisfied = 0
    mapped = 0
    mapped_satisfied = 0

    for requirement in requirements:
        requirement_check, missing_patterns = _requirement_status(
            workspace, requirement, test_sources
        )
        if requirement_check.passed:
            satisfied += 1
        else:
            missing_ids.append(requirement.id)

        mapped_for_requirement = not missing_patterns
        mapped, mapped_satisfied = _apply_requirement_mapping_counts(
            mapped=mapped,
            mapped_satisfied=mapped_satisfied,
            mapped_for_requirement=mapped_for_requirement,
            requirement_passed=requirement_check.passed,
        )
        if missing_patterns:
            gap_ids.append(requirement.id)
            evidence_gaps[requirement.id] = missing_patterns

    return RequirementsCoverageScore(
        total_requirements=len(requirements),
        satisfied_requirements=satisfied,
        mapped_requirements=mapped,
        mapped_satisfied_requirements=mapped_satisfied,
        missing_requirement_ids=missing_ids,
        requirement_gap_ids=gap_ids,
        requirement_test_evidence_gaps=evidence_gaps,
    )


def _requirement_status(
    workspace: Path,
    requirement: RequirementSpec,
    test_sources: list[str],
) -> tuple[AcceptanceCheck, list[str]]:
    requirement_check = run_deterministic_check(requirement.check, workspace)
    missing_evidence = _missing_test_evidence(test_sources, requirement.required_test_evidence)
    return requirement_check, missing_evidence


def _apply_requirement_mapping_counts(
    *,
    mapped: int,
    mapped_satisfied: int,
    mapped_for_requirement: bool,
    requirement_passed: bool,
) -> tuple[int, int]:
    if not mapped_for_requirement:
        return mapped, mapped_satisfied
    mapped += 1
    if requirement_passed:
        mapped_satisfied += 1
    return mapped, mapped_satisfied


def terminated_outputs(reason: str | None) -> EvaluationOutputs:
    """Create deterministic zeroed scores for terminated runs."""
    failure_reason = reason or "Run terminated before scoring."
    return EvaluationOutputs(
        functional=_terminated_functional_score(),
        acceptance=_terminated_acceptance_score(failure_reason),
        visual=None,
        verification_stability=_terminated_verification_stability_score(),
        test_coverage=_terminated_coverage_score(),
        requirements_coverage=_terminated_requirements_coverage_score(),
        execution_validity=_terminated_execution_validity_score(failure_reason),
        performance_gates=PerformanceGatesScore(checks=[]),
        metric_scores=[],
        gate_history=[],
    )


def _terminated_functional_score() -> FunctionalScore:
    return FunctionalScore(
        passed=False,
        tests_passed=0,
        tests_total=0,
        build_succeeded=False,
        gates_passed=0,
        gates_total=0,
    )


def _terminated_acceptance_score(failure_reason: str) -> AcceptanceScore:
    return AcceptanceScore(
        checks=[
            AcceptanceCheck(
                rule="Evaluation run completed",
                type="deterministic",
                passed=False,
                evidence=failure_reason,
            )
        ]
    )


def _terminated_verification_stability_score() -> VerificationStabilityScore:
    return VerificationStabilityScore(
        total_gate_failures=settings.verification_stability.max_gate_failures,
        unique_failure_categories=0,
        repeat_failures=0,
    )


def _terminated_coverage_score() -> CoverageScore:
    return CoverageScore(
        threshold=None,
        measured=None,
        source=None,
        passed=False,
    )


def _terminated_requirements_coverage_score() -> RequirementsCoverageScore:
    return RequirementsCoverageScore(
        total_requirements=0,
        satisfied_requirements=0,
        mapped_requirements=0,
        missing_requirement_ids=[],
        requirement_gap_ids=[],
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


def _all_gates_passed(outputs: EvaluationOutputs) -> bool:
    return outputs.functional.gates_total == outputs.functional.gates_passed


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
    _upsert_gate_check(checks, _required_verification_check(validity_input))
    _upsert_gate_check(checks, _commit_bypass_check(validity_input.process_metrics))

    commit_count, commit_evidence = _git_commit_count(validity_input.workspace_path)
    atomic_commits_present = commit_count > 0
    if validity_input.atomic_commits_required:
        _upsert_gate_check(checks, _atomic_commits_check(commit_evidence, atomic_commits_present))
    _upsert_gate_check(
        checks,
        _completion_claim_consistent(
            validity_input.events,
            _all_gates_passed(validity_input.outputs),
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


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def _classify_unscored_reasons(terminated_early: bool, termination_reason: str | None) -> list[str]:
    """Classify harness/provider issues that unscore a run and require a rerun."""
    if not terminated_early and not termination_reason:
        return []

    reason = (termination_reason or "").lower()
    rules: list[tuple[str, tuple[str, ...]]] = [
        ("harbor_timeout", ("timeout expired",)),
        ("compose_version_unsupported", ("unsupported docker compose version",)),
        ("provider_rate_limit", ("rate limit",)),
        ("provider_stream_disconnect", ("stream disconnected before completion",)),
        ("harness_unavailable", ("harbor not installed",)),
        ("harbor_cli_failure", ("harbor exited with code",)),
        ("harbor_trial_exception", ("harbor trial exception",)),
    ]
    reasons: list[str] = []
    for code, patterns in rules:
        if _contains_any(reason, patterns):
            reasons.append(code)
    if "codex turn failed" in reason and not reasons:
        reasons.append("provider_or_harness_turn_failure")

    return list(dict.fromkeys(reasons))


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
    execution: ExecutionPhaseResult, artifacts: PersistedArtifacts
) -> dict[str, Any]:
    harbor_timings = _harbor_phase_timings(execution.harbor_result.trial_dir)
    trial_total_sec = harbor_timings.get("trial_total_sec")
    orchestration_overhead_excluding_test_sec = (
        round(max(0.0, execution.duration_sec - trial_total_sec), 3)
        if trial_total_sec is not None
        else None
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
        "phase_timings_sec": harbor_timings,
        "harness_overhead_sec": orchestration_overhead_excluding_test_sec,
        "orchestration_overhead_excluding_test_sec": orchestration_overhead_excluding_test_sec,
        "cache": execution.cache_metadata,
        "auth": execution.auth_metadata,
        "artifacts": artifacts.harbor_artifacts,
    }


def _scorecard_verifier_metadata(
    execution: ExecutionPhaseResult, artifacts: PersistedArtifacts
) -> dict[str, Any]:
    verifier_scorecard_path = _verifier_scorecard_path(execution.harbor_result.trial_dir)
    verifier_payload = _load_json_dict(verifier_scorecard_path) if verifier_scorecard_path else {}
    verifier_metadata = verifier_payload.get("metadata")
    command_timings = (
        verifier_metadata.get("command_timings_sec")
        if isinstance(verifier_metadata, dict)
        else None
    )
    return {
        "scorecard": str(verifier_scorecard_path) if verifier_scorecard_path else None,
        "artifacts": artifacts.verifier_artifacts,
        "command_timings_sec": command_timings,
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
        "harbor": _scorecard_harbor_metadata(request.execution, request.artifacts),
        "harness": {"artifacts": request.artifacts.harness_artifacts},
        "verifier": _scorecard_verifier_metadata(request.execution, request.artifacts),
        "process": _scorecard_process_metadata(request.execution.process_metrics),
        "evidence": request.artifacts.evidence_artifacts,
        "workspace": {
            "prune": request.artifacts.workspace_prune,
            "changes": request.artifacts.workspace_changes,
        },
    }


def build_scorecard(context: ScorecardBuildContext) -> Scorecard:
    """Create scorecard with populated metrics and metadata."""

    execution = context.execution
    outputs = execution.outputs
    execution_validity = build_execution_validity_score(_execution_validity_input(context))
    performance_gates = build_performance_gates_score(outputs=outputs)
    resource_efficiency = build_resource_efficiency_score(execution.process_metrics)
    unscored_reasons = _classify_unscored_reasons(
        execution.terminated_early,
        execution.termination_reason,
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
    outputs = execution.outputs
    metric_scores = build_metric_scores(
        context,
        execution_validity=components.execution_validity,
        resource_efficiency=components.resource_efficiency,
    )
    scorer_results = build_scorer_results(context, metric_scores)
    performance_gates = canonical_performance_gates(
        components.performance_gates,
        quality_score=_quality_score_from_scorers(scorer_results),
        min_quality_score=request.scenario.verification.min_quality_score,
    )
    return Scorecard(
        run_id=layout.run_id,
        scenario_name=request.scenario.name,
        scenario_revision=request.scenario.scenario_revision,
        harness=request.config.harness.value,
        model=request.config.model.qualified_name,
        starter_root=request.scenario.starter.root,
        duration_sec=execution.duration_sec,
        terminated_early=execution.terminated_early,
        termination_reason=execution.termination_reason,
        unscored=components.unscored,
        unscored_reasons=components.unscored_reasons,
        functional=outputs.functional,
        acceptance=outputs.acceptance,
        visual=outputs.visual,
        verification_stability=outputs.verification_stability,
        test_coverage=outputs.test_coverage,
        requirements_coverage=outputs.requirements_coverage,
        execution_validity=components.execution_validity,
        performance_gates=performance_gates,
        resource_efficiency=components.resource_efficiency,
        metric_scores=metric_scores,
        scorer_results=scorer_results,
        metadata=components.metadata,
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
