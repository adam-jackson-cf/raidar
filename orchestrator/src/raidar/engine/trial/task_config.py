"""Render the native Harbor task configuration for one planned trial."""

from __future__ import annotations

import shutil
from pathlib import Path

from harbor.models.task.config import ArtifactConfig, TaskConfig

from raidar.contract.snapshot import app_artifact_config
from raidar.contract.trial import ProgressionRule
from raidar.engine.trial.network import restrict_trial_network


def configure_trial_task(
    task_dir: Path, steps: tuple[str, ...], rule: ProgressionRule, *, runtime: str, provider: str
) -> None:
    """Select checkpoints, pin progression, bound the task's native egress and give every
    step the lab's current ``/app`` artifact (its exclude list grows without re-importing a
    problem, e.g. when a workflow's own state was added to it)."""
    path = task_dir / "task.toml"
    config = TaskConfig.model_validate_toml(path.read_text())
    by_name = {step.name: step for step in config.steps or []}
    missing = [name for name in steps if name not in by_name]
    if missing:
        raise ValueError(f"task {task_dir} has no steps {missing}")
    update = {
        "min_reward": rule.harbor_min_reward(),
        "artifacts": [ArtifactConfig.model_validate(app_artifact_config())],
    }
    kept = [by_name[name].model_copy(update=update) for name in steps]
    for name in set(by_name) - set(steps):
        shutil.rmtree(task_dir / "steps" / name, ignore_errors=True)
    path.write_text(config.model_copy(update={"steps": kept}).model_dump_toml())
    restrict_trial_network(task_dir, runtime=runtime, provider=provider)
