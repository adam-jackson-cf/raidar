"""Task execution via Harbor."""

import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from .audit.scaffold_manifest import create_scaffold_audit, generate_manifest, save_manifest
from .harness.config import HarnessConfig
from .harness.rules import inject_rules
from .scaffold import ScaffoldSource
from .schemas.scorecard import EvalConfig, EvalRun, Scorecard
from .schemas.task import TaskDefinition


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
    shutil.copytree(scaffold_dir, target_dir, dirs_exist_ok=True)

    # Inject rules
    injected_rules: Path | None = None
    rules_dir = task_dir / "rules"
    if rules_dir.exists():
        injected_rules = inject_rules(rules_dir, target_dir, agent, rules_variant)

    # Generate initial manifest for baseline
    manifest = generate_manifest(target_dir)
    save_manifest(manifest, target_dir / "scaffold.manifest.json")

    return target_dir, injected_rules


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


def execute_harbor(adapter, workspace: Path, timeout_sec: int, run_env: dict[str, str]):
    """Execute a Harbor run and return termination flags."""
    harbor_cmd = adapter.build_harbor_command()

    # Execute via Harbor (placeholder - actual execution depends on Harbor being installed)
    try:
        result = subprocess.run(
            harbor_cmd,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=run_env,
        )
        if result.returncode != 0:
            return True, f"Harbor exited with code {result.returncode}"
    except subprocess.TimeoutExpired:
        return True, "Timeout expired"
    except FileNotFoundError:
        return True, "Harbor not installed"

    return False, None


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


def build_scorecard(context: ScaffoldContext, scaffold_meta: dict) -> Scorecard:
    """Create a placeholder scorecard for the run."""
    from .schemas.scorecard import (
        ComplianceScore,
        EfficiencyScore,
        FunctionalScore,
    )

    scaffold_audit = create_scaffold_audit(context.scaffold_source.manifest, context.workspace)
    scaffold_audit.template = context.scaffold_source.template
    scaffold_audit.template_version = context.scaffold_source.version
    scaffold_audit.manifest_fingerprint = context.scaffold_source.manifest.fingerprint

    return Scorecard(
        functional=FunctionalScore(
            passed=False,
            tests_passed=0,
            tests_total=0,
            build_succeeded=False,
        ),
        compliance=ComplianceScore(checks=[]),
        visual=None,
        efficiency=EfficiencyScore(
            total_gate_failures=0,
            unique_failure_categories=0,
            repeat_failures=0,
        ),
        metadata={"scaffold": scaffold_meta},
        scaffold_audit=scaffold_audit,
    )


def run_task(request: RunRequest) -> EvalRun:
    """Execute a task and return evaluation results."""
    run_id, start_time, artifacts_dir = initialize_run(request.results_dir)
    adapter = request.config.adapter()
    adapter.validate()

    context = prepare_run_context(request)
    adapter.prepare_workspace(context.workspace)

    run_env = os.environ.copy()
    run_env.update(adapter.runtime_env())

    terminated_early, termination_reason = execute_harbor(
        adapter,
        context.workspace,
        request.config.timeout_sec,
        run_env,
    )

    end_time = datetime.now(UTC)
    duration = (end_time - start_time).total_seconds()

    artifacts = persist_scaffold_artifacts(context, artifacts_dir)
    scaffold_meta = build_scaffold_meta(context, artifacts)
    scorecard = build_scorecard(context, scaffold_meta)

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
        gate_history=[],
    )
