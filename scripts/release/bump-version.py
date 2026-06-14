"""
Version bump and changelog generator for Raidar.

Bump type:
- feat: minor bump (0.1.1 -> 0.2.0)
- fix: patch bump (0.1.1 -> 0.1.2)
- perf: patch bump (0.1.1 -> 0.1.2)
- BREAKING CHANGE -> major bump (0.1.1 -> 1.0.0)
- chore:, docs:, refactor:, test: -> no bump
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = REPO_ROOT / "orchestrator" / "pyproject.toml"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"

VERSION_PATTERN = re.compile(r'(?m)^version = "([^"]+)"$')
PROJECT_TABLE_PATTERN = re.compile(
    r"(?ms)^\[project\]\n(?P<body>.*?)(?=^\[[^\n]+\]\n|\Z)"
)
COMMIT_TYPE_PATTERN = re.compile(r"^(\w+)(?:\([^)]*\))?(!)?:")
BREAKING_CHANGE_PATTERN = re.compile(r"(?im)^BREAKING[ -]CHANGE:")

BUMP_TYPES = {
    "feat": "minor",
    "fix": "patch",
    "perf": "patch",
}


def _project_block(content: str) -> re.Match[str]:
    match = PROJECT_TABLE_PATTERN.search(content)
    if match is None:
        raise ValueError(f"Could not find [project] table in {PYPROJECT_PATH}")
    return match


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
    ) as temp_file:
        temp_path = Path(temp_file.name)
        temp_file.write(content)
        temp_file.flush()
        os.fsync(temp_file.fileno())

    try:
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _atomic_write_many(updates: dict[Path, str]) -> None:
    temp_paths: dict[Path, Path] = {}
    try:
        for path, content in updates.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=path.parent,
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                temp_file.write(content)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            temp_paths[path] = temp_path

        for path, temp_path in temp_paths.items():
            os.replace(temp_path, path)
    finally:
        for temp_path in temp_paths.values():
            temp_path.unlink(missing_ok=True)


def get_current_version() -> str:
    """Read current version from orchestrator pyproject."""
    content = PYPROJECT_PATH.read_text(encoding="utf-8")
    project_block = _project_block(content).group("body")
    match = VERSION_PATTERN.search(project_block)
    if match is None:
        raise ValueError(f"Could not find [project].version in {PYPROJECT_PATH}")
    return match.group(1)


def parse_version(version: str) -> tuple[int, int, int]:
    parts = version.split(".")
    if len(parts) != 3:
        raise ValueError(f"Expected semantic version x.y.z, got: {version}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def format_version(major: int, minor: int, patch: int) -> str:
    return f"{major}.{minor}.{patch}"


def get_commits_since_last_bump() -> list[str]:
    """Get commit subjects and bodies since last version bump commit."""
    result = subprocess.run(
        ["git", "log", "--format=%s%n%b%x1e", "--no-merges", "-100"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
    )

    commits = [record.strip() for record in result.stdout.split("\x1e") if record.strip()]
    filtered: list[str] = []
    for commit in commits:
        subject = commit.splitlines()[0]
        if subject.startswith("chore: bump version") or subject.startswith(
            "chore(release):"
        ):
            break
        filtered.append(commit)

    return filtered


def analyze_commits(commits: list[str]) -> tuple[str, list[dict[str, str]]]:
    """Analyze commits to determine bump type and categorize changelog entries."""
    bump_type = "none"
    categorized: list[dict[str, str]] = []

    for commit in commits:
        subject = commit.splitlines()[0]
        if BREAKING_CHANGE_PATTERN.search(commit):
            bump_type = "major"

        match = COMMIT_TYPE_PATTERN.match(subject)
        if match:
            commit_type = match.group(1).lower()
            categorized.append({"type": commit_type, "raw": subject})

            if match.group(2):
                bump_type = "major"
            elif bump_type != "major" and commit_type in BUMP_TYPES:
                candidate = BUMP_TYPES[commit_type]
                if bump_type == "none":
                    bump_type = candidate
                elif bump_type == "patch" and candidate == "minor":
                    bump_type = "minor"
        else:
            categorized.append({"type": "other", "raw": subject})

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


def render_pyproject_update(new_version: str) -> str:
    content = PYPROJECT_PATH.read_text(encoding="utf-8")
    project_match = _project_block(content)
    project_block = project_match.group("body")
    updated_block, replacements = VERSION_PATTERN.subn(
        f'version = "{new_version}"', project_block, count=1
    )
    if replacements != 1:
        raise ValueError(f"Could not replace exactly one [project].version in {PYPROJECT_PATH}")
    return content[: project_match.start("body")] + updated_block + content[project_match.end("body") :]


def update_pyproject(new_version: str) -> None:
    _atomic_write_text(PYPROJECT_PATH, render_pyproject_update(new_version))
    print(f"Updated pyproject.toml: {new_version}")


def generate_changelog_entry(
    version: str,
    commits: list[dict[str, str]],
    bump_type: str,
) -> str:
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    lines = [f"## {version} - {date}", ""]

    if bump_type == "major":
        lines.extend(["### Breaking Changes", ""])

    by_type: dict[str, list[str]] = {}
    for commit in commits:
        by_type.setdefault(commit["type"], []).append(commit["raw"])

    type_headers = {
        "feat": "Features",
        "fix": "Bug Fixes",
        "perf": "Performance",
        "docs": "Documentation",
        "refactor": "Refactoring",
        "test": "Tests",
        "chore": "Chores",
        "other": "Other Changes",
    }

    type_order = ["feat", "fix", "perf", "docs", "refactor", "test", "chore", "other"]
    for kind in type_order:
        if kind in by_type:
            lines.extend([f"### {type_headers[kind]}", ""])
            lines.extend(f"- {raw}" for raw in by_type[kind])
            lines.append("")

    for kind in sorted(set(by_type) - set(type_order)):
        lines.extend(f"- {raw}" for raw in by_type[kind])

    lines.append("")
    return "\n".join(lines)


def render_changelog_update(entry: str) -> str:
    content = CHANGELOG_PATH.read_text(encoding="utf-8")
    section_match = re.search(r"(?m)^## ", content)
    if section_match is not None:
        insertion_point = section_match.start()
        prefix = content[:insertion_point].rstrip()
        suffix = content[insertion_point:].lstrip("\n")
        return f"{prefix}\n\n{entry}\n{suffix}"

    title_match = re.search(r"(?m)^# .*(?:\n|$)", content)
    if title_match is not None:
        prefix = content[: title_match.end()].rstrip()
        suffix = content[title_match.end() :].strip()
        if suffix:
            return f"{prefix}\n\n{entry}\n{suffix}\n"
        return f"{prefix}\n\n{entry}"

    stripped = content.strip()
    if stripped:
        return f"{stripped}\n\n{entry}"
    return entry


def update_changelog(entry: str) -> None:
    _atomic_write_text(CHANGELOG_PATH, render_changelog_update(entry))
    print("Updated CHANGELOG.md")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bump version and update changelog")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = parser.parse_args()

    current_version = get_current_version()
    commits = get_commits_since_last_bump()

    if not commits:
        print("No commits since last version bump")
        return 0

    bump_type, categorized = analyze_commits(commits)
    new_version = calculate_new_version(current_version, bump_type)
    changelog_version = new_version if bump_type != "none" else current_version
    entry = generate_changelog_entry(changelog_version, categorized, bump_type)

    print(f"Current version: {current_version}")
    print(f"Bump type: {bump_type}")
    print(f"New version: {new_version}")
    print("\nChangelog entry:")
    print(entry)

    if args.dry_run:
        return 0

    updates = {CHANGELOG_PATH: render_changelog_update(entry)}
    if bump_type != "none":
        updates[PYPROJECT_PATH] = render_pyproject_update(new_version)

    _atomic_write_many(updates)
    if bump_type != "none":
        print(f"Updated pyproject.toml: {new_version}")
    print("Updated CHANGELOG.md")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
