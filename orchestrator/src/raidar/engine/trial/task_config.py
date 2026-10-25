"""Render the native Harbor task configuration for one planned trial."""

from __future__ import annotations

import shutil
from pathlib import Path

from harbor.models.task.config import TaskConfig

from raidar.contract.trial import ProgressionRule
from raidar.engine.trial.network import restrict_trial_network


def configure_trial_task(
    task_dir: Path,
    steps: tuple[str, ...],
    rule: ProgressionRule,
    *,
    runtime: str,
    provider: str,
    component_hosts: tuple[str, ...],
) -> None:
    """Select checkpoints, pin progression and bound the task's native egress."""
    path = task_dir / "task.toml"
    config = TaskConfig.model_validate_toml(path.read_text())
    by_name = {step.name: step for step in config.steps or []}
    missing = [name for name in steps if name not in by_name]
    if missing:
        raise ValueError(f"task {task_dir} has no steps {missing}")
    min_reward = rule.harbor_min_reward()
    kept = [by_name[name].model_copy(update={"min_reward": min_reward}) for name in steps]
    for name in set(by_name) - set(steps):
        shutil.rmtree(task_dir / "steps" / name, ignore_errors=True)
    path.write_text(config.model_copy(update={"steps": kept}).model_dump_toml())
    restrict_trial_network(
        task_dir, runtime=runtime, provider=provider, component_hosts=component_hosts
    )
