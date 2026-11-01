"""Authoring commands of ``stage/1``: the lock check reads a package's lock through uv."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from raidar.cli import stage_lock
from tests.release_support import REAL_PACKAGE, UV_LOCK_WITH_SDIST

pytestmark = pytest.mark.slow


def test_a_lock_with_an_sdist_is_a_lock_problem(tmp_path: Path) -> None:
    root = tmp_path / "acme" / "pkg"
    shutil.copytree(REAL_PACKAGE, root)
    (root / "tool" / "uv.lock").write_text(UV_LOCK_WITH_SDIST)

    problems = stage_lock.lock_problems(root)

    assert any("sdist" in problem for problem in problems)
