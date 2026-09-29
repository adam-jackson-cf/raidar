"""``raidar validate`` and ``raidar lock`` for ``experiment.yaml`` files."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import click

from raidar.spec.loader import LoadedExperiment, load_experiment, overrides_from_cli
from raidar.spec.lock import (
    DEFAULT_LOCK_NAME,
    LockError,
    build_lock,
    default_resolvers,
    render_lock,
)
from raidar.spec.references import RefKind
from raidar.spec.validation import SpecValidationError


def register(main: click.Group) -> None:
    main.add_command(validate_command)
    main.add_command(lock_command)


def _override_options(command: Callable[..., Any]) -> Callable[..., Any]:
    decorators = [
        click.argument("experiment", type=click.Path(exists=True, dir_okay=False, path_type=Path)),
        click.option("--blocks", type=int, default=None, help="Override analysis.blocks."),
        click.option("--seed", type=int, default=None, help="Override analysis.seed."),
        click.option(
            "--concurrency", type=int, default=None, help="Override analysis.concurrency."
        ),
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
    blocks: int | None,
    seed: int | None,
    concurrency: int | None,
    assignments: Sequence[str],
) -> LoadedExperiment:
    overrides = overrides_from_cli(
        blocks=blocks, seed=seed, concurrency=concurrency, assignments=assignments
    )
    try:
        return load_experiment(experiment, overrides)
    except SpecValidationError as exc:
        raise click.ClickException(str(exc)) from exc


@click.command("validate")
@_override_options
def validate_command(
    experiment: Path,
    blocks: int | None,
    seed: int | None,
    concurrency: int | None,
    assignments: tuple[str, ...],
) -> None:
    """Validate an experiment.yaml (schema, cross-references, factor guard)."""
    loaded = _load(experiment, blocks, seed, concurrency, assignments)
    spec = loaded.spec
    available = default_resolvers().available_kinds()
    click.echo("Experiment validation passed.")
    click.echo(f"  id: {spec.id}")
    click.echo(f"  problems: {len(spec.problems)}")
    click.echo(f"  arms: {', '.join(arm.id for arm in spec.arms)}")
    click.echo(f"  factors: {', '.join(spec.factors) or 'none'}")
    click.echo(f"  baseline_arm: {spec.analysis.baseline_arm}")
    click.echo(f"  blocks: {spec.analysis.blocks}  concurrency: {spec.analysis.concurrency}")
    click.echo(f"  headline_policy: {spec.assessment.headline_policy or 'default by K (at lock)'}")
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
    help=f"Lock file path (default: {DEFAULT_LOCK_NAME} next to the experiment).",
)
@click.option("--check", is_flag=True, help="Fail if the existing lock differs; write nothing.")
def lock_command(
    experiment: Path,
    blocks: int | None,
    seed: int | None,
    concurrency: int | None,
    assignments: tuple[str, ...],
    output: Path | None,
    check: bool,
) -> None:
    """Resolve an experiment to content hashes and pinned versions."""
    loaded = _load(experiment, blocks, seed, concurrency, assignments)
    lock_path = output or loaded.base_dir / DEFAULT_LOCK_NAME
    try:
        rendered = render_lock(build_lock(loaded, lock_path=lock_path))
    except LockError as exc:
        raise click.ClickException(str(exc)) from exc
    if check:
        if not lock_path.is_file() or lock_path.read_bytes() != rendered:
            raise click.ClickException(f"{lock_path} is missing or out of date; run raidar lock")
        click.echo(f"Lock is current: {lock_path}")
        return
    lock_path.write_bytes(rendered)
    click.echo(f"Wrote {lock_path}")
