"""Real-git seams of ``check-structure.py``; its pure layout rules are tested in the fast tier."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.quality


def _load_structure_module():
    script_path = REPO_ROOT / "scripts" / "checks" / "check-structure.py"
    spec = importlib.util.spec_from_file_location("check_structure", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


structure = _load_structure_module()


def test_repo_paths_list_tracked_and_unignored_files_but_not_ignored_ones(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text("/var/\n", encoding="utf-8")
    (tmp_path / "var").mkdir()
    (tmp_path / "var" / "jobs.json").write_text("{}", encoding="utf-8")
    (tmp_path / "stray").mkdir()
    (tmp_path / "stray" / "a.txt").write_text("", encoding="utf-8")

    assert structure.repo_paths(tmp_path) == [".gitignore", "stray/a.txt"]
    assert structure.violations(structure.repo_paths(tmp_path)) == [
        "unexpected top-level directory: stray/"
    ]


def test_main_exits_zero_on_a_clean_layout_and_one_on_a_violation(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "lab" / "judge").mkdir(parents=True)
    (tmp_path / "lab" / "judge" / "a.yaml").write_text("", encoding="utf-8")

    assert structure.main(["--repo-root", str(tmp_path)]) == 0

    (tmp_path / "lab" / "judge" / "thing_v2.yaml").write_text("", encoding="utf-8")

    assert structure.main(["--repo-root", str(tmp_path)]) == 1


def test_this_repository_has_no_legacy_top_level_directories() -> None:
    top_level = {path.split("/", 1)[0] for path in structure.repo_paths(REPO_ROOT) if "/" in path}

    assert top_level <= structure.ROOT_DIRECTORIES
