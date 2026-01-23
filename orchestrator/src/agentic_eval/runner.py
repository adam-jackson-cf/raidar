"""Task execution via Harbor."""

import os
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import yaml

from .audit.scaffold_manifest import generate_manifest, save_manifest
from .harness.config import HarnessConfig
from .harness.rules import inject_rules
from .schemas.scorecard import EvalConfig, EvalRun, Scorecard
from .schemas.task import TaskDefinition


def load_task(task_path: Path) -> TaskDefinition:
    """Load task definition from YAML file."""
    with open(task_path) as f:
        data = yaml.safe_load(f)
    return TaskDefinition.model_validate(data)


def prepare_workspace(
    scaffold_root: Path,
    target_dir: Path,
    task_dir: Path,
    agent: str,
    rules_variant: str,
) -> Path:
    """Prepare workspace by copying scaffold and injecting rules.

    Args:
        scaffold_root: Path to scaffold catalog root
        target_dir: Path to create workspace
        task_dir: Path to task directory (contains rules/)
        agent: Agent name for rule file selection
        rules_variant: Rules variant (strict, minimal, none)

    Returns:
        Path to prepared workspace
    """
    # Copy scaffold to target
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(scaffold_dir, target_dir, dirs_exist_ok=True)

    # Inject rules
    rules_dir = task_dir / "rules"
    if rules_dir.exists():
        inject_rules(rules_dir, target_dir, agent, rules_variant)

    # Generate initial manifest for baseline
    manifest = generate_manifest(target_dir)
    save_manifest(manifest, target_dir / "scaffold.manifest.json")

    return target_dir


def run_task(
    task: TaskDefinition,
    config: HarnessConfig,
    scaffold_root: Path,
    task_dir: Path,
    workspace_dir: Path,
    results_dir: Path,
) -> EvalRun:
    """Execute a task and return evaluation results.

    Args:
        task: Task definition
        config: Harness configuration
        scaffold_root: Path to scaffold catalog root
        task_dir: Path to task directory
        workspace_dir: Path to create workspace
        results_dir: Path to store results

    Returns:
        EvalRun with execution results
    """
    run_id = str(uuid.uuid4())[:8]
    start_time = datetime.now(UTC)
    adapter = config.adapter()
    adapter.validate()

    from .scaffold import record_scaffold_metadata, resolve_scaffold_source

    scaffold_source = resolve_scaffold_source(
        scaffold_root, task.scaffold.template, task.scaffold.version
    )

    workspace = prepare_workspace(
        scaffold_dir=scaffold_source.path,
        target_dir=workspace_dir,
        task_dir=task_dir,
        agent=config.agent.value,
        rules_variant=config.rules_variant,
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
        config.rules_variant,
    )
    adapter.prepare_workspace(workspace)

    # Build Harbor command
    harbor_cmd = adapter.build_harbor_command()
    run_env = os.environ.copy()
    run_env.update(adapter.runtime_env())

    terminated_early = False
    termination_reason = None

    # Execute via Harbor (placeholder - actual execution depends on Harbor being installed)
    try:
        result = subprocess.run(
            harbor_cmd,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=config.timeout_sec,
            env=run_env,
        )
        if result.returncode != 0:
            terminated_early = True
            termination_reason = f"Harbor exited with code {result.returncode}"
    except subprocess.TimeoutExpired:
        terminated_early = True
        termination_reason = "Timeout expired"
    except FileNotFoundError:
        terminated_early = True
        termination_reason = "Harbor not installed"

    end_time = datetime.now(UTC)
    duration = (end_time - start_time).total_seconds()

    # Create placeholder scorecard (actual scoring in Phase 2)
    from .schemas.scorecard import (
        ComplianceScore,
        EfficiencyScore,
        FunctionalScore,
    )

    scaffold_meta = {
        "template": scaffold_source.template,
        "version": scaffold_source.version,
        "fingerprint": scaffold_source.manifest.fingerprint,
        "baseline_manifest": baseline_manifest_path.name,
        "workspace_manifest": manifest_path.name,
        "metadata_file": metadata_path.name,
    }

    scorecard = Scorecard(
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
    )

    return EvalRun(
        id=run_id,
        timestamp=start_time.isoformat(),
        config=EvalConfig(
            model=config.model.litellm_model,
            harness=config.agent.value,
            rules_variant=config.rules_variant,
            task_name=task.name,
            scaffold_template=scaffold_source.template,
            scaffold_version=scaffold_source.version,
        ),
        duration_sec=duration,
        terminated_early=terminated_early,
        termination_reason=termination_reason,
        scores=scorecard,
        events=[],
        gate_history=[],
    )
