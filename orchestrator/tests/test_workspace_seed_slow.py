"""Slow-tier behaviours split from :mod:`tests.test_workspace_seed`."""

from __future__ import annotations

import subprocess

import pytest

from raidar.engine.trial.workspace import (
    workspace_seed,
)
from tests.test_workspace_seed import (
    _LOCKS as _LOCKS,
)
from tests.test_workspace_seed import (
    CUTOFF as CUTOFF,
)

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("locked", [True, False])
def test_the_seed_install_script_is_valid_shell(locked: bool) -> None:
    seed = workspace_seed(
        "scb/adder", ["numpy"] if locked else [], "3.12", CUTOFF, _LOCKS if locked else {}
    )

    for script in (seed.install, seed.check):
        subprocess.run(["sh", "-n", "-c", script], check=True)
