"""Repository state helpers for quality-gate checks."""

from __future__ import annotations

import subprocess
from pathlib import Path

import click

ARTIFACT_CHANGE_PREFIXES = ("experiments/",)


def repo_paths_from_git_cmd(args: list[str]) -> list[str]:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise click.ClickException(result.stderr.strip() or f"Command failed: {' '.join(args)}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def repo_name_status_from_git_cmd(args: list[str]) -> list[tuple[str, str]]:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise click.ClickException(result.stderr.strip() or f"Command failed: {' '.join(args)}")
    entries: list[tuple[str, str]] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        path = parts[-1]
        entries.append((status, path))
    return entries


def changed_repo_paths(repo_root: Path) -> list[str]:
    staged = repo_paths_from_git_cmd(
        ["git", "-C", str(repo_root), "diff", "--name-only", "--cached"]
    )
    unstaged = repo_paths_from_git_cmd(["git", "-C", str(repo_root), "diff", "--name-only"])
    untracked = repo_paths_from_git_cmd(
        ["git", "-C", str(repo_root), "ls-files", "--others", "--exclude-standard"]
    )
    return sorted(set(staged + unstaged + untracked))


def generated_artifact_paths(paths: list[str]) -> list[str]:
    return sorted(
        path
        for path in paths
        if any(path.startswith(prefix) for prefix in ARTIFACT_CHANGE_PREFIXES)
    )


def changed_repo_entries(repo_root: Path) -> list[tuple[str, str]]:
    staged = repo_name_status_from_git_cmd(
        ["git", "-C", str(repo_root), "diff", "--name-status", "--cached"]
    )
    unstaged = repo_name_status_from_git_cmd(["git", "-C", str(repo_root), "diff", "--name-status"])
    untracked = [
        ("??", path)
        for path in repo_paths_from_git_cmd(
            ["git", "-C", str(repo_root), "ls-files", "--others", "--exclude-standard"]
        )
    ]
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str]] = []
    for entry in staged + unstaged + untracked:
        if entry in seen:
            continue
        seen.add(entry)
        deduped.append(entry)
    return deduped


def assert_no_generated_artifact_changes(repo_root: Path) -> None:
    changed_entries = changed_repo_entries(repo_root)
    matches = [
        path
        for status, path in changed_entries
        if not status.startswith("D")
        and any(path.startswith(prefix) for prefix in ARTIFACT_CHANGE_PREFIXES)
    ]
    if not matches:
        return
    listed = "\n".join(f"- {path}" for path in matches)
    raise click.ClickException(
        "Generated Harbor artifacts must not be committed. Remove these changes:\n" + listed
    )


def has_unstaged_changes(repo_root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "diff", "--quiet"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode != 0
