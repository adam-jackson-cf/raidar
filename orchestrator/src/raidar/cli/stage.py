"""``raidar stage``: author, lock, check, list and inspect stage packages (``stage/1``).

``new`` copies an in-tree package (default ``raidar/docs-check``) under a new name, ``lock``
generates the wheels-only ``uv.lock``, ``check`` (``stage_fixtures``) runs fixtures offline in a
pack container, and ``list`` and ``info`` read the tree and the register.
"""

from __future__ import annotations

import json

import click
from raidar_contract.package import StagePackageError

from raidar.cli import imports, stage_fixtures, stage_package
from raidar.cli.stage_lock import StageLockError, generate_lock, lock_problems
from raidar.contract.paths import stage_checks_root
from raidar.subject.package.registry import load_stage_package


def register(main: click.Group) -> None:
    main.add_command(stage)
    imports.register(main, stage)


@click.group()
def stage() -> None:
    """Stage packages: new, lock, check, list, info."""


stage.add_command(stage_package.new_command)


@stage.command("lock")
@click.argument("name")
def stage_lock(name: str) -> None:
    """Generate NAME's wheels-only tool/uv.lock (resolution only; no package code runs)."""
    root, _ = stage_package.load_named(name)
    try:
        generate_lock(root)
    except StageLockError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"Wrote {root / 'tool' / 'uv.lock'}")


stage.add_command(stage_fixtures.check_command)


@stage.command("list")
def stage_list() -> None:
    """List the packages under lab/stages/ with their release state."""
    register_ = stage_package.register()
    rows = stage_package.package_folders()
    if not rows:
        click.echo("no stage packages")
    for folder in rows:
        name = folder.relative_to(stage_package.stages_root()).as_posix()
        try:
            package = load_stage_package(folder)
        except StagePackageError as error:
            click.echo(f"{name}\tINVALID: {error}")
            continue
        state, _ = stage_package.release_state(name, package.digest, register_)
        click.echo(f"{name}\t{package.digest[:12]}\t{state}\t{package.manifest.summary}")


@stage.command("info")
@click.argument("name")
def stage_info(name: str) -> None:
    """Show NAME's manifest facts, digest, release state, lock state and canary record."""
    root, package = stage_package.load_named(name)
    manifest = package.manifest
    state, next_line = stage_package.release_state(name, package.digest, stage_package.register())
    record = stage_checks_root() / f"{package.digest}.json"
    document = {
        "name": name,
        "summary": manifest.summary,
        "digest": package.digest,
        "release": state,
        "register": next_line,
        "requires": manifest.requires,
        "params": manifest.params,
        "tokens": list(manifest.tokens),
        "commands": {} if manifest.tool is None else manifest.tool.commands,
        "gate": [check.name for check in manifest.gate],
        "stop": None if manifest.on_stop is None else manifest.on_stop.max_rounds,
        "outputs": sorted(manifest.outputs),
        "skills": list(manifest.skills),
        "fixtures": list(package.fixture_cases()),
        "lock": lock_problems(root) or "current",
        "canary_record": str(record) if record.is_file() else None,
    }
    click.echo(json.dumps(document, indent=2, default=str))
