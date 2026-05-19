"""Top-level runtime phase pipeline for one evaluation run."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

from raidar.agents.adapters.base import HarnessAdapter
from raidar.agents.adapters.factory import resolve_adapter
from raidar.runtime.models import (
    ExecutionPhaseResult,
    HarborExecutionRequest,
    PersistedArtifacts,
    RunLayout,
    RunRequest,
    ScorecardBuildContext,
    WorkspaceContext,
    WorkspacePreparationPhaseResult,
)
from raidar.schemas.scorecard import EvalConfig, EvalRun


@dataclass(frozen=True, slots=True)
class _WorkspacePrepStart:
    runner: ModuleType
    request: RunRequest
    adapter: HarnessAdapter
    layout: RunLayout
    timings: dict[str, float]
    cache_metadata: dict[str, object]
    started_at: float


@dataclass(frozen=True, slots=True)
class _BaselinePrep:
    runner: ModuleType
    request: RunRequest
    adapter: HarnessAdapter
    layout: RunLayout
    context: WorkspaceContext
    timings: dict[str, float]
    cache_metadata: dict[str, object]
    started_at: float


@dataclass(frozen=True, slots=True)
class _BundlePrep:
    runner: ModuleType
    request: RunRequest
    adapter: HarnessAdapter
    layout: RunLayout
    context: WorkspaceContext
    timings: dict[str, float]
    cache_metadata: dict[str, object]
    started_at: float
    screenshot_command: list[str] | None
    evidence_errors: list[str]
    harbor_task_bundle: Path
    run_env: dict[str, str]


@dataclass(frozen=True, slots=True)
class _VisualEvidenceState:
    evidence_artifacts: dict[str, object]
    hydrate_error: str | None


@dataclass(frozen=True, slots=True)
class _HydratedVisualEvidenceRequest:
    runner: ModuleType
    request: RunRequest
    phase: WorkspacePreparationPhaseResult
    execution: ExecutionPhaseResult
    evidence_artifacts: dict[str, object]


def _runner() -> ModuleType:
    from raidar import runner as runner_module

    return runner_module


def _resolve_harbor_outputs(runner, harbor_result, terminated_early, termination_reason):
    verifier_outputs = None
    verifier_reason = None
    if harbor_result.trial_dir is not None:
        verifier_outputs, verifier_reason = runner._load_verifier_outputs(harbor_result.trial_dir)

    recovered_from_timeout = (
        terminated_early
        and verifier_outputs is not None
        and termination_reason is not None
        and "timeout expired" in termination_reason.lower()
    )
    if recovered_from_timeout:
        return verifier_outputs, False, None
    if not terminated_early and verifier_outputs is None:
        return None, True, verifier_reason
    if terminated_early:
        return runner.terminated_outputs(termination_reason), True, termination_reason
    return verifier_outputs, False, None


def _initial_cache_metadata() -> dict[str, object]:
    return {
        "baseline": {
            "hit": None,
            "status": None,
            "cache_key": None,
            "workspace_dir": None,
            "metadata_path": None,
            "complete": None,
            "fingerprint": None,
        },
        "preflight": {"hit": None},
        "image": {"hit": None},
        "image_key": None,
        "image_tag": None,
    }


def _with_timing(
    timings: dict[str, float],
    key: str,
    started_at: float,
) -> dict[str, float]:
    return {**timings, key: round(time.perf_counter() - started_at, 3)}


def _with_cache_entry(
    cache_metadata: dict[str, object],
    key: str,
    value: object,
) -> dict[str, object]:
    return {**cache_metadata, key: value}


def _prepare_workspace_start(request) -> _WorkspacePrepStart:
    runner = _runner()
    adapter = resolve_adapter(request.config)
    adapter.validate()
    return _WorkspacePrepStart(
        runner=runner,
        request=request,
        adapter=adapter,
        layout=runner.initialize_run(request),
        timings={},
        cache_metadata=_initial_cache_metadata(),
        started_at=time.perf_counter(),
    )


def _prepare_baseline_context(start: _WorkspacePrepStart) -> _BaselinePrep:
    phase_started = time.perf_counter()
    context = start.runner.prepare_run_context(start.request)
    timings = _with_timing(start.timings, "prepare_run_context", phase_started)
    cache_metadata = _with_cache_entry(
        start.cache_metadata,
        "baseline",
        {
            "hit": context.baseline_cache_hit,
            "status": context.baseline_cache_status,
            "cache_key": context.baseline_cache_key,
            "workspace_dir": str(context.baseline_workspace),
            "metadata_path": str(context.baseline_metadata_path),
            "complete": True,
            "fingerprint": context.baseline_fingerprint,
        },
    )
    start.adapter.prepare_workspace(context.workspace)
    return _BaselinePrep(
        runner=start.runner,
        request=start.request,
        adapter=start.adapter,
        layout=start.layout,
        context=context,
        timings=timings,
        cache_metadata=cache_metadata,
        started_at=start.started_at,
    )


def _prepare_preflight(prep: _BaselinePrep) -> _BaselinePrep:
    phase_started = time.perf_counter()
    prep.runner.cleanup_stale_harbor_resources(
        include_containers=True,
        include_build_processes=True,
    )
    timings = _with_timing(prep.timings, "cleanup_stale_harbor_resources", phase_started)

    phase_started = time.perf_counter()
    preflight_hit = prep.runner.ensure_starter_preflight(prep.request, prep.context)
    timings = _with_timing(timings, "ensure_starter_preflight", phase_started)
    cache_metadata = _with_cache_entry(prep.cache_metadata, "preflight", {"hit": preflight_hit})
    return replace(prep, timings=timings, cache_metadata=cache_metadata)


def _prepare_visual_inputs(prep: _BaselinePrep) -> tuple[list[str] | None, list[str]]:
    screenshot_command = prep.runner._resolve_homepage_screenshot_command(
        prep.request.scenario,
        prep.context.workspace,
    )
    return screenshot_command, []


def _prepare_harbor_bundle(
    prep: _BaselinePrep,
    screenshot_command: list[str] | None,
    evidence_errors: list[str],
) -> _BundlePrep:
    phase_started = time.perf_counter()
    harbor_task_bundle = prep.runner.create_harbor_task_bundle(
        prep.request,
        prep.context,
        bundle_root=prep.layout.harbor_dir / "bundle",
    )
    timings = _with_timing(prep.timings, "create_harbor_task_bundle", phase_started)
    return _BundlePrep(
        runner=prep.runner,
        request=prep.request,
        adapter=prep.adapter,
        layout=prep.layout,
        context=prep.context,
        timings=timings,
        cache_metadata=prep.cache_metadata,
        started_at=prep.started_at,
        screenshot_command=screenshot_command,
        evidence_errors=evidence_errors,
        harbor_task_bundle=harbor_task_bundle,
        run_env=prep.runner._build_harbor_run_env(prep.adapter),
    )


def _prepare_task_image(prep: _BundlePrep) -> _BundlePrep:
    cache_metadata = dict(prep.cache_metadata)
    image_ref = prep.runner._task_image_reference(prep.request, prep.harbor_task_bundle)
    if image_ref:
        cache_metadata["image_key"] = image_ref.cache_key
        cache_metadata["image_tag"] = image_ref.tag
    prep.runner._maybe_run_cache_maintenance(
        run_env=prep.run_env,
        active_image_name=image_ref.image_name if image_ref else None,
    )

    phase_started = time.perf_counter()
    image_hit = None
    if image_ref:
        image_hit = prep.runner._ensure_task_image(
            prep.runner.TaskImageEnsureRequest(
                task_bundle_path=prep.harbor_task_bundle,
                image_ref=image_ref,
                harness=prep.request.config.harness.value,
                run_env=prep.run_env,
                log_dir=prep.layout.harbor_dir,
                task_timeout_sec=prep.request.config.timeout_sec,
            )
        )
    timings = _with_timing(prep.timings, "_ensure_task_image", phase_started)
    cache_metadata["image"] = {"hit": image_hit}
    return replace(prep, timings=timings, cache_metadata=cache_metadata)


def _harbor_request(prep: _BundlePrep) -> HarborExecutionRequest:
    harbor_request = HarborExecutionRequest(
        adapter=prep.adapter,
        workspace=prep.context.workspace,
        task_bundle_path=prep.harbor_task_bundle,
        jobs_dir=prep.layout.harbor_dir / "raw",
        run_harbor_dir=prep.layout.harbor_dir,
        run_id=prep.layout.run_id,
        timeout_sec=prep.runner._harbor_process_timeout(prep.request.config.timeout_sec),
        run_env=prep.run_env,
    )
    return harbor_request


def _workspace_preparation_result(prep: _BundlePrep) -> WorkspacePreparationPhaseResult:
    prep_total_sec = round(time.perf_counter() - prep.started_at, 3)
    return WorkspacePreparationPhaseResult(
        layout=prep.layout,
        context=prep.context,
        harbor_request=_harbor_request(prep),
        prep_phase_timings_sec=prep.timings,
        prep_total_sec=prep_total_sec,
        cache_metadata=prep.cache_metadata,
        auth_metadata=prep.adapter.execution_metadata(),
        screenshot_command=tuple(prep.screenshot_command) if prep.screenshot_command else None,
        evidence_errors=tuple(prep.evidence_errors),
    )


def prepare_workspace_phase(request):
    """Workspace prep phase: context, preflight, and Harbor bundle creation."""

    start = _prepare_workspace_start(request)
    baseline = _prepare_baseline_context(start)
    baseline = _prepare_preflight(baseline)
    screenshot_command, evidence_errors = _prepare_visual_inputs(baseline)
    bundle = _prepare_harbor_bundle(baseline, screenshot_command, evidence_errors)
    bundle = _prepare_task_image(bundle)
    return _workspace_preparation_result(bundle)


def execute_harbor_phase(request, phase):
    """Harbor execution phase with verifier output loading."""

    runner = _runner()
    harbor_result = runner.execute_harbor(phase.harbor_request)
    terminated_early = harbor_result.terminated_early
    termination_reason = harbor_result.termination_reason
    try:
        process_metrics = runner.collect_process_metrics(
            request.scenario,
            harbor_result.trial_dir,
            harness=request.config.harness.value,
        )
    except RuntimeError as exc:
        message = str(exc)
        if terminated_early and "Missing token usage metrics" in message:
            process_metrics = runner._empty_process_metrics()
        else:
            raise
    events = runner.collect_trace_events(
        harbor_result.trial_dir,
        harness=request.config.harness.value,
    )

    outputs, terminated_early, termination_reason = _resolve_harbor_outputs(
        runner,
        harbor_result,
        terminated_early,
        termination_reason,
    )
    if outputs is None:
        outputs = runner.terminated_outputs("Verifier outputs unavailable.")

    duration_sec = (datetime.now(UTC) - phase.layout.start_time).total_seconds()
    return ExecutionPhaseResult(
        harbor_result=harbor_result,
        terminated_early=terminated_early,
        termination_reason=termination_reason,
        process_metrics=process_metrics,
        events=events,
        outputs=outputs,
        duration_sec=duration_sec,
        prep_phase_timings_sec=getattr(phase, "prep_phase_timings_sec", {}),
        prep_total_sec=getattr(phase, "prep_total_sec", 0.0),
        cache_metadata=getattr(phase, "cache_metadata", {}),
        auth_metadata=getattr(phase, "auth_metadata", {}),
    )


def persist_artifacts_phase(request, phase, execution):
    """Artifact persistence phase."""

    runner = _runner()
    visual_state = _persist_visual_evidence(runner, request, phase, execution)
    evidence_artifacts = visual_state.evidence_artifacts
    if visual_state.hydrate_error:
        evidence_artifacts["errors"].append(visual_state.hydrate_error)

    workspace_prune = runner._prune_workspace_artifacts(phase.layout.workspace_dir)
    workspace_changes = runner._workspace_changes_from_baseline(
        baseline_workspace=phase.context.baseline_workspace,
        run_workspace=phase.layout.workspace_dir,
        run_root_dir=phase.layout.root_dir,
    )
    return PersistedArtifacts(
        starter_meta=runner.build_starter_meta(request, phase.context),
        scenario_revision_meta=runner.build_scenario_revision_meta(request, phase.context),
        verifier_artifacts=runner.persist_verifier_artifacts(
            execution.harbor_result, phase.layout.verifier_dir
        ),
        harness_artifacts=runner.persist_harness_artifacts(
            execution.harbor_result, phase.layout.harness_dir
        ),
        harbor_artifacts=runner.persist_harbor_artifacts(
            execution.harbor_result, phase.layout.harbor_dir
        ),
        evidence_artifacts=evidence_artifacts,
        workspace_prune=workspace_prune,
        workspace_changes=workspace_changes,
    )


def _initial_evidence_artifacts(phase) -> dict[str, object]:
    return {
        "screenshot_command": list(phase.screenshot_command) if phase.screenshot_command else None,
        "homepage_post": None,
        "final_workspace_archive": None,
        "visual": {
            "actual": None,
            "reference": None,
            "diff": None,
            "regions": [],
        },
        "errors": list(phase.evidence_errors),
    }


def _persist_visual_evidence(runner, request, phase, execution) -> _VisualEvidenceState:
    evidence_artifacts = _initial_evidence_artifacts(phase)
    if phase.screenshot_command and not execution.terminated_early:
        return _persist_hydrated_visual_evidence(
            _HydratedVisualEvidenceRequest(
                runner=runner,
                request=request,
                phase=phase,
                execution=execution,
                evidence_artifacts=evidence_artifacts,
            )
        )
    return _VisualEvidenceState(evidence_artifacts=evidence_artifacts, hydrate_error=None)


def _persist_hydrated_visual_evidence(
    request: _HydratedVisualEvidenceRequest,
) -> _VisualEvidenceState:
    archive_path, hydrate_error = request.runner._hydrate_workspace_from_final_app(
        request.execution.harbor_result,
        request.phase.context.workspace,
    )
    if archive_path:
        request.evidence_artifacts["final_workspace_archive"] = str(archive_path)
        _capture_homepage_post(request.runner, request.phase, request.evidence_artifacts)
        visual_artifacts = request.runner._persist_visual_evidence_artifacts(
            request.runner.VisualEvidenceRequest(
                request=request.request,
                workspace=request.phase.context.workspace,
                run_root_dir=request.phase.layout.root_dir,
            )
        )
        request.evidence_artifacts["visual"] = visual_artifacts
        request.runner._rebind_visual_evidence_paths(
            request.execution.outputs.visual, visual_artifacts
        )
    return _VisualEvidenceState(
        evidence_artifacts=request.evidence_artifacts, hydrate_error=hydrate_error
    )


def _capture_homepage_post(runner, phase, evidence_artifacts) -> None:
    post_path, post_error = runner._run_homepage_capture_command(
        list(phase.screenshot_command),
        phase.context.workspace,
        phase.layout.root_dir / "homepage-post.png",
    )
    if post_path:
        evidence_artifacts["homepage_post"] = str(post_path)
    if post_error:
        evidence_artifacts["errors"].append(f"homepage-post capture failed: {post_error}")


def synthesize_scorecard_phase(request, phase, execution, artifacts):
    """Score synthesis phase from persisted artifacts and execution outputs."""

    runner = _runner()
    scorecard = runner.build_scorecard(
        ScorecardBuildContext(
            request=request,
            layout=phase.layout,
            context=phase.context,
            artifacts=artifacts,
            execution=execution,
        )
    )
    runner.persist_canonical_verifier_artifacts(phase.layout, scorecard, execution.outputs)
    runner.write_run_analysis(phase.layout, request, scorecard, execution.harbor_result)
    return scorecard


def run_task(request):
    """Execute a scenario and return evaluation results."""

    runner = _runner()
    prepared = prepare_workspace_phase(request)
    execution = execute_harbor_phase(request, prepared)
    artifacts = persist_artifacts_phase(request, prepared, execution)
    scorecard = synthesize_scorecard_phase(request, prepared, execution, artifacts)
    return EvalRun(
        id=prepared.layout.run_id,
        timestamp=prepared.layout.start_time.isoformat(),
        config=EvalConfig(
            model=request.config.model.qualified_name,
            harness=request.config.harness.value,
            scenario_name=request.scenario.name,
            scenario_revision=request.scenario.scenario_revision,
            starter_root=request.scenario.starter.root,
            evaluation_profile=runner.scenario_evaluation_profile(request.scenario),
        ),
        duration_sec=execution.duration_sec,
        terminated_early=execution.terminated_early,
        termination_reason=execution.termination_reason,
        scores=scorecard,
        events=execution.events,
        gate_history=execution.outputs.gate_history,
    )
