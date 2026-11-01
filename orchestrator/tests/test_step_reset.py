"""Per-step reset scripts and the per-run compose project of trials (F3).

Real bash runs live in ``test_step_reset_script.py`` and real Harbor trials in
``test_step_reset_docker.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from raidar.engine.health.setup_script import reset_check, write_setup_scripts


def test_reset_check_fails_when_the_reset_never_ran(tmp_path: Path) -> None:
    outcome = reset_check(tmp_path)

    assert not outcome.passed
    assert "setup.sh did not run" in outcome.detail


def test_reset_scripts_are_executable_and_refuse_reuse_or_an_empty_task(tmp_path: Path) -> None:
    write_setup_scripts(tmp_path, ["checkpoint_1", "checkpoint_2"])
    first, second = (
        tmp_path / "steps" / step / "workdir" / "setup.sh"
        for step in ("checkpoint_1", "checkpoint_2")
    )

    assert os.access(first, os.X_OK)
    assert os.access(second, os.X_OK)
    with pytest.raises(FileExistsError):
        write_setup_scripts(tmp_path, ["checkpoint_1"])
    with pytest.raises(ValueError, match="at least one step"):
        write_setup_scripts(tmp_path / "empty", [])
