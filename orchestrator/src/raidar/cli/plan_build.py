"""Shared plan construction for ``raidar plan`` and ``raidar run``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from raidar.cli.experiment_spec import _load
from raidar.execution.agent_config import AgentOverride
from raidar.execution.plan import ExperimentPlan, PlanError, PlanOptions, build_plan
from raidar.packs.drift import LockedPackError, lock_runtime_pack
from raidar.problems.store import ProblemStore, ProblemStoreError, open_store
from raidar.spec.lock import (
    LockError,
    StaleLockError,
    build_lock,
    check_lock_file,
    default_lock_path,
    render_lock,
)


def _current_lock(loaded: Any, lock_path: Path) -> dict[str, Any]:
    """The lock, refused unless relocking the experiment reproduces it byte for byte.

    The refusal names each stale section; an assessment-only change relocks to
    the same ``lock_sha256``, so earlier runs still match the new lock. Only the
    host's Python patch release (``toolchain.python``, outside ``lock_sha256``)
    may differ from a committed lock, as ``make experiment-locks-check`` allows;
    the job records the host's value in its own ``job.lock.json``.
    """
    try:
        current = build_lock(loaded, lock_path=lock_path)
        note = check_lock_file(lock_path, current, ignore_host_python=True)
    except (LockError, StaleLockError) as exc:
        raise click.ClickException(str(exc)) from exc
    if note:
        click.echo(f"note: {note}", err=True)
    return dict(json.loads(render_lock(current)))


def build_cli_plan(options: dict[str, Any]) -> tuple[ExperimentPlan, ProblemStore]:
    loaded = _load(
        options["experiment"],
        options["attempts"],
        options["seed"],
        options["assignments"],
    )
    lock = _current_lock(loaded, options["lock_path"] or default_lock_path(loaded.path))
    override: AgentOverride | None = options["agent_override"]
    try:
        store = open_store(loaded.base_dir)
        plan = build_plan(
            loaded,
            lock,
            store=store,
            pack=lock_runtime_pack(lock),
            options=PlanOptions(
                arms=tuple(options["arms"]),
                attempts=tuple(options["attempts_selected"]),
                max_checkpoints=options["max_checkpoints"],
                agent_override=override,
                concurrency=options["concurrency"],
            ),
        )
    except (LockedPackError, PlanError, ProblemStoreError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    return plan, store
