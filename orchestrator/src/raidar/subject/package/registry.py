"""Lab stage lookup and repository-layout refusals over the portable package parser."""

from __future__ import annotations

from pathlib import Path

from raidar_contract.package import StagePackageError
from raidar_contract.package.manifest import NAME_RE, StagePackage
from raidar_contract.package.registry import load_stage_package as load_portable_package
from raidar_contract.references import Release

from raidar.contract.paths import stages_root

STAGES_DIR = "stages"


def stage_folder(release: Release, repo_root: Path) -> Path:
    """The folder ``lab/stages/<ns>/<name>`` a release reference names below ``repo_root``."""
    return stages_root(repo_root) / release.name


def load_stage_package(root: Path) -> StagePackage:
    """Validate the lab's location policy before parsing portable package contents."""
    _check_location(root)
    return load_portable_package(root)


def _check_location(root: Path) -> None:
    """Refuse a symlinked package root or ancestor below ``stages``, and a path that is not
    exactly ``stages/<namespace>/<name>`` with valid components."""
    if root.is_symlink():
        raise StagePackageError(f"{root}: a package root is never a symlink")
    if STAGES_DIR not in root.parts:
        return
    index = len(root.parts) - 1 - root.parts[::-1].index(STAGES_DIR)
    below = root.parts[index + 1 :]
    if len(below) != 2 or not all(NAME_RE.fullmatch(part) for part in below):
        raise StagePackageError(f"{root}: expected {STAGES_DIR}/<namespace>/<name>")
    for ancestor in (root.parent, root.parent.parent):
        if ancestor.is_symlink():
            raise StagePackageError(f"{ancestor}: symlinked ancestors are refused")
