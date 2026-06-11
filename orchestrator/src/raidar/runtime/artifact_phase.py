"""Artifact persistence phase for one evaluation run."""

from __future__ import annotations

import json
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

RESERVED_EVIDENCE_KEYS = frozenset(
    {
        "screenshot_command",
        "homepage_post",
        "final_workspace_archive",
        "visual",
        "errors",
        "retained_files",
    }
)
MAX_EVIDENCE_FILE_BYTES = 65536
MAX_EVIDENCE_TEXT_CHARS = 4000
MAX_EVIDENCE_LIST_ITEMS = 50
MAX_EVIDENCE_LIST_ITEM_CHARS = 500


def persist_artifacts_phase(
    request: Any,
    phase: Any,
    execution: Any,
) -> PersistedArtifacts:
    """Artifact persistence phase."""

    evidence_artifacts = _initial_evidence_artifacts(phase)
    hydrated = _hydrate_final_workspace(phase, execution, evidence_artifacts)
    if hydrated and phase.screenshot_command:
        _persist_visual_evidence(request, phase, execution, evidence_artifacts)
    _ingest_retained_evidence(request, phase, evidence_artifacts)

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


def _hydrate_final_workspace(
    phase: Any, execution: Any, evidence_artifacts: dict[str, object]
) -> bool:
    """Hydrate the local run workspace from the final Harbor app archive."""

    if execution.terminated_early:
        return False
    archive_path, hydrate_error = _hydrate_workspace_from_final_app(
        execution.harbor_result,
        phase.context.workspace,
    )
    if hydrate_error:
        _evidence_errors(evidence_artifacts).append(hydrate_error)
    if archive_path is None:
        return False
    evidence_artifacts["final_workspace_archive"] = str(archive_path)
    return True


def _persist_visual_evidence(
    request: Any, phase: Any, execution: Any, evidence_artifacts: dict[str, object]
) -> None:
    _capture_homepage_post(phase, evidence_artifacts)
    visual_artifacts = _persist_visual_evidence_artifacts(
        VisualEvidenceRequest(
            request=request,
            workspace=phase.context.workspace,
            run_root_dir=phase.layout.root_dir,
        )
    )
    evidence_artifacts["visual"] = visual_artifacts
    _rebind_visual_evidence_paths(execution.outputs.visual, visual_artifacts)


def _capture_homepage_post(phase: Any, evidence_artifacts: dict[str, object]) -> None:
    post_path, post_error = _run_homepage_capture_command(
        list(phase.screenshot_command),
        phase.context.workspace,
        phase.layout.root_dir / "homepage-post.png",
    )
    if post_path:
        evidence_artifacts["homepage_post"] = str(post_path)
    if post_error:
        _evidence_errors(evidence_artifacts).append(f"homepage-post capture failed: {post_error}")


def _ingest_retained_evidence(
    request: Any, phase: Any, evidence_artifacts: dict[str, object]
) -> None:
    """Ingest scenario-declared retained evidence files into scorer-visible evidence."""

    declared = request.scenario.evidence.retained_files
    if not declared:
        return
    evidence_artifacts["retained_files"] = [
        _ingest_evidence_file(phase.context.workspace, entry.path, evidence_artifacts)
        for entry in declared
    ]


def _ingest_evidence_file(
    workspace: Any, relative_path: str, evidence_artifacts: dict[str, object]
) -> dict[str, object]:
    payload, status = _load_evidence_payload(workspace, relative_path)
    if payload is None:
        return {"path": relative_path, "status": status, "keys": []}
    ingested: list[str] = []
    for key, value in payload.items():
        if key in RESERVED_EVIDENCE_KEYS or key in evidence_artifacts:
            continue
        sanitized = _sanitize_evidence_value(value)
        if sanitized is None:
            continue
        evidence_artifacts[key] = sanitized
        ingested.append(key)
    return {"path": relative_path, "status": "ingested", "keys": ingested}


def _load_evidence_payload(workspace: Any, relative_path: str) -> tuple[dict[str, Any] | None, str]:
    path = workspace / relative_path
    if not path.is_file():
        return None, "missing"
    if path.stat().st_size > MAX_EVIDENCE_FILE_BYTES:
        return None, f"oversize: exceeds {MAX_EVIDENCE_FILE_BYTES} bytes"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"unreadable: {exc}"
    if not isinstance(payload, dict):
        return None, "invalid: top-level JSON value must be an object"
    return payload, "ingested"


def _sanitize_evidence_value(value: Any) -> str | list[str] | None:
    if isinstance(value, str):
        text = value.strip()
        return text[:MAX_EVIDENCE_TEXT_CHARS] if text else None
    if isinstance(value, list):
        items = [
            item.strip()[:MAX_EVIDENCE_LIST_ITEM_CHARS]
            for item in value[:MAX_EVIDENCE_LIST_ITEMS]
            if isinstance(item, str) and item.strip()
        ]
        return items if items else None
    return None


def _evidence_errors(evidence_artifacts: dict[str, object]) -> list[str]:
    errors = evidence_artifacts["errors"]
    if not isinstance(errors, list):
        raise TypeError("evidence_artifacts errors must be a list")
    return errors
