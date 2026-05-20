"""Deterministic scenario-revision cloning helpers."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .schemas.scenario import ScenarioDefinition

REVISION_PATTERN = re.compile(r"^v(\d+)$")
FALLBACK_IGNORE_PATTERNS = (
    ".DS_Store",
    ".next",
    "coverage",
    "node_modules",
    ".pytest_cache",
    ".cache",
    "dist",
    "build",
    "playwright-report",
    "test-results",
    "*.tsbuildinfo",
)


@dataclass(frozen=True, slots=True)
class ScenarioCloneResult:
    """Artifacts created by scenario-revision cloning."""

    scenario_root: Path
    source_revision: str
    target_revision: str
    parent_revision: str
    target_scenario_yaml: Path


def _validate_revision_label(revision: str) -> int:
    match = REVISION_PATTERN.fullmatch(revision)
    if match is None:
        raise ValueError(f"Invalid revision label '{revision}'. Expected format 'v###'.")
    return int(match.group(1))


def next_scenario_revision(source_revision: str) -> str:
    """Return the next deterministic revision label for a scenario."""

    numeric = _validate_revision_label(source_revision) + 1
    width = max(3, len(source_revision) - 1)
    return f"v{numeric:0{width}d}"


def clone_scenario_revision(
    *,
    scenario_root: Path,
    source_revision: str,
    target_revision: str | None = None,
) -> ScenarioCloneResult:
    """Clone one scenario revision to another and update scenario metadata."""

    source_dir = scenario_root / source_revision
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Source scenario revision directory does not exist: {source_dir}")

    source_scenario_yaml = source_dir / "scenario.yaml"
    if not source_scenario_yaml.is_file():
        raise FileNotFoundError(f"Source scenario definition not found: {source_scenario_yaml}")

    resolved_target = target_revision or next_scenario_revision(source_revision)
    _validate_revision_label(resolved_target)
    if resolved_target == source_revision:
        raise ValueError("Target revision must differ from source revision.")

    target_dir = scenario_root / resolved_target
    if target_dir.exists():
        raise FileExistsError(f"Target scenario revision directory already exists: {target_dir}")

    _copy_scenario_tree(source_dir=source_dir, target_dir=target_dir)

    try:
        target_scenario_yaml = target_dir / "scenario.yaml"
        scenario_def = ScenarioDefinition.from_yaml(target_scenario_yaml)
        scenario_def.scenario_revision = resolved_target
        scenario_def.parent_revision = source_revision
        scenario_def.to_yaml(target_scenario_yaml)
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise

    return ScenarioCloneResult(
        scenario_root=scenario_root,
        source_revision=source_revision,
        target_revision=resolved_target,
        parent_revision=source_revision,
        target_scenario_yaml=target_scenario_yaml,
    )


def _copy_scenario_tree(*, source_dir: Path, target_dir: Path) -> None:
    """Copy one scenario revision while excluding repo-ignored local artifacts."""

    repo_root = _git_repo_root(source_dir)
    if (
        repo_root is not None
        and _is_relative_to(source_dir, repo_root)
        and not _path_is_git_ignored(repo_root=repo_root, path=source_dir)
    ):
        _copy_scenario_tree_from_git(
            repo_root=repo_root, source_dir=source_dir, target_dir=target_dir
        )
        return

    shutil.copytree(
        source_dir,
        target_dir,
        ignore=shutil.ignore_patterns(*FALLBACK_IGNORE_PATTERNS),
    )


def _git_repo_root(path: Path) -> Path | None:
    """Return the enclosing git repository root, if one exists."""

    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    repo_root = result.stdout.strip()
    return Path(repo_root) if repo_root else None


def _copy_scenario_tree_from_git(*, repo_root: Path, source_dir: Path, target_dir: Path) -> None:
    """Copy tracked and non-ignored files from the repo view of the source tree."""

    source_rel = source_dir.relative_to(repo_root)
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            str(source_rel),
        ],
        check=True,
        capture_output=True,
    )

    target_dir.mkdir(parents=True, exist_ok=False)
    for entry in result.stdout.split(b"\0"):
        if not entry:
            continue
        source_path = repo_root / entry.decode("utf-8")
        if not source_path.is_file():
            continue
        destination = target_dir / source_path.relative_to(source_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)


def _path_is_git_ignored(*, repo_root: Path, path: Path) -> bool:
    """Return whether git ignore rules exclude the source tree itself."""

    result = subprocess.run(
        ["git", "-C", str(repo_root), "check-ignore", "-q", str(path.relative_to(repo_root))],
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def _is_relative_to(path: Path, parent: Path) -> bool:
    """Return whether path is located under parent."""

    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
