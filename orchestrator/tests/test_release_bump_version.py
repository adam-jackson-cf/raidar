"""Tests for release version bump behavior."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "release" / "bump-version.py"


@pytest.fixture(scope="module")
def bump_version_module():
    spec = importlib.util.spec_from_file_location("bump_version", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Could not load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "commit",
    [
        "feat!: change CLI contract",
        "feat(api)!: change CLI contract",
        "feat: change CLI contract\n\nBREAKING CHANGE: old flag removed",
    ],
)
def test_analyze_commits_treats_conventional_breaking_changes_as_major(
    bump_version_module,
    commit: str,
) -> None:
    bump_type, categorized = bump_version_module.analyze_commits([commit])

    assert bump_type == "major"
    assert categorized == [{"type": "feat", "raw": commit.splitlines()[0]}]


def test_get_commits_since_last_bump_keeps_bodies_and_stops_at_release(
    bump_version_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "feat: change CLI contract\n"
                "\n"
                "BREAKING CHANGE: old flag removed"
                "\x1e"
                "fix: repair cache"
                "\x1e"
                "chore(release): 0.11.1"
                "\x1e"
                "feat: older change"
                "\x1e"
            ),
        )

    monkeypatch.setattr(bump_version_module.subprocess, "run", fake_run)

    assert bump_version_module.get_commits_since_last_bump() == [
        "feat: change CLI contract\n\nBREAKING CHANGE: old flag removed",
        "fix: repair cache",
    ]
