"""Finding, naming and loading stage packages under ``lab/stages/``, and their release state."""

from __future__ import annotations

import re
from pathlib import Path

import click
from raidar_contract.package import StagePackageError
from raidar_contract.package.manifest import StagePackage
from raidar_contract.package.registry import MANIFEST_FILE, STAGES_DIR, load_stage_package
from raidar_contract.releases import ReleaseKind, ReleaseRegister, load_register

from raidar.contract.paths import lab_root, releases_file

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*$")


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
    return f"changed since {latest}", f"register stages: {name}@{latest.version + 1}: {digest}"
