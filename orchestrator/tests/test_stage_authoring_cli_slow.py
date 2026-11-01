"""Authoring commands of ``stage/1``: the lock check reads a package's lock through uv."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from raidar.cli import stage_lock
from raidar.contract.paths import lab_root

pytestmark = pytest.mark.slow

REAL_PACKAGE = lab_root() / "stages" / "raidar" / "docs-check"
TEMPLATE_LOCK = (REAL_PACKAGE / "tool" / "uv.lock").read_text()
# Model uv's sdist metadata as well as the real package's pinned wheel.
SDIST = (
    f'sdist = {{ url = "https://files.pythonhosted.org/x.tar.gz", hash = "sha256:{"0" * 64}" }}\n'
)
LOCK = TEMPLATE_LOCK.replace("wheels = [", SDIST + "wheels = [")


def test_a_lock_with_an_sdist_is_a_lock_problem(tmp_path: Path) -> None:
    root = tmp_path / "acme" / "pkg"
    shutil.copytree(REAL_PACKAGE, root)
    (root / "tool" / "uv.lock").write_text(LOCK)

    problems = stage_lock.lock_problems(root)

    assert any("sdist" in problem for problem in problems)
