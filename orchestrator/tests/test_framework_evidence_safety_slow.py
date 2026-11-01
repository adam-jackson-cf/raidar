"""Special-file evidence must be refused without hanging a run."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("filename", ["source.py", "source.data"])
def test_special_files_invalidate_inventory_without_opening_them(
    tmp_path: Path, filename: str
) -> None:
    os.mkfifo(tmp_path / filename)
    # A subprocess gives a broken implementation a hard bound rather than hanging pytest.
    code = (
        "from pathlib import Path; "
        "from raidar.scoring.framework.inventory import build_inventory; "
        f"inventory = build_inventory(Path({str(tmp_path)!r})); "
        "assert not inventory.eligible; "
        "assert len(inventory.invalid) == 1; "
        "assert 'not a regular file' in inventory.invalid[0].detail"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=5)
