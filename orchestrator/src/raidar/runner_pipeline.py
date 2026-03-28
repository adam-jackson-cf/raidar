"""Top-level run pipeline extracted from the runner module."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from raidar.schemas.scorecard import EvalConfig, EvalRun


def _runner():
    from raidar import runner as runner_module

    return runner_module


def prepare_workspace_phase(request):
    """Workspace prep phase: context, preflight, and Harbor bundle creation."""

    runner = _runner()
    prep_started = time.perf_counter()
    layout = runner.initialize_run(request)
    adapter = request.config.adapter()
    adapter.validate()
    auth_metadata = adapter.execution_metadata()

    prep_phase_timings: dict[str, float] = {}
    cache_metadata: dict[str, object] = {
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

    phase_started = time.perf_counter()
    context = runner.prepare_run_context(request)
    prep_phase_timings["prepare_run_context"] = round(time.perf_counter() - phase_started, 3)
    cache_metadata["baseline"] = {
        "hit": context.baseline_cache_hit,
        "status": context.baseline_cache_status,
        "cache_key": context.baseline_cache_key,
        "workspace_dir": str(context.baseline_workspace),
        "metadata_path": str(context.baseline_metadata_path),
        "complete": True,
        "fingerprint": context.baseline_fingerprint,
    }

    adapter.prepare_workspace(context.workspace)

    phase_started = time.perf_counter()
    runner.cleanup_stale_harbor_resources(include_containers=True, include_build_processes=True)
    prep_phase_timings["cleanup_stale_harbor_resources"] = round(
        time.perf_counter() - phase_started, 3
    )

    phase_started = time.perf_counter()
    preflight_hit = runner.ensure_starter_preflight(request, context)
    prep_phase_timings["ensure_starter_preflight"] = round(time.perf_counter() - phase_started, 3)
    cache_metadata["preflight"] = {"hit": preflight_hit}

    screenshot_command = runner._resolve_homepage_screenshot_command(
        request.scenario, context.workspace
    )
    evidence_errors: list[str] = []

    phase_started = time.perf_counter()
    harbor_task_bundle = runner.create_harbor_task_bundle(
        request,
        context,
        bundle_root=layout.harbor_dir / "bundle",
    )
    prep_phase_timings["create_harbor_task_bundle"] = round(time.perf_counter() - phase_started, 3)

    run_env = runner._build_harbor_run_env(adapter)
    image_ref = runner._fast_task_image_reference(request, harbor_task_bundle)
    if image_ref:
        cache_metadata["image_key"] = image_ref.cache_key
        cache_metadata["image_tag"] = image_ref.tag
    runner._maybe_run_cache_maintenance(
        run_env=run_env,
        active_image_name=image_ref.image_name if image_ref else None,
    )

    phase_started = time.perf_counter()
    image_hit = None
    if image_ref:
        image_hit = runner._ensure_fast_task_image(
            task_bundle_path=harbor_task_bundle,
            image_ref=image_ref,
            harness=request.config.harness.value,
            run_env=run_env,
            log_dir=layout.harbor_dir,
        )
    prep_phase_timings["_ensure_fast_task_image"] = round(time.perf_counter() - phase_started, 3)
    cache_metadata["image"] = {"hit": image_hit}

    harbor_request = runner.HarborExecutionRequest(
        adapter=adapter,
        workspace=context.workspace,
        task_bundle_path=harbor_task_bundle,
        jobs_dir=layout.harbor_dir / "raw",
        run_harbor_dir=layout.harbor_dir,
        run_id=layout.run_id,
        timeout_sec=runner._harbor_process_timeout(request.config.timeout_sec),
        run_env=run_env,
    )
    prep_total_sec = round(time.perf_counter() - prep_started, 3)
    return runner.WorkspacePreparationPhaseResult(
        layout=layout,
        context=context,
        harbor_request=harbor_request,
        prep_phase_timings_sec=prep_phase_timings,
        prep_total_sec=prep_total_sec,
        cache_metadata=cache_metadata,
        auth_metadata=auth_metadata,
        screenshot_command=tuple(screenshot_command) if screenshot_command else None,
        evidence_errors=tuple(evidence_errors),
    )


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

    verifier_outputs = None
    if not terminated_early:
        verifier_outputs, verifier_reason = runner._load_verifier_outputs(harbor_result.trial_dir)
        if verifier_outputs is None:
            terminated_early = True
            termination_reason = verifier_reason

    outputs = (
        runner.terminated_outputs(termination_reason) if terminated_early else verifier_outputs
    )
    if outputs is None:
        outputs = runner.terminated_outputs("Verifier outputs unavailable.")

    duration_sec = (datetime.now(UTC) - phase.layout.start_time).total_seconds()
    return runner.ExecutionPhaseResult(
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
    evidence_artifacts: dict[str, object] = {
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
    if phase.screenshot_command and not execution.terminated_early:
        archive_path, hydrate_error = runner._hydrate_workspace_from_final_app(
            execution.harbor_result,
            phase.context.workspace,
        )
        if archive_path:
            evidence_artifacts["final_workspace_archive"] = str(archive_path)
            post_path, post_error = runner._run_homepage_capture_command(
                list(phase.screenshot_command),
                phase.context.workspace,
                phase.layout.root_dir / "homepage-post.png",
            )
            if post_path:
                evidence_artifacts["homepage_post"] = str(post_path)
            if post_error:
                evidence_artifacts["errors"].append(f"homepage-post capture failed: {post_error}")
            visual_artifacts = runner._persist_visual_evidence_artifacts(
                request=request,
                workspace=phase.context.workspace,
                run_root_dir=phase.layout.root_dir,
            )
            evidence_artifacts["visual"] = visual_artifacts
            runner._rebind_visual_evidence_paths(execution.outputs.visual, visual_artifacts)
        if hydrate_error:
            evidence_artifacts["errors"].append(hydrate_error)

    workspace_prune = runner._prune_workspace_artifacts(phase.layout.workspace_dir)
    workspace_changes = runner._workspace_changes_from_baseline(
        baseline_workspace=phase.context.baseline_workspace,
        run_workspace=phase.layout.workspace_dir,
        run_root_dir=phase.layout.root_dir,
    )
    return runner.PersistedArtifacts(
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


def synthesize_scorecard_phase(request, phase, execution, artifacts):
    """Score synthesis phase from persisted artifacts and execution outputs."""

    runner = _runner()
    scorecard = runner.build_scorecard(
        runner.ScorecardBuildContext(
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
