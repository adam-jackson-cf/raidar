"""Artifact persistence phase for one evaluation run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from raidar.runtime import artifacts as runtime_artifacts
from raidar.runtime import models, workspace_artifacts

VisualEvidenceRequest = runtime_artifacts.VisualEvidenceRequest
_persist_visual_evidence_artifacts = runtime_artifacts._persist_visual_evidence_artifacts
_rebind_visual_evidence_paths = runtime_artifacts._rebind_visual_evidence_paths
build_scenario_revision_meta = runtime_artifacts.build_scenario_revision_meta
build_starter_meta = runtime_artifacts.build_starter_meta
persist_harbor_artifacts = runtime_artifacts.persist_harbor_artifacts
persist_harness_artifacts = runtime_artifacts.persist_harness_artifacts
persist_verifier_artifacts = runtime_artifacts.persist_verifier_artifacts
_hydrate_workspace_from_final_app = workspace_artifacts._hydrate_workspace_from_final_app
_prune_workspace_artifacts = workspace_artifacts._prune_workspace_artifacts
_run_homepage_capture_command = workspace_artifacts._run_homepage_capture_command
_workspace_changes_from_baseline = workspace_artifacts._workspace_changes_from_baseline
PersistedArtifacts = models.PersistedArtifacts


@dataclass(frozen=True, slots=True)
class _VisualEvidenceState:
    evidence_artifacts: dict[str, object]
    hydrate_error: str | None


@dataclass(frozen=True, slots=True)
class _HydratedVisualEvidenceRequest:
    request: Any
    phase: Any
    execution: Any
    evidence_artifacts: dict[str, object]


def persist_artifacts_phase(
    request: Any,
    phase: Any,
    execution: Any,
) -> PersistedArtifacts:
    """Artifact persistence phase."""

    visual_state = _persist_visual_evidence(request, phase, execution)
    evidence_artifacts = visual_state.evidence_artifacts
    if visual_state.hydrate_error:
        evidence_artifacts["errors"].append(visual_state.hydrate_error)

    workspace_prune = _prune_workspace_artifacts(phase.layout.workspace_dir)
    workspace_changes = _workspace_changes_from_baseline(
        baseline_workspace=phase.context.baseline_workspace,
        run_workspace=phase.layout.workspace_dir,
        run_root_dir=phase.layout.root_dir,
    )
    return PersistedArtifacts(
        starter_meta=build_starter_meta(request, phase.context),
        scenario_revision_meta=build_scenario_revision_meta(request, phase.context),
        verifier_artifacts=persist_verifier_artifacts(
            execution.harbor_result, phase.layout.verifier_dir
        ),
        harness_artifacts=persist_harness_artifacts(
            execution.harbor_result, phase.layout.harness_dir
        ),
        harbor_artifacts=persist_harbor_artifacts(execution.harbor_result, phase.layout.harbor_dir),
        evidence_artifacts=evidence_artifacts,
        workspace_prune=workspace_prune,
        workspace_changes=workspace_changes,
    )


def _initial_evidence_artifacts(phase: Any) -> dict[str, object]:
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


def _persist_visual_evidence(request: Any, phase: Any, execution: Any) -> _VisualEvidenceState:
    evidence_artifacts = _initial_evidence_artifacts(phase)
    if phase.screenshot_command and not execution.terminated_early:
        return _persist_hydrated_visual_evidence(
            _HydratedVisualEvidenceRequest(
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
    archive_path, hydrate_error = _hydrate_workspace_from_final_app(
        request.execution.harbor_result,
        request.phase.context.workspace,
    )
    if archive_path:
        request.evidence_artifacts["final_workspace_archive"] = str(archive_path)
        _capture_homepage_post(request.phase, request.evidence_artifacts)
        visual_artifacts = _persist_visual_evidence_artifacts(
            VisualEvidenceRequest(
                request=request.request,
                workspace=request.phase.context.workspace,
                run_root_dir=request.phase.layout.root_dir,
            )
        )
        request.evidence_artifacts["visual"] = visual_artifacts
        _rebind_visual_evidence_paths(request.execution.outputs.visual, visual_artifacts)
    return _VisualEvidenceState(
        evidence_artifacts=request.evidence_artifacts, hydrate_error=hydrate_error
    )


def _capture_homepage_post(phase: Any, evidence_artifacts: dict[str, object]) -> None:
    post_path, post_error = _run_homepage_capture_command(
        list(phase.screenshot_command),
        phase.context.workspace,
        phase.layout.root_dir / "homepage-post.png",
    )
    if post_path:
        evidence_artifacts["homepage_post"] = str(post_path)
    if post_error:
        errors = evidence_artifacts["errors"]
        if isinstance(errors, list):
            errors.append(f"homepage-post capture failed: {post_error}")
