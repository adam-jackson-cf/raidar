"""Byte-verified local imports and unverified, identity-free provenance."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import TypedDict

import yaml
from raidar_contract.canonical import IGNORED_TREE_NAMES
from raidar_contract.package.digest import read_stage_tree
from raidar_contract.package.registry import load_stage_package
from raidar_contract.releases import Release, ReleaseError, ReleaseKind, load_register
from raidar_contract.workflow import load_package_workflow, package_workflow_digest

from raidar.contract.paths import releases_file, stages_root, workflows_root
from raidar.subject.stages import resolve_stages


class PackageImportError(ValueError):
    """An unsafe export, byte mismatch or inconsistent provenance record."""


class ImportEntry(TypedDict):
    name: str
    sha256: str
    source: str


def _source(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or value.splitlines() != [value]:
        raise PackageImportError("SOURCE must be one non-empty line")
    return value


def _name(value: str) -> str:
    Release.parse_stage(f"{value}@1")
    if value.startswith("raidar/"):
        raise PackageImportError("the raidar/ namespace is reserved")
    return value


def _entry(row: object, path: Path) -> ImportEntry:
    if not isinstance(row, dict) or set(row) != {"name", "sha256", "source"}:
        raise PackageImportError(f"{path}: each entry is exactly {{name, sha256, source}}")
    name, digest = row["name"], row["sha256"]
    if not isinstance(name, str) or not isinstance(digest, str):
        raise PackageImportError(f"{path}: name and sha256 must be strings")
    _name(name)
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise PackageImportError(f"{path}: sha256 must be 64 lowercase hexadecimal characters")
    return {"name": name, "sha256": digest, "source": _source(row["source"])}


def load_imports(repo_root: Path) -> list[ImportEntry]:
    """Read the strict, one-entry-per-digest ledger; never resolve SOURCE."""
    path = repo_root / "lab" / "imports.yaml"
    if not path.exists():
        return []
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise PackageImportError(f"{path}: unreadable import ledger: {error}") from error
    if not isinstance(document, list):
        raise PackageImportError(f"{path}: expected a list of {{name, sha256, source}}")
    entries: list[ImportEntry] = []
    seen: set[str] = set()
    for row in document:
        entry = _entry(row, path)
        if entry["sha256"] in seen:
            raise PackageImportError(f"{path}: duplicate imported digest {entry['sha256']}")
        seen.add(entry["sha256"])
        entries.append(entry)
    return entries


def _safe_tree(folder: Path) -> None:
    if ".." in folder.parts:
        raise PackageImportError(f"{folder}: traversal is refused")
    for ancestor in (folder, *folder.parents):
        if ancestor.is_symlink():
            raise PackageImportError(f"{ancestor}: symlinked folders are refused")
    # The stage-tree walker checks excluded fixture trees too, without opening special files.
    read_stage_tree(folder)


def _digest(kind: ReleaseKind, folder: Path, name: str, repo_root: Path) -> str:
    if kind == ReleaseKind.STAGE:
        return load_stage_package(folder).digest
    definition = load_package_workflow(folder, name)
    register = load_register(releases_file(repo_root))
    packages = resolve_stages(definition, repo_root, register, draft=False)
    return package_workflow_digest(definition, [stage.entry for stage in packages])


def _install_verified(
    kind: ReleaseKind, staged: Path, target: Path, name: str, digest: str, repo_root: Path
) -> str:
    register = load_register(releases_file(repo_root))
    latest = register.latest(kind, name)
    if latest is not None and register.of(kind)[latest] != digest:
        raise PackageImportError(f"different bytes under existing release {latest}")
    if os.path.lexists(target):
        _safe_tree(target)
        if _digest(kind, target, name, repo_root) != digest:
            raise PackageImportError(f"refusing to overwrite different bytes at {target}")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staged, target)
    version = 1 if latest is None else latest.version
    return f"{name}@{version}: {digest}"


def import_folder(
    kind: ReleaseKind, source: Path, name: str, sha256: str, provenance: str, repo_root: Path
) -> tuple[Path, str]:
    """Copy unchanged export bytes; never overwrite different bytes or register a release."""
    _name(name)
    _source(provenance)
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise PackageImportError("SHA256 must be 64 lowercase hexadecimal characters")
    entries = load_imports(repo_root)
    entry: ImportEntry = {"name": name, "sha256": sha256, "source": provenance}
    recorded = next((row for row in entries if row["sha256"] == sha256), None)
    if recorded is not None and recorded != entry:
        raise PackageImportError("recorded digest has a different name or SOURCE")
    root = stages_root(repo_root) if kind == ReleaseKind.STAGE else workflows_root(repo_root)
    target = root / name
    _safe_tree(source)
    _safe_parent(target.parent)
    # Validate under the requested leaf, without renaming any exported bytes.
    with tempfile.TemporaryDirectory(prefix="raidar-import-") as temporary:
        staged = Path(temporary) / target.name
        shutil.copytree(source, staged, ignore=shutil.ignore_patterns(*sorted(IGNORED_TREE_NAMES)))
        digest = _digest(kind, staged, name, repo_root)
        if digest != sha256:
            raise PackageImportError(f"digest mismatch: expected {sha256}, found {digest}")
        release = _install_verified(kind, staged, target, name, digest, repo_root)
    if recorded is None:
        ledger = repo_root / "lab" / "imports.yaml"
        ledger.write_text(yaml.safe_dump([*entries, entry], sort_keys=False), encoding="utf-8")
    return target, release


def _safe_parent(folder: Path) -> None:
    for ancestor in (folder, *folder.parents):
        if ancestor.is_symlink():
            raise PackageImportError(f"{ancestor}: symlinked folders are refused")


def _folder_issues(
    kind: ReleaseKind, root: Path, repo_root: Path, recorded: set[tuple[str, str]]
) -> list[str]:
    issues: list[str] = []
    if not root.is_dir():
        return issues
    folders = (
        folder
        for namespace in sorted(root.iterdir())
        if namespace.name != "raidar" and namespace.is_dir()
        for folder in sorted(namespace.iterdir())
        if folder.is_dir()
    )
    for folder in folders:
        name = f"{folder.parent.name}/{folder.name}"
        try:
            _safe_tree(folder)
            digest = _digest(kind, folder, name, repo_root)
        except (ValueError, OSError) as error:
            issues.append(f"{folder}: {error}")
            continue
        if (name, digest) not in recorded:
            issues.append(f"{kind.value}/{name}: no matching import entry for {digest}")
    return issues


def import_issues(repo_root: Path) -> list[str]:
    """Check imported folders and all historical releases; provenance presence, never truth."""
    try:
        entries = load_imports(repo_root)
    except (PackageImportError, ReleaseError) as error:
        return [str(error)]
    recorded = {(row["name"], row["sha256"]) for row in entries}
    register = load_register(releases_file(repo_root))
    issues = []
    for kind, root in (
        (ReleaseKind.STAGE, stages_root(repo_root)),
        (ReleaseKind.WORKFLOW, workflows_root(repo_root)),
    ):
        issues += _folder_issues(kind, root, repo_root, recorded)
        issues += [
            f"{release}: no matching import entry for {digest}"
            for release, digest in register.of(kind).items()
            if "/" in release.name
            and not release.name.startswith("raidar/")
            and (release.name, digest) not in recorded
        ]
    return issues
