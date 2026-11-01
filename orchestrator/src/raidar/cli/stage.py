"""``raidar stage``: author, lock, check, list and inspect stage packages (``stage/1``).

``new`` copies an in-tree package (default ``raidar/docs-check``) under a new name, ``lock``
generates the wheels-only ``uv.lock``, ``check`` (``stage_fixtures``) runs fixtures offline in a
pack container, and ``list`` and ``info`` read the tree and the register.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

import click
import yaml
from raidar_contract.canonical import IGNORED_TREE_NAMES
from raidar_contract.package import StagePackageError
from raidar_contract.package.registry import MANIFEST_FILE, load_stage_package

from raidar.cli import imports, stage_fixtures, stage_package
from raidar.cli.stage_lock import StageLockError, generate_lock, lock_problems
from raidar.contract.paths import stage_checks_root

DEFAULT_SOURCE = "raidar/docs-check"
IGNORED = tuple(sorted(IGNORED_TREE_NAMES))


def register(main: click.Group) -> None:
    main.add_command(stage)
    imports.register(main, stage)


@click.group()
def stage() -> None:
    """Stage packages: new, lock, check, list, info."""


@stage.command("new")
@click.argument("name")
@click.option(
    "--from",
    "source_name",
    default=DEFAULT_SOURCE,
    show_default=True,
    help="In-tree package under lab/stages/ to copy (<ns>/<name>).",
)
def stage_new(name: str, source_name: str) -> None:
    """Copy the in-tree package --from, fixtures included, to NAME (<ns>/<name>); no overwrite."""
    root = stage_package.stages_root()
    target = root / stage_package.valid_name(name)
    source = root / stage_package.valid_name(source_name)
    if not (source / MANIFEST_FILE).is_file():
        raise click.ClickException(f"no stage package at {source} (see make stage-list)")
    if os.path.lexists(target):
        raise click.ClickException(f"refusing to overwrite {target}")
    _refuse_unsafe(source, target)
    shutil.copytree(source, target, symlinks=False, ignore=shutil.ignore_patterns(*IGNORED))
    try:
        _set_manifest_name(target, target.name)
        load_stage_package(target)
    except (click.ClickException, StagePackageError, OSError, yaml.YAMLError) as error:
        shutil.rmtree(target)
        if isinstance(error, click.ClickException):
            raise
        raise click.ClickException(f"{source_name} cannot be copied: {error}") from error
    click.echo(f"Wrote {target} (from {source_name})")
    click.echo(f"Next: make stage-check NAME={name} TARGET=<name>")


def _refuse_unsafe(source: Path, target: Path) -> None:
    """Refuse symlinked folders and any symlink or special file in the source, before writing."""
    for folder in (source, source.parent, target.parent):
        if folder.is_symlink():
            raise click.ClickException(f"{folder}: symlinked folders are refused")
    for directory, dirnames, filenames in os.walk(source, followlinks=False):
        dirnames[:] = [item for item in dirnames if item not in IGNORED]
        for entry in (*dirnames, *filenames):
            if entry in IGNORED:
                continue
            path = Path(directory, entry)
            kind = stat.S_IFMT(path.lstat().st_mode)
            if kind not in (stat.S_IFDIR, stat.S_IFREG):
                label = "symlinks" if kind == stat.S_IFLNK else "special files"
                raise click.ClickException(f"{path}: {label} are refused")


def _root_values(text: str) -> list[yaml.Event]:
    """Keep each root value's source span, including aliases resolved away by compose."""
    depth, value = 0, False
    values = []
    for event in yaml.parse(text, Loader=yaml.SafeLoader):
        if isinstance(event, yaml.CollectionEndEvent):
            depth -= 1
        if depth == 1 and isinstance(
            event, (yaml.ScalarEvent, yaml.AliasEvent, yaml.CollectionStartEvent)
        ):
            if value:
                values.append(event)
            value = not value
        if isinstance(event, yaml.CollectionStartEvent):
            depth += 1
    return values


def _name_span(text: str, event: yaml.Event, old: str, leaf: str) -> tuple[int, int, str]:
    if isinstance(event, yaml.AliasEvent):
        return event.start_mark.index, event.end_mark.index, leaf
    token = next(
        (
            token
            for token in yaml.scan(text, Loader=yaml.SafeLoader)
            if isinstance(token, yaml.ScalarToken)
            and token.start_mark.index >= event.start_mark.index
            and token.end_mark.index <= event.end_mark.index
        ),
        None,
    )
    if token is None:
        raise click.ClickException("manifest needs a scalar name")
    start, end = token.start_mark.index, token.end_mark.index
    replacement = leaf
    if token.style in ("|", ">"):
        header, newline, body = text[start:end].partition("\n")
        replacement = header + newline + body.replace(old, leaf, 1)
    elif token.style:
        replacement = f"{token.style}{leaf}{token.style}"
    return start, end, replacement


def _set_manifest_name(target: Path, leaf: str) -> None:
    """The one change a copy makes: the manifest's ``name`` is the new folder's leaf."""
    path = target / MANIFEST_FILE
    text = path.read_bytes().decode("utf-8")
    document = yaml.compose(text, Loader=yaml.SafeLoader)
    if not isinstance(document, yaml.MappingNode):
        raise click.ClickException(f"{path}: manifest needs a scalar name")
    entries = zip(document.value, _root_values(text), strict=True)
    for (key, value), event in reversed(list(entries)):
        if not (
            isinstance(key, yaml.ScalarNode)
            and key.value == "name"
            and isinstance(value, yaml.ScalarNode)
        ):
            continue
        start, end, replacement = _name_span(text, event, value.value, leaf)
        mode = stat.S_IMODE(path.stat().st_mode)
        path.chmod(mode | stat.S_IWUSR)
        try:
            path.write_bytes(f"{text[:start]}{replacement}{text[end:]}".encode())
        finally:
            path.chmod(mode)
        return
    raise click.ClickException(f"{path}: manifest needs a scalar name")


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
