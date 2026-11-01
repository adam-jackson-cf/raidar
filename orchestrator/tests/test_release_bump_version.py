"""Tests for release version bump behavior."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "release" / "bump-version.py"

pytestmark = pytest.mark.quality


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


def test_update_pyproject_only_rewrites_project_version(
    bump_version_module,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "\n".join(
            [
                "[project]",
                'name = "sample"',
                'version = "1.2.3"',
                "",
                "[tool.sample]",
                'version = "9.9.9"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bump_version_module, "PYPROJECT_PATH", pyproject)

    bump_version_module.update_pyproject("2.0.0")

    assert pyproject.read_text(encoding="utf-8") == (
        '[project]\nname = "sample"\nversion = "2.0.0"\n\n[tool.sample]\nversion = "9.9.9"\n'
    )


def test_release_updates_and_verifies_the_lock_before_publishing_files(
    bump_version_module,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    lockfile = tmp_path / "uv.lock"
    changelog = tmp_path / "CHANGELOG.md"
    pyproject.write_text('[project]\nname = "sample"\nversion = "1.2.3"\n', encoding="utf-8")
    changelog.write_text("# Changelog\n", encoding="utf-8")
    monkeypatch.setenv("UV_OFFLINE", "true")
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    subprocess.run(["uv", "lock", "--project", str(tmp_path)], check=True)
    monkeypatch.setattr(bump_version_module, "PYPROJECT_PATH", pyproject)
    monkeypatch.setattr(bump_version_module, "LOCKFILE_PATH", lockfile)
    monkeypatch.setattr(bump_version_module, "CHANGELOG_PATH", changelog)
    monkeypatch.setattr(bump_version_module, "get_commits_since_last_bump", lambda: ["fix: repair"])
    monkeypatch.setattr(sys, "argv", [str(SCRIPT_PATH)])

    assert bump_version_module.main() == 0

    assert tomllib.loads(pyproject.read_text())["project"]["version"] == "1.2.4"
    packages = tomllib.loads(lockfile.read_text())["package"]
    assert (
        next(package for package in packages if package["name"] == "sample")["version"] == "1.2.4"
    )
    assert "## 1.2.4" in changelog.read_text()
    subprocess.run(["uv", "lock", "--project", str(tmp_path), "--check"], check=True)


def test_failed_lock_verification_leaves_all_release_files_unchanged(
    bump_version_module,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    lockfile = tmp_path / "uv.lock"
    changelog = tmp_path / "CHANGELOG.md"
    originals = {
        pyproject: '[project]\nname = "sample"\nversion = "1.2.3"\n',
        lockfile: "old lock\n",
        changelog: "# Changelog\n",
    }
    for path, contents in originals.items():
        path.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(bump_version_module, "PYPROJECT_PATH", pyproject)
    monkeypatch.setattr(bump_version_module, "LOCKFILE_PATH", lockfile)
    monkeypatch.setattr(bump_version_module, "CHANGELOG_PATH", changelog)
    monkeypatch.setattr(bump_version_module, "get_commits_since_last_bump", lambda: ["feat: add"])
    monkeypatch.setattr(sys, "argv", [str(SCRIPT_PATH)])
    commands = []

    def resolve_then_fail_verification(command, **_kwargs):
        commands.append(command)
        staging = Path(command[command.index("--project") + 1])
        if "--check" in command:
            raise subprocess.CalledProcessError(1, command)
        (staging / "uv.lock").write_text("resolved lock\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(bump_version_module.subprocess, "run", resolve_then_fail_verification)

    with pytest.raises(subprocess.CalledProcessError):
        bump_version_module.main()

    assert len(commands) == 2
    assert all(path.read_text(encoding="utf-8") == contents for path, contents in originals.items())
