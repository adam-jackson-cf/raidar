"""Real-git seam of ``check-python-fanout.py``; its import accounting is tested in the fast tier."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.quality


def _load_fanout_module():
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "checks" / "check-python-fanout.py"
    )
    spec = importlib.util.spec_from_file_location("check_python_fanout", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repo_root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo_root, check=True, timeout=120)


def test_discovers_unignored_python_files_only(tmp_path: Path) -> None:
    fanout = _load_fanout_module()
    _git(tmp_path, "init")
    (tmp_path / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (tmp_path / "kept").mkdir()
    (tmp_path / "kept" / "module.py").write_text("", encoding="utf-8")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "module.py").write_text("", encoding="utf-8")

    discovered = {
        path.relative_to(tmp_path).as_posix() for path in fanout.discover_python_files(tmp_path)
    }

    assert discovered == {"kept/module.py"}
