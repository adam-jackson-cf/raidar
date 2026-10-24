"""Materialise one trial's Harbor task from an imported problem (plan D2).

The imported, agent-visible task is copied per trial and then:

- truncated to the trial's steps (``max_checkpoints``), dropping later step dirs;
- given ``[[steps]].min_reward`` when the protocol halts on failure (D3);
- given the arm's compiled condition: each step's ``workdir/setup.sh`` (the
  per-step context reset plus component support files) and the first step's
  workspace overlay (WS-C);
- for a no-LLM ``oracle`` agent override only, given per-step solutions that
  install the problem's host-only reference payloads (the validation engine's
  ``solve.sh``). Real arms never carry solutions.
"""

from __future__ import annotations

import shutil
import stat
import time
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml
from harbor.models.task.config import TaskConfig

from raidar.design.experiment.arm import compile_arm
from raidar.design.experiment.loader import LoadedExperiment
from raidar.design.problem.content import content_digest
from raidar.design.problem.harbor_engine import SOLVE_SH
from raidar.engine.trial.models import ProgressionRule
from raidar.subject.environment.python.image import image_present
from raidar.subject.treatment import CompiledCondition
from raidar.subject.treatment.runtime.provision import AnyProvision, trial_provisions

TASK_FILE = "task.toml"
COMPOSE_FILE = "environment/docker-compose.yaml"


class MaterialiseError(ValueError):
    """The trial task cannot be built as planned."""


@dataclass(frozen=True)
class MaterialisedTask:
    path: Path
    digest: str
    compiled: CompiledCondition
    component_apply_s: float

    @property
    def provisions(self) -> tuple[AnyProvision, ...]:
        """Per-trial tool installs the arm's components need (``runtime.provision``)."""
        return trial_provisions(self.compiled)


def compose_init(task_dir: Path) -> bool:
    """Whether the task's compose override runs the main service with ``init: true``."""
    path = task_dir / COMPOSE_FILE
    document = yaml.safe_load(path.read_text()) if path.is_file() else None
    services = document.get("services") if isinstance(document, dict) else None
    main = services.get("main") if isinstance(services, dict) else None
    return isinstance(main, dict) and main.get("init") is True


def _rewrite_task_toml(task_dir: Path, steps: tuple[str, ...], rule: ProgressionRule) -> None:
    path = task_dir / TASK_FILE
    config = TaskConfig.model_validate_toml(path.read_text())
    by_name = {step.name: step for step in config.steps or []}
    missing = [name for name in steps if name not in by_name]
    if missing:
        raise MaterialiseError(f"task {task_dir} has no steps {missing}")
    min_reward = rule.harbor_min_reward()
    kept = [by_name[name].model_copy(update={"min_reward": min_reward}) for name in steps]
    for name in set(by_name) - set(steps):
        shutil.rmtree(task_dir / "steps" / name, ignore_errors=True)
    path.write_text(config.model_copy(update={"steps": kept}).model_dump_toml())


def _write_solutions(task_dir: Path, references: Mapping[str, Path]) -> None:
    for step, payload in references.items():
        solution = task_dir / "steps" / step / "solution"
        shutil.copytree(payload, solution / "payload", symlinks=True)
        solve = solution / "solve.sh"
        solve.write_text(SOLVE_SH)
        solve.chmod(solve.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def materialise_task(
    source: Path,
    dest: Path,
    *,
    steps: tuple[str, ...],
    rule: ProgressionRule,
    condition: tuple[LoadedExperiment, str, str],
    references: Mapping[str, Path] | None = None,
) -> MaterialisedTask:
    """Build the trial task at ``dest``.

    ``condition`` is ``(loaded, arm_id, planned_digest)``: the arm is compiled
    again here (timed as the N1 *component apply* phase) and must still match
    the digest the plan recorded, so a component input that changed after
    planning is refused rather than run under the old hash.
    """
    if dest.exists():
        raise MaterialiseError(f"{dest} already exists; trial tasks are never reused")
    loaded, arm_id, planned_digest = condition
    if compose_init(source) != loaded.spec.environment.compose_init:
        raise MaterialiseError(
            f"{source}: compose init is {compose_init(source)}, the experiment requires "
            f"environment.compose_init={loaded.spec.environment.compose_init}"
        )
    shutil.copytree(source, dest, symlinks=True)
    _rewrite_task_toml(dest, steps, rule)
    started = time.perf_counter()
    compiled: CompiledCondition = compile_arm(loaded.spec, arm_id, base_dir=loaded.base_dir)
    if compiled.digest != planned_digest:
        raise MaterialiseError(
            f"arm {arm_id!r} compiles to {compiled.digest[:12]}, planned {planned_digest[:12]}"
        )
    compiled.write_task_steps(dest, list(steps))
    component_apply_s = time.perf_counter() - started
    if references is not None:
        _write_solutions(dest, {step: references[step] for step in steps})
    return MaterialisedTask(dest, content_digest(dest), compiled, component_apply_s)


def environment_cold(task_dir: Path) -> bool:
    """True when the task's environment image is not in the local image store yet."""
    document = tomllib.loads((task_dir / "task.toml").read_text())
    image = (document.get("environment") or {}).get("docker_image")
    return isinstance(image, str) and not image_present(image)
