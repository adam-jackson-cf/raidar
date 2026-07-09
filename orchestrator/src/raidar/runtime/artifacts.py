"""Runtime artifact loading and persistence services."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from raidar.harness import harness_definition
from raidar.runtime.harbor_results import (
    _verifier_scorecard_path,
)
from raidar.runtime.models import (
    EvaluationOutputs,
    GateEvent,
    HarborExecutionResult,
    RunLayout,
    RunRequest,
    WorkspaceContext,
)
from raidar.runtime.verifier_runners import verifier_output_manifest
from raidar.runtime.workspace import (
    scenario_evaluation_profile,
    scenario_metrics,
    scenario_scorers,
)
from raidar.runtime.workspace_artifacts import (
    _visual_artifact_manifest,
    _visual_reference_assets,
    _visual_region_names,
)
from raidar.runtime.workspace_cache import (
    _hash_bytes,
)
from raidar.sanitization import (
    sanitize_evidence_payload,
    sanitize_persisted_text,
)
from raidar.schemas.scorecard import (
    CoverageScore,
    ExecutionValidityScore,
    FunctionalScore,
    MetricScore,
    PerformanceGatesScore,
    RequirementsCoverageScore,
    Scorecard,
    VerificationStabilityScore,
    VisualScore,
)

SCORING_SCHEMA_VERSION = "2.0.0"


@dataclass(frozen=True, slots=True)
class VisualEvidenceRequest:
    """Input for persisting visual evidence assets."""

    request: RunRequest
    workspace: Path
    run_root_dir: Path


def _parse_gate_history(payload: dict[str, Any]) -> list[GateEvent]:
    return _parse_scorecard_list(payload, "gate_history", GateEvent)


def _parse_metric_scores(payload: dict[str, Any]) -> list[MetricScore]:
    return _parse_scorecard_list(payload, "metric_scores", MetricScore)


def _parse_scorecard_list(
    payload: dict[str, Any],
    field_name: str,
    model_type,
):
    items = payload.get(field_name)
    if not isinstance(items, list):
        raise ValueError(f"scorecard.{field_name} must be a list")
    return [model_type.model_validate(item) for item in items]


def _parse_verifier_scorecard(payload: dict[str, Any]) -> EvaluationOutputs:
    gate_history = _parse_gate_history(payload)
    metric_scores = _parse_metric_scores(payload)
    return EvaluationOutputs(
        functional=FunctionalScore.model_validate(payload.get("functional")),
        visual=(
            VisualScore.model_validate(payload.get("visual"))
            if payload.get("visual") is not None
            else None
        ),
        verification_stability=VerificationStabilityScore.model_validate(
            payload.get("verification_stability")
        ),
        test_coverage=CoverageScore.model_validate(payload.get("test_coverage")),
        requirements_coverage=RequirementsCoverageScore.model_validate(
            payload.get("requirements_coverage")
        ),
        execution_validity=ExecutionValidityScore.model_validate(payload.get("execution_validity")),
        performance_gates=PerformanceGatesScore.model_validate(payload.get("performance_gates")),
        metric_scores=metric_scores,
        gate_history=gate_history,
    )


def _load_verifier_outputs(trial_dir: Path | None) -> tuple[EvaluationOutputs | None, str | None]:
    scorecard_path = _verifier_scorecard_path(trial_dir)
    if not scorecard_path:
        return None, "Harbor trial directory not found."
    if not scorecard_path.exists():
        return None, f"Verifier scorecard missing: {scorecard_path}"

    try:
        payload = json.loads(scorecard_path.read_text())
    except json.JSONDecodeError as exc:
        return None, f"Invalid verifier scorecard JSON: {exc.msg}"
    if not isinstance(payload, dict):
        return None, "Invalid verifier scorecard content: expected object root."

    try:
        outputs = _parse_verifier_scorecard(payload)
    except (ValidationError, ValueError) as exc:
        return None, f"Invalid verifier scorecard content: {exc}"

    return outputs, None


def build_starter_meta(request: RunRequest, context: WorkspaceContext) -> dict:
    """Build starter metadata for the scorecard."""
    del request
    return {
        "scenario": context.starter_source.scenario_name,
        "scenario_revision": context.starter_source.scenario_revision,
        "root": str(context.starter_source.path),
        "baseline_workspace_dir": str(context.baseline_workspace),
        "baseline_cache_key": context.baseline_cache_key,
        "baseline_cache_status": context.baseline_cache_status,
        "baseline_metadata_path": str(context.baseline_metadata_path),
        "baseline_fingerprint": context.baseline_fingerprint,
        "run_workspace_dir": str(context.workspace),
        "fingerprint": context.starter_source.fingerprint,
        "metadata_file": context.metadata_path.name,
        "rules_file": context.injected_rules.name if context.injected_rules else None,
        "artifacts": {
            "metadata": str(context.metadata_path),
            "baseline_metadata": str(context.baseline_metadata_path),
            **({"rules": str(context.injected_rules)} if context.injected_rules else {}),
        },
    }


def build_scenario_revision_meta(request: RunRequest, context: WorkspaceContext) -> dict[str, Any]:
    """Build deterministic scenario/starter fingerprint metadata."""
    scenario_path = request.scenario_dir / "scenario.yaml"
    scenario_yaml_hash = _hash_bytes(scenario_path.read_bytes()) if scenario_path.exists() else None

    seed_payload = {
        "scoring_schema_version": SCORING_SCHEMA_VERSION,
        "scenario_yaml_hash": scenario_yaml_hash,
        "scenario_model": request.scenario.model_dump(mode="json", exclude_none=True),
        "scenario_name": request.scenario.name,
        "scenario_revision": request.scenario.scenario_revision,
        "starter_root": request.scenario.starter.root,
        "starter_fingerprint": context.starter_source.fingerprint,
    }
    seed = json.dumps(seed_payload, sort_keys=True, separators=(",", ":")).encode()
    return {
        "scoring_schema_version": SCORING_SCHEMA_VERSION,
        "scenario_yaml_hash": scenario_yaml_hash,
        "scenario_fingerprint": _hash_bytes(seed),
        "evaluation_profile": scenario_evaluation_profile(request.scenario),
        "metrics": scenario_metrics(request.scenario),
        "scorers": scenario_scorers(request.scenario),
        "environment": request.scenario.environment.model_dump(mode="json"),
        "scorer_requirements": [
            {
                "scorer": scorer.ref,
                "requirements": scorer.requirements.model_dump(mode="json"),
            }
            for scorer in request.scenario.resolved_scorers()
        ],
    }


def persist_verifier_artifacts(
    harbor_result: HarborExecutionResult, verifier_dir: Path
) -> dict[str, str]:
    """Persist verifier outputs for run and scenario audits."""
    if not harbor_result.trial_dir:
        return {}
    source_dir = harbor_result.trial_dir / "verifier"
    if not source_dir.exists():
        return {}

    copied: dict[str, str] = {}
    for filename in verifier_output_manifest():
        source = source_dir / filename
        if not source.exists():
            continue
        target = verifier_dir / filename
        copied[filename] = str(_copy_sanitized_verifier_artifact(source, target))
    return copied


def _copy_sanitized_verifier_artifact(source: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix == ".json":
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            target.write_text(
                sanitize_persisted_text(source.read_text(encoding="utf-8"), max_chars=4000),
                encoding="utf-8",
            )
            return target
        target.write_text(
            json.dumps(sanitize_evidence_payload(payload, max_chars=4000), indent=2) + "\n",
            encoding="utf-8",
        )
        return target
    if _is_sanitizable_text_path(source):
        target.write_text(
            sanitize_persisted_text(source.read_text(encoding="utf-8"), max_chars=4000),
            encoding="utf-8",
        )
        return target
    return shutil.copy2(source, target)


def persist_canonical_verifier_artifacts(
    layout: RunLayout, scorecard: Scorecard, outputs: EvaluationOutputs
) -> None:
    """Rewrite canonical verifier artifacts from the synthesized canonical scorecard."""
    layout.verifier_dir.mkdir(parents=True, exist_ok=True)
    gate_history_payload = sanitize_evidence_payload(
        [event.model_dump(mode="json") for event in outputs.gate_history],
        max_chars=4000,
    )
    scorecard_payload = sanitize_evidence_payload(
        scorecard.model_dump(mode="json"),
        max_chars=4000,
    )
    scorecard_payload["gate_history"] = gate_history_payload

    (layout.verifier_dir / "scorecard.json").write_text(
        json.dumps(scorecard_payload, indent=2) + "\n",
        encoding="utf-8",
    )
    (layout.verifier_dir / "gate-history.json").write_text(
        json.dumps(gate_history_payload, indent=2) + "\n",
        encoding="utf-8",
    )
    (layout.verifier_dir / "execution-validity.json").write_text(
        json.dumps(
            sanitize_evidence_payload(
                scorecard.execution_validity.model_dump(mode="json"),
                max_chars=4000,
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (layout.verifier_dir / "performance-gates.json").write_text(
        json.dumps(
            sanitize_evidence_payload(
                scorecard.performance_gates.model_dump(mode="json"),
                max_chars=4000,
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    reward_value = scorecard.quality_score if scorecard.execution_validity.passed else 0
    (layout.verifier_dir / "reward.txt").write_text(f"{reward_value}", encoding="utf-8")


def _copy_optional_visual_asset(source: Path, target: Path) -> str | None:
    if not source.exists():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    return str(shutil.copy2(source, target))


def _persist_visual_evidence_artifacts(request: VisualEvidenceRequest) -> dict[str, Any]:
    """Persist visual evidence assets into the canonical run directory."""
    run_request = request.request
    if run_request.scenario.visual is None:
        return {
            "actual": None,
            "reference": None,
            "diff": None,
            "regions": [],
        }

    visual_dir = request.run_root_dir / "visual"
    main_artifacts = _persist_main_visual_artifacts(request, visual_dir)
    return {
        "actual": main_artifacts["actual"],
        "reference": main_artifacts["reference"],
        "diff": main_artifacts["diff"],
        "regions": _persist_region_visual_artifacts(request, visual_dir),
    }


def _persist_main_visual_artifacts(
    request: VisualEvidenceRequest, visual_dir: Path
) -> dict[str, str | None]:
    run_request = request.request
    visual_artifacts = _visual_artifact_manifest(run_request.scenario)
    main_reference_name = Path(run_request.scenario.visual.reference_image).name
    artifacts = {
        "actual": _copy_optional_visual_asset(
            request.workspace / visual_artifacts["actual"],
            visual_dir / visual_artifacts["actual"],
        ),
        "reference": None,
        "diff": _copy_optional_visual_asset(
            request.workspace / visual_artifacts["diff"],
            visual_dir / visual_artifacts["diff"],
        ),
    }
    for source_reference, relative_target in _visual_reference_assets(run_request):
        copied = _copy_optional_visual_asset(source_reference, visual_dir / relative_target.name)
        if relative_target.name == main_reference_name:
            artifacts["reference"] = copied
    return artifacts


def _persist_region_visual_artifacts(
    request: VisualEvidenceRequest, visual_dir: Path
) -> list[dict[str, str | None]]:
    run_request = request.request
    return [
        _persist_region_visual_artifact(request, visual_dir, region_name)
        for region_name in _visual_region_names(run_request)
    ]


def _persist_region_visual_artifact(
    request: VisualEvidenceRequest,
    visual_dir: Path,
    region_name: str,
) -> dict[str, str | None]:
    region = next(
        item for item in request.request.scenario.visual.regions if item.name == region_name
    )
    actual_name = region.actual_image
    diff_name = region.diff_image
    reference_name = region.reference_image
    return {
        "name": region_name,
        "actual": _copy_optional_visual_asset(
            request.workspace / actual_name,
            visual_dir / actual_name,
        ),
        "reference": _copy_optional_visual_asset(
            request.request.scenario_dir / reference_name,
            visual_dir / Path(reference_name).name,
        ),
        "diff": _copy_optional_visual_asset(
            request.workspace / diff_name,
            visual_dir / diff_name,
        ),
    }


def _rebind_visual_evidence_paths(
    scorecard_visual: VisualScore | None, evidence: dict[str, Any]
) -> None:
    """Replace transient /app visual paths with canonical run artifact paths."""
    if scorecard_visual is None:
        return

    scorecard_visual.actual_path = evidence.get("actual")
    scorecard_visual.reference_path = evidence.get("reference")
    scorecard_visual.diff_path = evidence.get("diff")

    regional_paths = {
        entry.get("name"): entry for entry in evidence.get("regions", []) if isinstance(entry, dict)
    }
    for region in scorecard_visual.regional_scores:
        if not isinstance(region, dict):
            continue
        region_paths = regional_paths.get(region.get("name"))
        if region_paths is None:
            continue
        region["actual_path"] = region_paths.get("actual")
        region["reference_path"] = region_paths.get("reference")
        region["diff_path"] = region_paths.get("diff")


def persist_harness_artifacts(
    harbor_result: HarborExecutionResult, harness_dir: Path, *, harness: str
) -> dict[str, str]:
    """Persist Harbor harness transcripts and command history."""
    if not harbor_result.trial_dir:
        return {}
    source = harbor_result.trial_dir / "agent"
    if not source.exists():
        return {}

    copied: dict[str, str] = {}
    for filename in harness_definition(harness).artifact_files:
        src = source / filename
        if src.exists():
            copied[filename] = str(_copy_sanitized_harness_artifact(src, harness_dir / filename))
    final_archive = harness_definition(harness).final_workspace_archive
    final_app = harness_dir / final_archive
    if final_app.exists():
        copied["project.final.tar.gz"] = str(
            shutil.copy2(final_app, harness_dir / "project.final.tar.gz")
        )

    setup_dir = source / "setup"
    if setup_dir.exists():
        target = harness_dir / "setup"
        _copy_sanitized_directory(setup_dir, target)
        copied["setup"] = str(target)

    commands_dir = harness_dir / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)
    for command_dir in sorted(source.glob("command-*")):
        if not command_dir.is_dir():
            continue
        target = commands_dir / command_dir.name
        _copy_sanitized_directory(command_dir, target)
        copied[f"commands/{command_dir.name}"] = str(target)

    return copied


def persist_harbor_artifacts(
    harbor_result: HarborExecutionResult, harbor_dir: Path
) -> dict[str, str]:
    """Record Harbor artifact pointers for run review."""
    copied: dict[str, str] = {}
    for name in ("command.txt", "harbor-stdout.log", "harbor-stderr.log"):
        candidate = harbor_dir / name
        if candidate.exists():
            _sanitize_text_file_in_place(candidate)
            copied[name] = str(candidate)
    copied["raw_job_dir"] = str(harbor_result.job_dir)
    if harbor_result.trial_dir:
        copied["raw_trial_dir"] = str(harbor_result.trial_dir)
    return copied


def _copy_sanitized_harness_artifact(source: Path, target: Path) -> Path:
    if _is_sanitizable_text_path(source):
        return _copy_sanitized_verifier_artifact(source, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    return shutil.copy2(source, target)


def _copy_sanitized_directory(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        destination = target / relative
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        _copy_sanitized_harness_artifact(item, destination)


def _sanitize_text_file_in_place(path: Path) -> None:
    if not _is_sanitizable_text_path(path):
        return
    path.write_text(
        sanitize_persisted_text(path.read_text(encoding="utf-8"), max_chars=4000),
        encoding="utf-8",
    )


def _is_sanitizable_text_path(path: Path) -> bool:
    return path.suffix.lower() in {".json", ".txt", ".log", ".sh", ".md"}


def _harness_event_stream_pointer(harness_dir: Path, harness: str) -> str:
    del harness_dir
    return harness_definition(harness).event_stream_pointer


def write_run_analysis(
    layout: RunLayout,
    request: RunRequest,
    scorecard: Scorecard,
    harbor_result: HarborExecutionResult,
) -> None:
    """Write a human-readable run summary with canonical/raw pointers."""
    evidence_meta = scorecard.metadata.get("evidence", {})
    workspace_meta = scorecard.metadata.get("workspace", {})
    prune_meta = workspace_meta.get("prune", {}) if isinstance(workspace_meta, dict) else {}
    change_meta = workspace_meta.get("changes", {}) if isinstance(workspace_meta, dict) else {}
    lines = [
        "# Run Summary",
        "",
        f"- run_id: `{layout.run_id}`",
        f"- started_at_utc: `{layout.start_time.isoformat()}`",
        f"- scenario: `{request.scenario.name}`",
        f"- harness: `{request.config.harness.value}`",
        f"- model: `{request.config.model.qualified_name}`",
        f"- run_label: `{layout.run_label}`",
        f"- execution_valid: `{scorecard.execution_validity.passed}`",
        f"- performance_gates_passed: `{scorecard.performance_gates.passed}`",
        f"- unscored: `{scorecard.unscored}`",
        f"- unscored_reasons: `{scorecard.unscored_reasons}`",
        f"- quality_score: `{scorecard.quality_score:.6f}`",
        f"- composite_score: `{scorecard.composite_score:.6f}`",
        "",
        "## Pointers",
        f"- canonical_run_dir: `{layout.root_dir}`",
        f"- workspace_dir: `{layout.workspace_dir}`",
        f"- raw_harbor_job_dir: `{harbor_result.job_dir}`",
        f"- raw_harbor_trial_dir: `{harbor_result.trial_dir}`",
        f"- run_json_path: `{layout.run_json_path}`",
        "",
        "## Key Artifacts",
        f"- verifier_scorecard: `{layout.verifier_dir / 'scorecard.json'}`",
        f"- harness_trajectory: `{layout.harness_dir / 'trajectory.json'}`",
    ]
    event_stream = _harness_event_stream_pointer(layout.harness_dir, request.config.harness.value)
    lines.append(f"- harness_event_stream: `{event_stream}`")
    lines.append(f"- post_run_visual_capture: `{evidence_meta.get('post_capture')}`")
    lines.append(f"- final_workspace_archive: `{evidence_meta.get('final_workspace_archive')}`")
    lines.append(f"- evidence_errors: `{evidence_meta.get('errors')}`")
    lines.append(f"- workspace_pruned_dirs: `{prune_meta.get('removed')}`")
    lines.append(f"- workspace_pruned_bytes: `{prune_meta.get('reclaimed_bytes')}`")
    lines.append(f"- workspace_changed_file_count: `{change_meta.get('changed_file_count')}`")
    lines.append(f"- workspace_changed_files: `{change_meta.get('changed_files')}`")
    lines.append(f"- workspace_diff_artifact: `{change_meta.get('artifact')}`")
    lines.append(f"- workspace_diff_error: `{change_meta.get('error')}`")
    layout.report_path.write_text(sanitize_persisted_text("\n".join(lines), max_chars=12000) + "\n")
