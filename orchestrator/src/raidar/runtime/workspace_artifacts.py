"""Workspace visual evidence, hydration, pruning, and diff helpers."""

from __future__ import annotations

import errno
import json
import os
import shlex
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any

from raidar.audit.workspace_diff import diff_directories
from raidar.config import settings
from raidar.runtime.models import HarborExecutionResult, RunRequest
from raidar.runtime.wait import wait_for_remove_tree_retry
from raidar.runtime.workspace_cache import _directory_size_bytes
from raidar.schemas.scenario import ScenarioDefinition

WORKSPACE_PRUNE_DIRS: tuple[str, ...] = (
    "node_modules",
    ".next",
    ".turbo",
    ".cache",
    "coverage",
    "dist",
    "build",
    "tmp",
)


def _resolve_homepage_screenshot_command(
    task: ScenarioDefinition, workspace: Path
) -> list[str] | None:
    del workspace
    if task.visual and task.visual.screenshot_command:
        return list(task.visual.screenshot_command)
    return None


def _visual_reference_assets(request: RunRequest) -> list[tuple[Path, Path]]:
    """Return scenario-local visual reference assets and their relative targets."""
    if request.scenario.visual is None:
        return []
    reference_path = Path(request.scenario.visual.reference_image)
    if reference_path.is_absolute():
        return []
    source_reference = (request.scenario_dir / reference_path).resolve()
    if not source_reference.exists():
        return []

    assets = [(source_reference, reference_path)]
    assets.extend(
        (sibling, reference_path.parent / sibling.name)
        for sibling in sorted(
            source_reference.parent.glob(
                f"{source_reference.stem}-region-*{source_reference.suffix}"
            )
        )
    )
    return assets


def _visual_region_names(request: RunRequest) -> list[str]:
    """Return authored or inferred visual region names for one scenario."""
    if request.scenario.visual is None:
        return []
    configured = [region.name for region in request.scenario.visual.regions]
    if configured:
        return configured

    prefix = f"{Path(request.scenario.visual.reference_image).stem}-region-"
    suffix = Path(request.scenario.visual.reference_image).suffix
    inferred: list[str] = []
    for _, relative_target in _visual_reference_assets(request):
        filename = relative_target.name
        if not filename.startswith(prefix) or not filename.endswith(suffix):
            continue
        inferred.append(filename[len(prefix) : len(filename) - len(suffix)])
    return inferred


def _run_homepage_capture_command(
    command: list[str], workspace: Path, output_path: Path
) -> tuple[Path | None, str | None]:
    actual_path = workspace / "actual.png"
    actual_path.unlink(missing_ok=True)

    install_error = _ensure_workspace_capture_dependencies(workspace)
    if install_error:
        return None, install_error

    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=settings.timeouts.screenshot,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)

    if completed.returncode != 0:
        output = (completed.stdout + "\n" + completed.stderr).strip()[:4000]
        rendered = " ".join(shlex.quote(part) for part in command)
        return None, f"`{rendered}` exited {completed.returncode}: {output}"

    if not actual_path.exists():
        rendered = " ".join(shlex.quote(part) for part in command)
        return None, f"`{rendered}` completed without producing {actual_path}"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(actual_path, output_path)
    actual_path.unlink(missing_ok=True)
    return output_path, None


def _ensure_workspace_capture_dependencies(workspace: Path) -> str | None:
    from raidar.runtime.workspace import _workspace_runtime_env

    package_json = workspace / "package.json"
    lockfile = workspace / "bun.lock"
    node_modules = workspace / "node_modules"
    next_package = node_modules / "next" / "package.json"
    if not package_json.exists() or not lockfile.exists() or next_package.exists():
        return None

    try:
        completed = subprocess.run(
            ["bun", "install", "--frozen-lockfile"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=settings.timeouts.screenshot,
            check=False,
            env=_workspace_runtime_env(workspace),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return f"Failed to install workspace dependencies before capture: {exc}"

    if completed.returncode != 0:
        output = (completed.stdout + "\n" + completed.stderr).strip()[:4000]
        return (
            "Failed to install workspace dependencies before capture: "
            f"`bun install --frozen-lockfile` exited {completed.returncode}: {output}"
        )
    return None


def _safe_extract_tarball(archive_path: Path, target_dir: Path) -> None:
    target_root = target_dir.resolve()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            member_target = (target_root / member.name).resolve()
            if member_target != target_root and not str(member_target).startswith(
                f"{target_root}{os.sep}"
            ):
                raise RuntimeError(f"Unsafe tar member path: {member.name}")
            if member.isdir():
                member_target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise RuntimeError(f"Unsupported tar member type: {member.name}")
            member_target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError(f"Unable to read tar member: {member.name}")
            with source, member_target.open("wb") as destination:
                shutil.copyfileobj(source, destination)


def _hydrate_workspace_from_final_app(
    harbor_result: HarborExecutionResult, workspace: Path
) -> tuple[Path | None, str | None]:
    if not harbor_result.trial_dir:
        return None, "Harbor trial directory missing; cannot hydrate post-run workspace."
    archive_path = harbor_result.trial_dir / "agent" / "final-app.tar.gz"
    if not archive_path.exists():
        return None, f"Missing final app archive: {archive_path}"
    try:
        _safe_extract_tarball(archive_path, workspace)
    except (OSError, tarfile.TarError, RuntimeError) as exc:
        return None, f"Failed to hydrate workspace from {archive_path}: {exc}"
    return archive_path, None


def _remove_tree_with_retries(path: Path, *, attempts: int = 3, delay_sec: float = 0.2) -> None:
    last_error: OSError | None = None
    transient_errnos = {errno.ENOTEMPTY, errno.EBUSY, errno.EPERM}
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            last_error = exc
            if exc.errno not in transient_errnos or attempt == attempts - 1:
                raise
            wait_for_remove_tree_retry(delay_sec)
    if last_error is not None:
        raise last_error


def _prune_workspace_artifacts(workspace: Path) -> dict[str, Any]:
    removed: list[str] = []
    reclaimed_bytes = 0
    for dirname in WORKSPACE_PRUNE_DIRS:
        candidate = workspace / dirname
        if not candidate.exists():
            continue
        reclaimed_bytes += _directory_size_bytes(candidate)
        _remove_tree_with_retries(candidate)
        removed.append(dirname)
    return {
        "removed": removed,
        "reclaimed_bytes": reclaimed_bytes,
    }


def _workspace_changes_from_baseline(
    *,
    baseline_workspace: Path,
    run_workspace: Path,
    run_root_dir: Path,
) -> dict[str, Any]:
    if not baseline_workspace.exists():
        return {
            "added": [],
            "removed": [],
            "modified": [],
            "changed_files": [],
            "changed_file_count": 0,
            "artifact": None,
            "error": f"Missing baseline workspace: {baseline_workspace}",
        }

    diff = diff_directories(baseline_workspace, run_workspace)
    artifact_path = run_root_dir / "workspace-diff.json"
    artifact_path.write_text(
        json.dumps(
            {
                "baseline_workspace": str(baseline_workspace),
                "run_workspace": str(run_workspace),
                "added": diff.added,
                "removed": diff.removed,
                "modified": diff.modified,
                "changed_files": diff.changed_files,
                "changed_file_count": diff.count,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "added": diff.added,
        "removed": diff.removed,
        "modified": diff.modified,
        "changed_files": diff.changed_files,
        "changed_file_count": diff.count,
        "artifact": str(artifact_path),
        "error": None,
    }
