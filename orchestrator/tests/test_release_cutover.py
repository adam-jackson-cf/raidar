"""Release decisions and version consistency, without git or process boundaries."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from tests.experiment_spec_support import (
    experiment_document,
    lock_document,
    pin_spec_digest,
    write_experiment,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = '[project]\nname = "raidar"\nversion = "1.2.3"\n'
LOCK = """version = 1
[[package]]
name = "unrelated"
version = "9.8.7"
source = { registry = "https://example.invalid/simple" }
[[package]]
name = "raidar"
version = "1.2.3"
source = { editable = "." }
"""
CLI = """import click
@click.command()
@click.version_option(package_name="raidar")
def main():
    pass
"""
ENTRY = "## 1.2.3 - 2026-11-09\n\n### Bug Fixes\n\n- fix: repair cache\n\n"
OLDER_ENTRY = "## 1.2.2 - 2026-11-01\n\n- fix: older repair\n"
CHANGELOG = "# Changelog\n\n" + ENTRY + OLDER_ENTRY


def _load_script(name: str) -> ModuleType:
    path = REPO_ROOT / "scripts" / "release" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bump = _load_script("bump-version")
version_check = _load_script("verify-version-wiring")


def _assert_actionable_line(diagnostic: str | None, seam: str) -> None:
    assert diagnostic is not None
    assert len(diagnostic.splitlines()) == 1
    assert seam.lower() in diagnostic.lower()
    # Accept repair prose without pinning a particular diagnostic sentence.
    assert any(
        action in diagnostic.lower()
        for action in ("run", "regenerat", "update", "set", "use", "restore", "align", "replace")
    )


def test_version_check_accepts_consistent_inputs() -> None:
    assert version_check.check_cli_version_wiring(CLI) is None
    assert version_check.check_lock_version(PYPROJECT, LOCK) is None
    assert version_check.check_changelog_version(PYPROJECT, CHANGELOG) is None
    assert version_check.check_release_entry("chore(release): 1.2.3", CHANGELOG, ENTRY) is None


def test_version_check_refuses_hardcoded_cli_version() -> None:
    diagnostic = version_check.check_cli_version_wiring(
        CLI.replace('package_name="raidar"', 'version="1.2.3"')
    )

    _assert_actionable_line(diagnostic, "version")


def test_version_check_reports_editable_raidar_lock_mismatch() -> None:
    diagnostic = version_check.check_lock_version(
        PYPROJECT, LOCK.replace('version = "1.2.3"', 'version = "1.2.2"')
    )

    _assert_actionable_line(diagnostic, "uv.lock")


def test_version_check_reports_changelog_head_mismatch() -> None:
    diagnostic = version_check.check_changelog_version(
        PYPROJECT, CHANGELOG.replace("## 1.2.3", "## 1.2.4", 1)
    )

    _assert_actionable_line(diagnostic, "CHANGELOG")


def test_release_entry_check_detects_a_commit_omitted_after_rebase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bump, "datetime", SimpleNamespace(now=lambda _timezone: datetime(2026, 11, 9, tzinfo=UTC))
    )
    _, categorized = bump.analyze_commits(["fix: repair cache", "docs: explain repair"])
    expected_entry = bump.generate_changelog_entry("1.2.3", categorized, "patch")
    # Reuse the generator's date so this test concerns omitted commits, not today's date.
    incomplete_entry = expected_entry.replace("- docs: explain repair\n", "")

    diagnostic = version_check.check_release_entry(
        "chore(release): 1.2.3",
        "# Changelog\n\n" + incomplete_entry + OLDER_ENTRY,
        expected_entry,
    )

    _assert_actionable_line(diagnostic, "CHANGELOG")


def test_release_entry_check_does_not_compare_historical_entries() -> None:
    assert (
        version_check.check_release_entry(
            "chore(release): 1.2.3", "# Changelog\n\n" + ENTRY + "## 0.1.0\n\nolder\n", ENTRY
        )
        is None
    )


def test_ordinary_commit_does_not_require_a_regenerated_release_entry() -> None:
    assert (
        version_check.check_release_entry("docs: explain repair", CHANGELOG, "different output")
        is None
    )


def test_version_release_refuses_a_head_other_than_origin_main() -> None:
    diagnostic = bump.release_refusal("a" * 40, "b" * 40, ["fix: repair cache"])

    _assert_actionable_line(diagnostic, "origin/main")


@pytest.mark.parametrize(
    "commits",
    [[], ["docs: explain release"], ["ci: adjust gate", "test: cover gate", "refactor: simplify"]],
)
def test_version_release_refuses_histories_without_a_version_bump(commits: list[str]) -> None:
    diagnostic = bump.release_refusal("a" * 40, "a" * 40, commits)

    _assert_actionable_line(diagnostic, "release")


@pytest.mark.parametrize(
    "commit", ["fix: repair cache", "feat: add mode", "refactor!: change contract"]
)
def test_version_release_accepts_bump_commits_on_origin_main(commit: str) -> None:
    assert bump.release_refusal("a" * 40, "a" * 40, ["docs: explain release", commit]) is None


@pytest.mark.parametrize("version", ["1.2.4", "2.0.0"])
def test_release_subject_allows_ci_to_run(version: str) -> None:
    assert bump.release_commit_subject(version) == f"chore(release): {version}"


def test_removing_release_version_from_toolchain_preserves_execution_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin_spec_digest(monkeypatch)
    path = write_experiment(tmp_path, experiment_document())
    current = lock_document(path)
    with monkeypatch.context() as old_toolchain:
        old_toolchain.setattr(
            "raidar.design.experiment.lock.TOOLCHAIN_PACKAGES", ("litellm", "raidar")
        )
        previous = lock_document(path)

    assert "raidar" in previous["toolchain"]
    assert current["lock_sha256"] == previous["lock_sha256"]
    assert "litellm" in current["toolchain"]
    assert "raidar" not in current["toolchain"]
