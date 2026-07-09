"""Harbor execution phase for one evaluation run."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from raidar.runtime import (
    artifacts as runtime_artifacts,
)
from raidar.runtime import (
    harbor_execution,
    models,
    process_metrics,
    scorecard,
    trace_events,
)

_empty_process_metrics = process_metrics._empty_process_metrics
_load_verifier_outputs = runtime_artifacts._load_verifier_outputs
collect_process_metrics = process_metrics.collect_process_metrics
ProcessMetricsError = process_metrics.ProcessMetricsError
collect_trace_events = trace_events.collect_trace_events
execute_harbor = harbor_execution.execute_harbor
terminated_outputs = scorecard.terminated_outputs
ExecutionPhaseResult = models.ExecutionPhaseResult


def _resolve_harbor_outputs(
    harbor_result: Any,
    terminated_early: bool,
    termination_reason: str | None,
    failure_code: str | None,
) -> tuple[Any, bool, str | None]:
    verifier_outputs = None
    verifier_reason = None
    if harbor_result.trial_dir is not None:
        verifier_outputs, verifier_reason = _load_verifier_outputs(harbor_result.trial_dir)

    recovered_from_timeout = (
        terminated_early and verifier_outputs is not None and failure_code == "harbor_timeout"
    )
    if recovered_from_timeout:
        return verifier_outputs, False, None
    if not terminated_early and verifier_outputs is None:
        return None, True, verifier_reason
    if terminated_early:
        return terminated_outputs(termination_reason), True, termination_reason
    return verifier_outputs, False, None


def execute_harbor_phase(request: Any, phase: Any) -> ExecutionPhaseResult:
    """Harbor execution phase with verifier output loading."""

    harbor_result = execute_harbor(phase.harbor_request)
    terminated_early = harbor_result.terminated_early
    termination_reason = harbor_result.termination_reason
    failure_code = harbor_result.failure_code
    try:
        runtime_metrics = collect_process_metrics(
            request.scenario,
            harbor_result.trial_dir,
            harness=request.config.harness.value,
        )
    except ProcessMetricsError as exc:
        if terminated_early and exc.failure_code == "missing_token_usage":
            runtime_metrics = _empty_process_metrics()
        else:
            raise
    events = collect_trace_events(
        harbor_result.trial_dir,
        harness=request.config.harness.value,
    )

    outputs, terminated_early, termination_reason = _resolve_harbor_outputs(
        harbor_result,
        terminated_early,
        termination_reason,
        failure_code,
    )
    if outputs is None:
        outputs = terminated_outputs("Verifier outputs unavailable.")

    duration_sec = (datetime.now(UTC) - phase.layout.start_time).total_seconds()
    return ExecutionPhaseResult(
        harbor_result=harbor_result,
        terminated_early=terminated_early,
        termination_reason=termination_reason,
        failure_code=failure_code if terminated_early else None,
        process_metrics=runtime_metrics,
        events=events,
        outputs=outputs,
        duration_sec=duration_sec,
        prep_phase_timings_sec=getattr(phase, "prep_phase_timings_sec", {}),
        prep_total_sec=getattr(phase, "prep_total_sec", 0.0),
        time_to_experiment_start_sec=getattr(
            phase,
            "time_to_experiment_start_sec",
            getattr(phase, "prep_total_sec", 0.0),
        ),
        cache_metadata=getattr(phase, "cache_metadata", {}),
        auth_metadata=getattr(phase, "auth_metadata", {}),
    )
