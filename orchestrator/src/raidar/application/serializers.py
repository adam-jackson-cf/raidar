"""Stable serializers for CLI-facing JSON payloads."""

from __future__ import annotations

from raidar.application.models import ScenarioInitResult, SuiteExecutionResult
from raidar.sanitization import sanitize_evidence_payload
from raidar.scenario_clone import ScenarioCloneResult
from raidar.schemas.scorecard import EvalRun


def run_payload(run: EvalRun) -> dict[str, object]:
    """Serialize one run into the stable machine-readable CLI payload."""

    run_meta = run.scores.metadata.get("run", {})
    canonical_run_dir = run_meta.get("canonical_run_dir")
    run_json_path = run_meta.get("run_json_path")
    return {
        "run_id": run.id,
        "duration_sec": run.duration_sec,
        "terminated_early": run.terminated_early,
        "termination_reason": sanitize_evidence_payload(run.termination_reason),
        "unscored": bool(run.scores.unscored),
        "unscored_reasons": sanitize_evidence_payload(list(run.scores.unscored_reasons)),
        "execution_valid": run.scores.execution_validity.passed,
        "performance_gates_passed": run.scores.performance_gates.passed,
        "composite_score": run.scores.composite_score,
        "diagnostic_score": run.scores.diagnostic_score,
        "quality_score": run.scores.quality_score,
        "canonical_run_dir": canonical_run_dir if isinstance(canonical_run_dir, str) else None,
        "run_json_path": run_json_path if isinstance(run_json_path, str) else None,
    }


def suite_execution_payload(result: SuiteExecutionResult) -> dict[str, object]:
    """Serialize one run/experiment result into the stable CLI payload."""

    return {
        "scenario_path": str(result.scenario_path),
        "scenario_name": result.scenario_name,
        "scenario_revision": result.scenario_revision,
        "retries_used": result.retries_used,
        "experiment_json_path": (
            str(result.experiment_json_path) if result.experiment_json_path is not None else None
        ),
        "summary_path": str(result.summary_path) if result.summary_path is not None else None,
        "report_path": str(result.report_path) if result.report_path is not None else None,
        "runs": [run_payload(run) for run in result.runs],
    }


def scenario_init_payload(result: ScenarioInitResult) -> dict[str, str | None]:
    """Serialize the stable scenario-init JSON payload."""

    return {
        "scenario_root": str(result.scenario_root),
        "scenario_name": result.scenario_name,
        "scenario_revision": result.scenario_revision,
        "parent_revision": result.parent_revision,
        "revision_dir": str(result.revision_dir),
        "scenario_yaml": str(result.scenario_yaml),
        "prompt_path": str(result.prompt_path),
        "rules_dir": str(result.rules_dir),
        "starter_root": result.starter_root,
    }


def scenario_clone_payload(result: ScenarioCloneResult) -> dict[str, str]:
    """Serialize the stable scenario clone JSON payload."""

    return {
        "scenario_root": str(result.scenario_root),
        "source_revision": result.source_revision,
        "target_revision": result.target_revision,
        "parent_revision": result.parent_revision,
        "revision_dir": str(result.target_scenario_yaml.parent),
        "scenario_yaml": str(result.target_scenario_yaml),
    }
