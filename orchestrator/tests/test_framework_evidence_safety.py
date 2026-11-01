"""Invalid filesystem evidence must be rejected without blocking or disappearing."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path

import pytest

from raidar.scoring.framework.inventory import build_inventory


def test_unreadable_directories_invalidate_inventory_and_keep_readable_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hidden = tmp_path / "unreadable"
    hidden.mkdir()
    (hidden / "source.py").write_text("hidden = True\n")
    (tmp_path / "visible.py").write_text("visible = True\n")
    original = os.scandir

    def denied(path: str | os.PathLike[str]) -> AbstractContextManager[Iterator[os.DirEntry[str]]]:
        if Path(path) == hidden:
            raise PermissionError(13, "Permission denied", str(path))
        return original(path)

    monkeypatch.setattr(os, "scandir", denied)
    inventory = build_inventory(tmp_path)
    assert not inventory.eligible
    assert [item.path for item in inventory.files] == ["visible.py"]
    assert [(item.path, item.code) for item in inventory.invalid] == [
        ("unreadable", "score_evidence_invalid")
    ]
    assert "directory unreadable" in inventory.invalid[0].detail
