"""Workspace preparation phase for one evaluation run."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from raidar.agents.adapters.factory import resolve_adapter
from raidar.runtime import (
    harbor_cleanup,
    harbor_env,
    harbor_execution,
    models,
    starter_preflight,
    task_bundle,
    task_images,
    workspace,
    workspace_artifacts,
)

TaskImageEnsureRequest = task_images.TaskImageEnsureRequest
RunLayout = models.RunLayout
RunRequest = models.RunRequest
WorkspaceContext = models.WorkspaceContext
WorkspacePreparationPhaseResult = models.WorkspacePreparationPhaseResult
_build_harbor_run_env = harbor_env._build_harbor_run_env
_ensure_task_image = task_images._ensure_task_image
_harbor_process_timeout = harbor_execution._harbor_process_timeout
_maybe_run_cache_maintenance = task_images._maybe_run_cache_maintenance
_resolve_homepage_screenshot_command = workspace_artifacts._resolve_homepage_screenshot_command
_resolve_contract = task_bundle._resolve_contract
_task_image_capability_inputs = task_bundle._task_image_capability_inputs
_task_image_reference = task_bundle._task_image_reference
cleanup_stale_harbor_resources = harbor_cleanup.cleanup_stale_harbor_resources
create_harbor_task_bundle = task_bundle.create_harbor_task_bundle
ensure_starter_preflight = starter_preflight.ensure_starter_preflight
initialize_run = workspace.initialize_run
prepare_run_context = workspace.prepare_run_context


@dataclass(frozen=True, slots=True)
class _WorkspacePrepStart:
    request: RunRequest
    adapter: Any
    contract: Any
    layout: RunLayout
    timings: dict[str, float]
    cache_metadata: dict[str, object]
    started_at: float


@dataclass(frozen=True, slots=True)
class _BaselinePrep:
    request: RunRequest
    adapter: Any
    contract: Any
    layout: RunLayout
    context: WorkspaceContext
    timings: dict[str, float]
    cache_metadata: dict[str, object]
    started_at: float


@dataclass(frozen=True, slots=True)
class _BundlePrep:
    request: RunRequest
    adapter: Any
    contract: Any
    layout: RunLayout
    context: WorkspaceContext
    timings: dict[str, float]
    cache_metadata: dict[str, object]
    started_at: float
    screenshot_command: list[str] | None
    evidence_errors: list[str]
    harbor_task_bundle: Path
    run_env: dict[str, str]


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
        "contract": {"id": None, "hash": None, "cache_payload": None},
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


def _prepare_workspace_start(request: RunRequest) -> _WorkspacePrepStart:
    adapter = resolve_adapter(request.config)
    adapter.validate()
    contract = _resolve_contract(request)
    return _WorkspacePrepStart(
        request=request,
        adapter=adapter,
        contract=contract,
        layout=initialize_run(request),
        timings={},
        cache_metadata=_initial_cache_metadata(),
        started_at=time.perf_counter(),
    )


def _prepare_baseline_context(start: _WorkspacePrepStart) -> _BaselinePrep:
    phase_started = time.perf_counter()
    context = prepare_run_context(start.request)
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
        request=start.request,
        adapter=start.adapter,
        contract=start.contract,
        layout=start.layout,
        context=context,
        timings=timings,
        cache_metadata=cache_metadata,
        started_at=start.started_at,
    )


def _prepare_preflight(prep: _BaselinePrep) -> _BaselinePrep:
    phase_started = time.perf_counter()
    cleanup_stale_harbor_resources(
        include_containers=True,
        include_build_processes=True,
    )
    timings = _with_timing(prep.timings, "cleanup_stale_harbor_resources", phase_started)

    phase_started = time.perf_counter()
    preflight_hit = ensure_starter_preflight(prep.request, prep.context)
    timings = _with_timing(timings, "ensure_starter_preflight", phase_started)
    cache_metadata = _with_cache_entry(prep.cache_metadata, "preflight", {"hit": preflight_hit})
    return replace(prep, timings=timings, cache_metadata=cache_metadata)


def _prepare_visual_inputs(prep: _BaselinePrep) -> tuple[list[str] | None, list[str]]:
    screenshot_command = _resolve_homepage_screenshot_command(
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
    harbor_task_bundle = create_harbor_task_bundle(
        prep.request,
        prep.context,
        bundle_root=prep.layout.harbor_dir / "bundle",
        contract=prep.contract,
    )
    timings = _with_timing(prep.timings, "create_harbor_task_bundle", phase_started)
    return _BundlePrep(
        request=prep.request,
        adapter=prep.adapter,
        contract=prep.contract,
        layout=prep.layout,
        context=prep.context,
        timings=timings,
        cache_metadata=prep.cache_metadata,
        started_at=prep.started_at,
        screenshot_command=screenshot_command,
        evidence_errors=evidence_errors,
        harbor_task_bundle=harbor_task_bundle,
        run_env=_build_harbor_run_env(prep.adapter, prep.contract.runtime_profile),
    )


def _prepare_task_image(prep: _BundlePrep) -> _BundlePrep:
    cache_metadata = dict(prep.cache_metadata)
    cache_metadata["contract"] = {
        "id": prep.contract.id,
        "hash": prep.contract.contract_hash,
        "cache_payload": prep.contract.cache_payload,
    }
    image_ref = _task_image_reference(prep.request, prep.harbor_task_bundle, prep.contract)
    provided_capabilities, required_capabilities = _task_image_capability_inputs(
        prep.request,
        prep.contract,
    )
    if image_ref:
        cache_metadata["image_key"] = image_ref.cache_key
        cache_metadata["image_tag"] = image_ref.tag
    _maybe_run_cache_maintenance(
        run_env=prep.run_env,
        active_image_name=image_ref.image_name if image_ref else None,
        runtime_profile=prep.contract.runtime_profile,
    )

    phase_started = time.perf_counter()
    image_hit = None
    if image_ref:
        image_hit = _ensure_task_image(
            TaskImageEnsureRequest(
                task_bundle_path=prep.harbor_task_bundle,
                image_ref=image_ref,
                harness=prep.request.config.harness.value,
                run_env=prep.run_env,
                log_dir=prep.layout.harbor_dir,
                task_timeout_sec=prep.request.config.timeout_sec,
                provided_capabilities=provided_capabilities,
                required_capabilities=required_capabilities,
                runtime_profile=prep.contract.runtime_profile,
            )
        )
    timings = _with_timing(prep.timings, "_ensure_task_image", phase_started)
    cache_metadata["image"] = {"hit": image_hit}
    return replace(prep, timings=timings, cache_metadata=cache_metadata)


def _harbor_request(prep: _BundlePrep) -> models.HarborExecutionRequest:
    return models.HarborExecutionRequest(
        adapter=prep.adapter,
        workspace=prep.context.workspace,
        task_bundle_path=prep.harbor_task_bundle,
        jobs_dir=prep.layout.harbor_dir / "raw",
        run_harbor_dir=prep.layout.harbor_dir,
        run_id=prep.layout.run_id,
        timeout_sec=_harbor_process_timeout(prep.request.config.timeout_sec),
        run_env=prep.run_env,
    )


def _workspace_preparation_result(prep: _BundlePrep) -> WorkspacePreparationPhaseResult:
    prep_total_sec = round(time.perf_counter() - prep.started_at, 3)
    return WorkspacePreparationPhaseResult(
        layout=prep.layout,
        context=prep.context,
        harbor_request=_harbor_request(prep),
        prep_phase_timings_sec=prep.timings,
        prep_total_sec=prep_total_sec,
        time_to_experiment_start_sec=prep_total_sec,
        cache_metadata=prep.cache_metadata,
        auth_metadata=prep.adapter.execution_metadata(),
        screenshot_command=tuple(prep.screenshot_command) if prep.screenshot_command else None,
        evidence_errors=tuple(prep.evidence_errors),
    )


def prepare_workspace_phase(request: RunRequest) -> WorkspacePreparationPhaseResult:
    """Workspace prep phase: context, preflight, and Harbor bundle creation."""

    start = _prepare_workspace_start(request)
    baseline = _prepare_baseline_context(start)
    baseline = _prepare_preflight(baseline)
    screenshot_command, evidence_errors = _prepare_visual_inputs(baseline)
    bundle = _prepare_harbor_bundle(baseline, screenshot_command, evidence_errors)
    bundle = _prepare_task_image(bundle)
    return _workspace_preparation_result(bundle)
