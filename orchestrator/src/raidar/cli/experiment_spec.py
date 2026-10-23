"""``raidar validate`` and ``raidar lock`` for ``experiment.yaml`` files."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import click

from raidar.components.definition.arm_check import check_arm_components
from raidar.spec.loader import LoadedExperiment, load_experiment, overrides_from_cli
from raidar.spec.lock import (
    LockError,
    StaleLockError,
    build_lock,
    check_lock_file,
    default_lock_path,
    default_resolvers,
    render_lock,
)
from raidar.spec.references import RefKind
from raidar.spec.validation import SpecValidationError, spec_warnings


def register(main: click.Group) -> None:
    main.add_command(validate_command)
    main.add_command(lock_command)


def _override_options(command: Callable[..., Any]) -> Callable[..., Any]:
    decorators = [
        click.argument("experiment", type=click.Path(exists=True, dir_okay=False, path_type=Path)),
        click.option("--attempts", type=int, default=None, help="Override analysis.attempts."),
        click.option("--seed", type=int, default=None, help="Override analysis.seed."),
        click.option(
            "--set",
            "assignments",
            multiple=True,
            metavar="KEY=VALUE",
            help="Dotted-path override; wins over flags, which win over the file.",
        ),
    ]
    for decorator in reversed(decorators):
        command = decorator(command)
    return command


def _load(
    experiment: Path,
    attempts: int | None,
    seed: int | None,
    assignments: Sequence[str],
) -> LoadedExperiment:
    overrides = overrides_from_cli(attempts=attempts, seed=seed, assignments=assignments)
    try:
        loaded = load_experiment(experiment, overrides)
        # Every arm must compile for its agent (C1): rejected here, never at run time.
        check_arm_components(loaded)
    except SpecValidationError as exc:
        raise click.ClickException(str(exc)) from exc
    for warning in spec_warnings(loaded.spec):
        click.echo(f"warning: {warning.render()}", err=True)
    return loaded


@click.command("validate")
@_override_options
def validate_command(
    experiment: Path,
    attempts: int | None,
    seed: int | None,
    assignments: tuple[str, ...],
) -> None:
    """Validate an experiment.yaml (schema, kind and naming rules, factor guard, components)."""
    loaded = _load(experiment, attempts, seed, assignments)
    spec = loaded.spec
    available = default_resolvers().available_kinds()
    click.echo("Experiment validation passed.")
    click.echo(f"  id: {spec.id}  kind: {spec.kind}  runtime: {spec.runtime}")
    click.echo(f"  problems: {len(spec.problems)}")
    click.echo(f"  arms: {', '.join(arm.id for arm in spec.arms)}")
    click.echo(f"  factors: {', '.join(spec.factors) or 'none'}")
    click.echo(f"  baseline_arm: {spec.analysis.baseline_arm}")
    click.echo(f"  attempts: {spec.analysis.attempts}  concurrency: {spec.analysis.concurrency}")
    click.echo(f"  metric: {spec.assessment.metric or 'default by K (at lock)'}")
    for kind, refs in spec.references().items():
        if not refs:
            continue
        state = "resolver available" if RefKind(kind) in available else "resolver pending"
        click.echo(f"  {kind} references: {len(refs)} ({state})")


@click.command("lock")
@_override_options
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Lock file path (default: <experiment name>.lock.json next to the experiment).",
)
@click.option(
    "--check",
    is_flag=True,
    help="Fail if the existing lock differs, naming each stale section; write nothing.",
)
@click.option(
    "--ignore-host-python",
    is_flag=True,
    help="With --check: accept a different host Python patch in toolchain.python only.",
)
def lock_command(
    experiment: Path,
    attempts: int | None,
    seed: int | None,
    assignments: tuple[str, ...],
    output: Path | None,
    check: bool,
    ignore_host_python: bool,
) -> None:
    """Resolve an experiment to content hashes and pinned versions.

    ``lock_sha256`` is the digest of the execution section only: relocking
    after a scorer, metric, threshold or toolchain change keeps it, so jobs
    executed under the earlier lock still match.
    """
    if ignore_host_python and not check:
        raise click.UsageError("--ignore-host-python needs --check")
    loaded = _load(experiment, attempts, seed, assignments)
    lock_path = output or default_lock_path(loaded.path)
    try:
        lock = build_lock(loaded, lock_path=lock_path)
        note = (
            check_lock_file(lock_path, lock, ignore_host_python=ignore_host_python)
            if check
            else None
        )
    except (LockError, StaleLockError) as exc:
        raise click.ClickException(str(exc)) from exc
    if check:
        if note:
            click.echo(f"Note: {note}")
        click.echo(f"Lock is current: {lock_path} (lock_sha256 {str(lock['lock_sha256'])[:12]})")
        return
    lock_path.write_bytes(render_lock(lock))
    click.echo(f"Wrote {lock_path} (lock_sha256 {str(lock['lock_sha256'])[:12]})")
