"""Check CLI, dependency lock and release notes against project metadata."""

from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = REPO_ROOT / "orchestrator" / "pyproject.toml"
CLI_PATH = REPO_ROOT / "orchestrator" / "src" / "raidar" / "cli" / "__init__.py"
RELEASE_HEADING = re.compile(r"(?m)^## (\d+\.\d+\.\d+)(?:[^\n]*)$")


def check_cli_version_wiring(cli_text: str) -> str | None:
    try:
        module = ast.parse(cli_text)
        main_fn = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        decorator = next(
            node
            for node in main_fn.decorator_list
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "click"
            and node.func.attr == "version_option"
        )
        keywords = {keyword.arg: keyword.value for keyword in decorator.keywords}
        package = keywords.get("package_name")
        if (
            not decorator.args
            and "version" not in keywords
            and isinstance(package, ast.Constant)
            and package.value == "raidar"
        ):
            return None
    except (SyntaxError, StopIteration):
        pass
    return 'CLI version wiring: use click.version_option(package_name="raidar") without a version value.'


def check_lock_version(pyproject_text: str, lock_text: str) -> str | None:
    try:
        project_version = tomllib.loads(pyproject_text)["project"]["version"]
        packages = [
            package
            for package in tomllib.loads(lock_text).get("package", [])
            if package.get("name") == "raidar"
            and package.get("source") == {"editable": "."}
        ]
        if len(packages) == 1 and packages[0].get("version") == project_version:
            return None
    except (tomllib.TOMLDecodeError, KeyError):
        pass
    return "uv.lock version mismatch: run uv lock --project orchestrator to align the editable raidar version."


def check_changelog_version(pyproject_text: str, changelog_text: str) -> str | None:
    try:
        heading = RELEASE_HEADING.search(changelog_text)
        if (
            heading
            and heading.group(1) == tomllib.loads(pyproject_text)["project"]["version"]
        ):
            return None
    except (tomllib.TOMLDecodeError, KeyError):
        pass
    return "CHANGELOG version mismatch: regenerate the release entry to align it with [project].version."


def check_release_entry(
    head_subject: str, changelog_text: str, expected_entry: str
) -> str | None:
    if not head_subject.startswith("chore(release):"):
        return None
    headings = list(RELEASE_HEADING.finditer(changelog_text))
    if headings:
        end = headings[1].start() if len(headings) > 1 else len(changelog_text)
        if changelog_text[headings[0].start() : end].strip() == expected_entry.strip():
            return None
    return "CHANGELOG release entry differs from parent history: regenerate the release from current origin/main."


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _expected_release_entry() -> str:
    spec = importlib.util.spec_from_file_location(
        "bump_version", Path(__file__).with_name("bump-version.py")
    )
    if spec is None or spec.loader is None:
        raise ValueError(
            "Release generator unavailable; restore scripts/release/bump-version.py."
        )
    bump = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bump)
    parent_project = tomllib.loads(_git("show", "HEAD^:orchestrator/pyproject.toml"))
    commits = bump.get_commits_since_last_bump("HEAD^")
    bump_type, categorized = bump.analyze_commits(commits)
    if bump_type == "none":
        raise ValueError(
            "Release has no bump-worthy commits; use an ordinary non-release commit."
        )
    version = bump.calculate_new_version(
        parent_project["project"]["version"], bump_type
    )
    return str(
        bump.generate_changelog_entry(
            version,
            categorized,
            bump_type,
            date=_git("show", "-s", "--format=%cs", "HEAD^"),
        )
    )


def main() -> int:
    try:
        project = PYPROJECT_PATH.read_text(encoding="utf-8")
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        checks = [
            check_cli_version_wiring(CLI_PATH.read_text(encoding="utf-8")),
            check_lock_version(
                project, PYPROJECT_PATH.with_name("uv.lock").read_text(encoding="utf-8")
            ),
            check_changelog_version(project, changelog),
        ]
        subject = _git("show", "-s", "--format=%s", "HEAD")
        if subject.startswith("chore(release):"):
            checks.append(
                check_release_entry(subject, changelog, _expected_release_entry())
            )
        for diagnostic in checks:
            if diagnostic is not None:
                print(diagnostic)
                return 1
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(
            f"Version check failed: {' '.join(str(error).splitlines())}; restore release metadata and rerun make version-check."
        )
        return 1
    print("Version checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
