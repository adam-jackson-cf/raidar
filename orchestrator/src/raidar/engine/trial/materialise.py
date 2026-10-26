"""Materialise one trial's Harbor task from an imported problem (plan D2).

The imported, agent-visible task is copied per trial and then:

- truncated to the trial's steps (``max_checkpoints``), dropping later step dirs;
- given ``[[steps]].min_reward`` when the protocol halts on failure (D3);
- given each step's ``workdir/setup.sh`` (the per-step context reset,
  ``raidar.engine.health.setup_script``) and the network boundary of the arm's provider;
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

from raidar.contract.trial import ProgressionRule
from raidar.design.experiment.arm import resolve_arm
from raidar.design.experiment.loader import LoadedExperiment
from raidar.design.problem.content import content_digest
from raidar.design.problem.harbor_engine import SOLVE_SH
from raidar.engine.health.setup_script import write_setup_scripts
from raidar.engine.trial.task_config import configure_trial_task
from raidar.subject.environment.python.image import image_present

COMPOSE_FILE = "environment/docker-compose.yaml"


class MaterialiseError(ValueError):
    """The trial task cannot be built as planned."""


@dataclass(frozen=True)
class MaterialisedTask:
    path: Path
    digest: str
    arm_apply_s: float


def compose_init(task_dir: Path) -> bool:
    """Whether the task's compose override runs the main service with ``init: true``."""
    path = task_dir / COMPOSE_FILE
    document = yaml.safe_load(path.read_text()) if path.is_file() else None
    services = document.get("services") if isinstance(document, dict) else None
    main = services.get("main") if isinstance(services, dict) else None
    return isinstance(main, dict) and main.get("init") is True


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
    arm: tuple[LoadedExperiment, str, str],
    references: Mapping[str, Path] | None = None,
) -> MaterialisedTask:
    """Build the trial task at ``dest``.

    ``arm`` is ``(loaded, arm_id, planned_digest)``: the arm is resolved again here (timed
    as the N1 *arm apply* phase) and must still have the identity the plan recorded, so a
    release or stage code that changed after planning is refused rather than run under the
    old hash.
    """
    if dest.exists():
        raise MaterialiseError(f"{dest} already exists; trial tasks are never reused")
    loaded, arm_id, planned_digest = arm
    if compose_init(source) != loaded.spec.environment.compose_init:
        raise MaterialiseError(
            f"{source}: compose init is {compose_init(source)}, the experiment requires "
            f"environment.compose_init={loaded.spec.environment.compose_init}"
        )
    shutil.copytree(source, dest, symlinks=True)
    started = time.perf_counter()
    resolved = resolve_arm(loaded.spec, arm_id)
    digest = str(resolved.identity()["digest"])
    if digest != planned_digest:
        raise MaterialiseError(
            f"arm {arm_id!r} resolves to {digest[:12]}, planned {planned_digest[:12]}"
        )
    try:
        configure_trial_task(
            dest, steps, rule, runtime=loaded.spec.runtime, provider=resolved.agent.provider
        )
    except ValueError as exc:
        raise MaterialiseError(str(exc)) from exc
    write_setup_scripts(dest, list(steps))
    arm_apply_s = time.perf_counter() - started
    if references is not None:
        _write_solutions(dest, {step: references[step] for step in steps})
    return MaterialisedTask(dest, content_digest(dest), arm_apply_s)


def environment_cold(task_dir: Path) -> bool:
    """True when the task's environment image is not in the local image store yet."""
    document = tomllib.loads((task_dir / "task.toml").read_text())
    image = (document.get("environment") or {}).get("docker_image")
    return isinstance(image, str) and not image_present(image)
