"""Task execution via Harbor."""

import json
import os
import shutil
import subprocess
import tarfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from .audit.scaffold_manifest import create_scaffold_audit, generate_manifest, save_manifest
from .config import settings
from .harness.config import HarnessConfig
from .harness.rules import inject_rules
from .scaffold import ScaffoldSource
from .schemas.events import GateEvent
from .schemas.scorecard import (
    ComplianceCheck,
    ComplianceScore,
    EfficiencyScore,
    EvalConfig,
    EvalRun,
    FunctionalScore,
    ScaffoldAudit,
    Scorecard,
    VisualScore,
)
from .schemas.task import TaskDefinition
from .scoring.compliance import evaluate_compliance
from .scoring.efficiency import evaluate_efficiency
from .scoring.functional import evaluate_functional
from .scoring.visual import evaluate_visual
from .watcher.gate_watcher import GateWatcher


def load_task(task_path: Path) -> TaskDefinition:
    """Load task definition from YAML file."""
    with open(task_path) as f:
        data = yaml.safe_load(f)
    return TaskDefinition.model_validate(data)


@dataclass(frozen=True, slots=True)
class RunRequest:
    """Input bundle for running a task."""

    task: TaskDefinition
    config: HarnessConfig
    scaffold_root: Path
    task_dir: Path
    workspace_dir: Path
    results_dir: Path


@dataclass(frozen=True, slots=True)
class ScaffoldContext:
    """Resolved scaffold context for a task run."""

    scaffold_source: ScaffoldSource
    workspace: Path
    injected_rules: Path | None
    manifest_path: Path
    baseline_manifest_path: Path
    metadata_path: Path


@dataclass(frozen=True, slots=True)
class ArtifactPaths:
    """Persisted artifact locations for a run."""

    baseline: Path
    manifest: Path
    metadata: Path
    rules: Path | None


@dataclass(frozen=True, slots=True)
class EvaluationOutputs:
    """Computed scoring outputs for a run."""

    functional: FunctionalScore
    compliance: ComplianceScore
    visual: VisualScore | None
    efficiency: EfficiencyScore
    gate_history: list[GateEvent]


@dataclass(frozen=True, slots=True)
class HarborExecutionResult:
    """Outcome of the Harbor execution phase."""

    terminated_early: bool
    termination_reason: str | None
    job_dir: Path
    trial_dir: Path | None


def prepare_workspace(
    *,
    scaffold_dir: Path,
    target_dir: Path,
    task_dir: Path,
    agent: str,
    rules_variant: str,
) -> tuple[Path, Path | None]:
    """Prepare workspace by copying scaffold and injecting rules.

    Args:
        scaffold_dir: Path to resolved scaffold template/version
        target_dir: Path to create workspace
        task_dir: Path to task directory (contains rules/)
        agent: Agent name for rule file selection
        rules_variant: Rules variant (strict, minimal, none)

    Returns:
        Tuple of workspace path and injected rules file (if any)
    """
    # Copy scaffold to target
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(
        scaffold_dir,
        target_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("node_modules", ".next", "jobs"),
    )

    # Inject rules
    injected_rules: Path | None = None
    rules_dir = task_dir / "rules"
    if rules_dir.exists():
        injected_rules = inject_rules(rules_dir, target_dir, agent, rules_variant)

    # Generate initial manifest for baseline
    manifest = generate_manifest(target_dir)
    save_manifest(manifest, target_dir / "scaffold.manifest.json")

    return target_dir, injected_rules


def create_harbor_task_bundle(request: RunRequest, context: ScaffoldContext) -> Path:
    """Build a Harbor-compatible task directory from the scaffold workspace."""
    bundle_dir = context.workspace / "harbor-task"
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)

    environment_dir = bundle_dir / "environment"
    app_dir = environment_dir / "app"
    tests_dir = bundle_dir / "tests"
    environment_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)

    shutil.copytree(
        context.workspace,
        app_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            "node_modules",
            ".next",
            "jobs",
            "harbor-task",
            "actual.png",
            "diff.png",
        ),
    )
    if request.task.visual:
        reference_path = Path(request.task.visual.reference_image)
        if not reference_path.is_absolute():
            source_reference = (request.task_dir / reference_path).resolve()
            if source_reference.exists():
                target_reference = app_dir / reference_path
                target_reference.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_reference, target_reference)

    instruction = (
        request.task.prompt.strip()
        + "\n\nYou are working in `/app`.\nFollow rules in `/app/AGENTS.md`.\n"
    )
    (bundle_dir / "instruction.md").write_text(instruction)
    (bundle_dir / "task.toml").write_text(
        f"""version = "1.0"

[metadata]
name = "{request.task.name}"
source = "scaffold-spec"

[verifier]
timeout_sec = 300.0

[agent]
timeout_sec = {float(request.config.timeout_sec)}

[environment]
build_timeout_sec = 900.0
cpus = 2
memory_mb = 4096
storage_mb = 10240
allow_internet = true
"""
    )
    (environment_dir / "Dockerfile").write_text(
        """FROM oven/bun:1
WORKDIR /app
COPY app/ /app/
RUN bun install --frozen-lockfile
"""
    )
    test_script = tests_dir / "test.sh"
    test_script.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

mkdir -p /logs/verifier /logs/agent
if [[ ! -d /app ]]; then
  echo "Missing /app workspace" >&2
  echo "0" > /logs/verifier/reward.txt
  exit 1
fi

tar \
  --exclude='./node_modules' \
  --exclude='./.next' \
  --exclude='./jobs' \
  --exclude='./actual.png' \
  --exclude='./diff.png' \
  -czf /logs/agent/final-app.tar.gz \
  -C /app .
echo "0" > /logs/verifier/reward.txt
"""
    )
    test_script.chmod(0o755)
    return bundle_dir


def initialize_run(results_dir: Path) -> tuple[str, datetime, Path]:
    """Create the run identifier and artifacts directory."""
    run_id = str(uuid.uuid4())[:8]
    start_time = datetime.now(UTC)
    results_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = results_dir / run_id
    if artifacts_dir.exists():
        shutil.rmtree(artifacts_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    return run_id, start_time, artifacts_dir


def prepare_run_context(request: RunRequest) -> ScaffoldContext:
    """Resolve scaffold source, workspace, and manifest metadata."""
    from .scaffold import record_scaffold_metadata, resolve_scaffold_source

    scaffold_source = resolve_scaffold_source(
        request.scaffold_root, request.task.scaffold.template, request.task.scaffold.version
    )

    workspace, injected_rules = prepare_workspace(
        scaffold_dir=scaffold_source.path,
        target_dir=request.workspace_dir,
        task_dir=request.task_dir,
        agent=request.config.agent.value,
        rules_variant=request.config.rules_variant,
    )
    manifest_path = workspace / "scaffold.manifest.json"
    if not manifest_path.exists():
        manifest = generate_manifest(workspace)
        save_manifest(manifest, manifest_path)

    baseline_manifest_path = workspace / ".baseline-scaffold.json"
    shutil.copy2(scaffold_source.manifest_path, baseline_manifest_path)

    metadata_path = record_scaffold_metadata(
        workspace,
        scaffold_source,
        manifest_path,
        baseline_manifest_path,
        request.config.rules_variant,
    )

    return ScaffoldContext(
        scaffold_source=scaffold_source,
        workspace=workspace,
        injected_rules=injected_rules,
        manifest_path=manifest_path,
        baseline_manifest_path=baseline_manifest_path,
        metadata_path=metadata_path,
    )


def execute_harbor(
    adapter,
    workspace: Path,
    task_path: Path,
    jobs_dir: Path,
    run_id: str,
    timeout_sec: int,
    run_env: dict[str, str],
) -> HarborExecutionResult:
    """Execute Harbor against a local task bundle."""
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job_name = f"orchestrator-{run_id}"
    job_dir = jobs_dir / job_name
    harbor_cmd = adapter.build_harbor_command(
        task_path=task_path,
        job_name=job_name,
        jobs_dir=jobs_dir,
    )

    execution_error = _run_harbor_process(
        harbor_cmd=harbor_cmd,
        workspace=workspace,
        timeout_sec=timeout_sec,
        run_env=run_env,
    )
    if execution_error:
        return _terminated_harbor_result(job_dir=job_dir, reason=execution_error, trial_dir=None)

    trial_dir = _select_trial_dir(job_dir)
    failure_reason = detect_trial_failure(trial_dir) if trial_dir else None
    if failure_reason:
        return _terminated_harbor_result(
            job_dir=job_dir,
            reason=failure_reason,
            trial_dir=trial_dir,
        )

    return HarborExecutionResult(
        terminated_early=False,
        termination_reason=None,
        job_dir=job_dir,
        trial_dir=trial_dir,
    )


def _terminated_harbor_result(
    *,
    job_dir: Path,
    reason: str,
    trial_dir: Path | None,
) -> HarborExecutionResult:
    return HarborExecutionResult(
        terminated_early=True,
        termination_reason=reason,
        job_dir=job_dir,
        trial_dir=trial_dir,
    )


def _run_harbor_process(
    *,
    harbor_cmd: list[str],
    workspace: Path,
    timeout_sec: int,
    run_env: dict[str, str],
) -> str | None:
    try:
        result = subprocess.run(
            harbor_cmd,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=run_env,
        )
    except subprocess.TimeoutExpired:
        return "Timeout expired"
    except FileNotFoundError:
        return "Harbor not installed"

    if result.returncode != 0:
        return f"Harbor exited with code {result.returncode}"
    return None


def _select_trial_dir(job_dir: Path) -> Path | None:
    if not job_dir.exists():
        return None
    trial_dirs = sorted([candidate for candidate in job_dir.iterdir() if candidate.is_dir()])
    with_agent = next(
        (candidate for candidate in trial_dirs if (candidate / "agent").exists()), None
    )
    return with_agent or (trial_dirs[0] if trial_dirs else None)


def detect_trial_failure(trial_dir: Path | None) -> str | None:
    """Extract a terminal failure reason from Harbor trial artifacts."""
    if not trial_dir:
        return None
    return _trial_exception_reason(trial_dir) or _codex_turn_failure_reason(trial_dir)


def _trial_exception_reason(trial_dir: Path) -> str | None:
    result_data = _load_json_dict(trial_dir / "result.json")
    exception_info = result_data.get("exception_info")
    if not isinstance(exception_info, dict):
        return None
    message = exception_info.get("exception_message")
    if not isinstance(message, str):
        return None
    message = message.strip()
    if not message:
        return None
    return f"Harbor trial exception: {message}"


def _codex_turn_failure_reason(trial_dir: Path) -> str | None:
    codex_log = trial_dir / "agent" / "codex.txt"
    if not codex_log.exists():
        return None
    for line in reversed(codex_log.read_text(errors="ignore").splitlines()):
        if '"type":"turn.failed"' not in line:
            continue
        message = _codex_turn_failure_message(line)
        return f"Codex turn failed: {message}" if message else "Codex turn failed."
    return None


def _codex_turn_failure_message(line: str) -> str | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        message: str | None = line
    else:
        raw_message = payload.get("error", {}).get("message")
        message = raw_message if isinstance(raw_message, str) else None
    if not message:
        return None
    message = message.strip()
    return message or None


def _load_json_dict(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_tar_archive(archive_path: Path, target_dir: Path) -> None:
    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(target_dir)


def hydrate_workspace_from_harbor(
    request: RunRequest,
    context: ScaffoldContext,
    harbor_result: HarborExecutionResult,
) -> tuple[bool, str | None]:
    """Replace workspace with Harbor-produced app artifact."""
    if not harbor_result.trial_dir:
        return False, "Harbor trial directory not found."

    archive_path = harbor_result.trial_dir / "agent" / "final-app.tar.gz"
    if not archive_path.exists():
        return False, f"Harbor artifact missing: {archive_path}"
    if archive_path.is_relative_to(context.workspace):
        return False, "Harbor artifact path must be outside workspace before hydration."

    if context.workspace.exists():
        shutil.rmtree(context.workspace)
    context.workspace.mkdir(parents=True, exist_ok=True)
    _extract_tar_archive(archive_path, context.workspace)

    manifest = generate_manifest(context.workspace)
    save_manifest(manifest, context.manifest_path)
    shutil.copy2(context.scaffold_source.manifest_path, context.baseline_manifest_path)

    from .scaffold import record_scaffold_metadata

    record_scaffold_metadata(
        context.workspace,
        context.scaffold_source,
        context.manifest_path,
        context.baseline_manifest_path,
        request.config.rules_variant,
    )
    return True, None


def persist_scaffold_artifacts(context: ScaffoldContext, artifacts_dir: Path) -> ArtifactPaths:
    """Persist scaffold artifacts for later audits."""
    artifact_manifest = Path(
        shutil.copy2(context.manifest_path, artifacts_dir / "workspace.manifest.json")
    )
    artifact_baseline = Path(
        shutil.copy2(
            context.scaffold_source.manifest_path, artifacts_dir / "baseline.manifest.json"
        )
    )
    artifact_meta = Path(shutil.copy2(context.metadata_path, artifacts_dir / "scaffold-meta.json"))
    artifact_rules = None
    if context.injected_rules and context.injected_rules.exists():
        artifact_rules = Path(
            shutil.copy2(
                context.injected_rules,
                artifacts_dir / context.injected_rules.name.replace(" ", "_"),
            )
        )

    return ArtifactPaths(
        baseline=artifact_baseline,
        manifest=artifact_manifest,
        metadata=artifact_meta,
        rules=artifact_rules,
    )


def build_scaffold_meta(context: ScaffoldContext, artifacts: ArtifactPaths) -> dict:
    """Build scaffold metadata for the scorecard."""
    return {
        "template": context.scaffold_source.template,
        "version": context.scaffold_source.version,
        "fingerprint": context.scaffold_source.manifest.fingerprint,
        "baseline_manifest": context.baseline_manifest_path.name,
        "workspace_manifest": context.manifest_path.name,
        "metadata_file": context.metadata_path.name,
        "rules_file": context.injected_rules.name if context.injected_rules else None,
        "artifacts": {
            "baseline_manifest": str(artifacts.baseline),
            "workspace_manifest": str(artifacts.manifest),
            "metadata": str(artifacts.metadata),
            **({"rules": str(artifacts.rules)} if artifacts.rules else {}),
        },
    }


def evaluate_workspace(request: RunRequest, context: ScaffoldContext) -> EvaluationOutputs:
    """Run deterministic post-harbor evaluation against the workspace."""
    subprocess.run(
        ["bun", "install", "--frozen-lockfile"],
        cwd=context.workspace,
        capture_output=True,
        text=True,
        timeout=settings.timeouts.build,
        check=False,
    )

    watcher = GateWatcher(max_failures=request.task.verification.max_gate_failures)
    gate_history = watcher.run_all_gates(request.task.verification.gates, context.workspace)

    functional = evaluate_functional(context.workspace)
    functional.gates_total = len(gate_history)
    functional.gates_passed = sum(1 for event in gate_history if event.exit_code == 0)

    compliance = evaluate_compliance(
        context.workspace,
        request.task.compliance,
        rules_path=context.injected_rules,
        run_llm_checks=False,
    )

    visual: VisualScore | None = None
    if request.task.visual:
        reference_image = Path(request.task.visual.reference_image)
        if not reference_image.is_absolute():
            reference_image = request.task_dir / reference_image
        visual = evaluate_visual(
            workspace=context.workspace,
            reference_image=reference_image,
            screenshot_command=request.task.visual.screenshot_command,
            threshold=request.task.visual.threshold,
        )

    efficiency = evaluate_efficiency(gate_history)
    return EvaluationOutputs(
        functional=functional,
        compliance=compliance,
        visual=visual,
        efficiency=efficiency,
        gate_history=gate_history,
    )


def terminated_outputs(reason: str | None) -> EvaluationOutputs:
    """Create deterministic zeroed scores for terminated runs."""
    failure_reason = reason or "Run terminated before scoring."
    return EvaluationOutputs(
        functional=FunctionalScore(
            passed=False,
            tests_passed=0,
            tests_total=0,
            build_succeeded=False,
            gates_passed=0,
            gates_total=0,
        ),
        compliance=ComplianceScore(
            checks=[
                ComplianceCheck(
                    rule="Evaluation run completed",
                    type="deterministic",
                    passed=False,
                    evidence=failure_reason,
                )
            ]
        ),
        visual=None,
        efficiency=EfficiencyScore(
            total_gate_failures=settings.efficiency.max_gate_failures,
            unique_failure_categories=0,
            repeat_failures=0,
        ),
        gate_history=[],
    )


def build_scorecard(
    request: RunRequest,
    context: ScaffoldContext,
    scaffold_meta: dict,
    run_id: str,
    duration_sec: float,
    terminated_early: bool,
    termination_reason: str | None,
    outputs: EvaluationOutputs,
    harbor_result: HarborExecutionResult,
) -> Scorecard:
    """Create scorecard with populated metrics and metadata."""

    scaffold_audit = create_scaffold_audit(context.scaffold_source.manifest, context.workspace)
    scaffold_audit.template = context.scaffold_source.template
    scaffold_audit.template_version = context.scaffold_source.version
    scaffold_audit.manifest_fingerprint = context.scaffold_source.manifest.fingerprint
    compliance = enforce_scaffold_change_check(
        compliance=outputs.compliance,
        scaffold_audit=scaffold_audit,
        terminated_early=terminated_early,
    )

    return Scorecard(
        run_id=run_id,
        task_name=request.task.name,
        agent=request.config.agent.value,
        model=request.config.model.qualified_name,
        rules_variant=request.config.rules_variant,
        duration_sec=duration_sec,
        terminated_early=terminated_early,
        termination_reason=termination_reason,
        functional=outputs.functional,
        compliance=compliance,
        visual=outputs.visual,
        efficiency=outputs.efficiency,
        metadata={
            "scaffold": scaffold_meta,
            "harbor": {
                "job_dir": str(harbor_result.job_dir),
                "trial_dir": str(harbor_result.trial_dir) if harbor_result.trial_dir else None,
            },
        },
        scaffold_audit=scaffold_audit,
    )


def enforce_scaffold_change_check(
    *,
    compliance: ComplianceScore,
    scaffold_audit: ScaffoldAudit,
    terminated_early: bool,
) -> ComplianceScore:
    """Ensure no-op scaffold outputs are penalized in compliance."""
    if terminated_early or scaffold_audit.changes_from_baseline:
        return compliance
    checks = [
        *compliance.checks,
        ComplianceCheck(
            rule="Modifies scaffold files",
            type="deterministic",
            passed=False,
            evidence="No tracked file changes from scaffold baseline.",
        ),
    ]
    return ComplianceScore(checks=checks)


def run_task(request: RunRequest) -> EvalRun:
    """Execute a task and return evaluation results."""
    run_id, start_time, artifacts_dir = initialize_run(request.results_dir)
    adapter = request.config.adapter()
    adapter.validate()

    context = prepare_run_context(request)
    adapter.prepare_workspace(context.workspace)
    harbor_task_bundle = create_harbor_task_bundle(request, context)

    run_env = os.environ.copy()
    run_env.update(adapter.runtime_env())

    harbor_result = execute_harbor(
        adapter,
        context.workspace,
        harbor_task_bundle,
        request.results_dir / "jobs",
        run_id,
        request.config.timeout_sec,
        run_env,
    )
    terminated_early = harbor_result.terminated_early
    termination_reason = harbor_result.termination_reason

    if not terminated_early:
        hydrated, hydration_reason = hydrate_workspace_from_harbor(
            request,
            context,
            harbor_result,
        )
        if not hydrated:
            terminated_early = True
            termination_reason = hydration_reason

    outputs = (
        terminated_outputs(termination_reason)
        if terminated_early
        else evaluate_workspace(request, context)
    )

    end_time = datetime.now(UTC)
    duration = (end_time - start_time).total_seconds()

    artifacts = persist_scaffold_artifacts(context, artifacts_dir)
    scaffold_meta = build_scaffold_meta(context, artifacts)
    scorecard = build_scorecard(
        request=request,
        context=context,
        scaffold_meta=scaffold_meta,
        run_id=run_id,
        duration_sec=duration,
        terminated_early=terminated_early,
        termination_reason=termination_reason,
        outputs=outputs,
        harbor_result=harbor_result,
    )

    return EvalRun(
        id=run_id,
        timestamp=start_time.isoformat(),
        config=EvalConfig(
            model=request.config.model.qualified_name,
            harness=request.config.agent.value,
            rules_variant=request.config.rules_variant,
            task_name=request.task.name,
            scaffold_template=context.scaffold_source.template,
            scaffold_version=context.scaffold_source.version,
        ),
        duration_sec=duration,
        terminated_early=terminated_early,
        termination_reason=termination_reason,
        scores=scorecard,
        events=[],
        gate_history=outputs.gate_history,
    )
