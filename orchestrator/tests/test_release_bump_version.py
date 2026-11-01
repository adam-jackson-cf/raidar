"""Real ``uv lock`` seam of the release bump; its commit and file decisions are fast-tier."""

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
