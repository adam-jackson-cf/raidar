"""Finding, naming and loading stage packages under ``lab/stages/``, and their release state."""

from __future__ import annotations

import os
import re
import shutil
import stat
from pathlib import Path

import click
import yaml

from raidar.contract.paths import lab_root, releases_file
from raidar.contract.releases import ReleaseKind, ReleaseRegister, load_register
from raidar.subject.package.registry import STAGES_DIR, load_stage_package
from raidar_contract.canonical import IGNORED_TREE_NAMES
from raidar_contract.package import StagePackageError
from raidar_contract.package.manifest import StagePackage
from raidar_contract.package.registry import MANIFEST_FILE

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*$")
DEFAULT_SOURCE = "raidar/docs-check"
IGNORED = tuple(sorted(IGNORED_TREE_NAMES))


def stages_root() -> Path:
    return lab_root() / STAGES_DIR


def valid_name(value: str) -> str:
    """``<ns>/<name>``; anything else (including a bare name) is a bad parameter."""
    if not _NAME.fullmatch(value):
        raise click.BadParameter(
            f"{value!r} is not a package name: <ns>/<name>, lowercase letters, digits, '-'",
            param_hint="NAME",
        )
    return value


def load_named(name: str) -> tuple[Path, StagePackage]:
    """The folder and validated package NAME; ``ClickException`` says what is wrong."""
    root = stages_root() / valid_name(name)
    if not root.is_dir():
        raise click.ClickException(f"no stage package at {root} (see make stage-list)")
    try:
        return root, load_stage_package(root)
    except StagePackageError as error:
        raise click.ClickException(f"{name}: {error}") from error


def package_folders() -> list[Path]:
    """Every folder ``lab/stages/<ns>/<name>/`` holding a ``stage.yaml``."""
    root = stages_root()
    if not root.is_dir():
        return []
    found = [item for item in root.rglob(MANIFEST_FILE) if len(item.relative_to(root).parts) == 3]
    return sorted(item.parent for item in found)


def register() -> ReleaseRegister:
    return load_register(releases_file())


def release_state(name: str, digest: str, register: ReleaseRegister) -> tuple[str, str | None]:
    """(state, next register line): ``released``, ``changed`` or ``unreleased``."""
    latest = register.latest(ReleaseKind.STAGE, name)
    if latest is None:
        return "unreleased", f"register stages: {name}@1: {digest}"
    if register.of(ReleaseKind.STAGE)[latest] == digest:
        return f"released as {latest}", None
    return f"changed since {latest}", f"register stages: {latest}: {digest}"


@click.command("new")
@click.argument("name")
@click.option(
    "--from",
    "source_name",
    default=DEFAULT_SOURCE,
    show_default=True,
    help="In-tree package under lab/stages/ to copy (<ns>/<name>).",
)
def new_command(name: str, source_name: str) -> None:
    """Copy the in-tree package --from, fixtures included, to NAME (<ns>/<name>); no overwrite."""
    root = stages_root()
    target = root / valid_name(name)
    source = root / valid_name(source_name)
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
