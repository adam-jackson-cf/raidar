#!/usr/bin/env python3
"""
Version bump and changelog generator for typescript-ui-eval.

Analyzes conventional commits to determine version bump type:
- feat: -> minor bump (0.1.1 -> 0.2.0)
- fix: -> patch bump (0.1.1 -> 0.1.2)
- perf: -> patch bump (0.1.1 -> 0.1.2)
- BREAKING CHANGE -> major bump (0.1.1 -> 1.0.0)
- chore:, docs:, refactor:, test: -> no bump
"""

from __future__ import annotations

import argparse
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "orchestrator" / "pyproject.toml"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"

VERSION_PATTERN = re.compile(r'^version\s*=\s*"(\d+\.\d+\.\d+)"', re.MULTILINE)
COMMIT_TYPE_PATTERN = re.compile(r"^(\w+)(?:\(.+\))?:\s*(.+)$")

BUMP_TYPES = {
    "feat": "minor",
    "fix": "patch",
    "perf": "patch",
}


def get_current_version() -> str:
    """Read current version from orchestrator pyproject.toml."""
    content = PYPROJECT_PATH.read_text(encoding="utf-8")
    match = VERSION_PATTERN.search(content)
    if not match:
        raise ValueError(f"Could not find version in {PYPROJECT_PATH}")
    return match.group(1)


def parse_version(version: str) -> tuple[int, int, int]:
    parts = version.split(".")
    return int(parts[0]), int(parts[1]), int(parts[2])


def format_version(major: int, minor: int, patch: int) -> str:
    return f"{major}.{minor}.{patch}"


def get_commits_since_last_bump() -> list[str]:
    """Get commit subjects since the last release commit."""
    result = subprocess.run(
        ["git", "log", "--oneline", "--format=%s", "--no-merges", "-100"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
    )
    commits = [line for line in result.stdout.strip().split("\n") if line]

    filtered: list[str] = []
    for commit in commits:
        if commit.startswith("chore: bump version") or commit.startswith("chore(release):"):
            break
        filtered.append(commit)
    return filtered


def analyze_commits(commits: list[str]) -> tuple[str, list[dict[str, str]]]:
    """Determine bump type and return categorized commit records."""
    bump_type = "none"
    categorized: list[dict[str, str]] = []

    for commit in commits:
        if "BREAKING CHANGE" in commit.upper():
            bump_type = "major"

        match = COMMIT_TYPE_PATTERN.match(commit)
        if match:
            commit_type = match.group(1).lower()
            categorized.append({"type": commit_type, "raw": commit})

            if commit_type in BUMP_TYPES:
                candidate = BUMP_TYPES[commit_type]
                if bump_type == "none":
                    bump_type = candidate
                elif bump_type == "patch" and candidate == "minor":
                    bump_type = "minor"
        else:
            categorized.append({"type": "other", "raw": commit})

    return bump_type, categorized


def calculate_new_version(current: str, bump_type: str) -> str:
    major, minor, patch = parse_version(current)
    if bump_type == "major":
        return format_version(major + 1, 0, 0)
    if bump_type == "minor":
        return format_version(major, minor + 1, 0)
    if bump_type == "patch":
        return format_version(major, minor, patch + 1)
    return current


def update_pyproject(new_version: str) -> None:
    content = PYPROJECT_PATH.read_text(encoding="utf-8")
    updated = VERSION_PATTERN.sub(f'version = "{new_version}"', content)
    PYPROJECT_PATH.write_text(updated, encoding="utf-8")


def generate_changelog_entry(version: str, commits: list[dict[str, str]]) -> str:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    lines = [f"## [{version}] - {today}", ""]

    type_order = [
        "feat",
        "fix",
        "perf",
        "refactor",
        "docs",
        "ci",
        "build",
        "style",
        "chore",
        "test",
        "other",
    ]
    by_type: dict[str, list[str]] = {}

    for commit in commits:
        key = commit["type"]
        by_type.setdefault(key, []).append(commit["raw"])

    for kind in type_order:
        for raw in by_type.get(kind, []):
            lines.append(f"- {raw}")

    for kind in sorted(set(by_type) - set(type_order)):
        for raw in by_type[kind]:
            lines.append(f"- {raw}")

    lines.append("")
    return "\n".join(lines)


def update_changelog(entry: str) -> None:
    content = CHANGELOG_PATH.read_text(encoding="utf-8")
    header_end = content.find("\n## ")
    if header_end == -1:
        header_end = content.find("\n\n") + 1
    updated = content[:header_end] + "\n" + entry + content[header_end:]
    CHANGELOG_PATH.write_text(updated, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bump version and update changelog")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes only")
    args = parser.parse_args()

    current_version = get_current_version()
    commits = get_commits_since_last_bump()

    if not commits:
        print("No new commits since last version bump")
        return 0

    bump_type, categorized = analyze_commits(commits)
    new_version = calculate_new_version(current_version, bump_type)

    if args.dry_run:
        print(f"Current version: {current_version}")
        print(f"Bump type: {bump_type}")
        print(f"New version: {new_version}")
        print("Commits:")
        for commit in categorized:
            print(f"- {commit['raw']}")
        return 0

    if bump_type != "none":
        update_pyproject(new_version)

    changelog_version = new_version if bump_type != "none" else current_version
    entry = generate_changelog_entry(changelog_version, categorized)
    update_changelog(entry)

    print(f"Bump type: {bump_type}")
    print(f"Version: {current_version} -> {new_version}")
    print(f"Updated {PYPROJECT_PATH}")
    print(f"Updated {CHANGELOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
