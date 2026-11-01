"""Snapshot inventory: entries whose bytes cannot be read faithfully invalidate the snapshot.

Unreadable, non-regular, non-UTF-8 and dangling entries are reported as invalid evidence
(the checkpoint becomes ineligible) while readable files keep their roles.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from raidar.scoring.framework import inventory as inventory_module
from raidar.scoring.framework.inventory import FileRole, build_inventory


def _issues(root: Path) -> dict[str, str]:
    return {item.path: item.detail for item in build_inventory(root).invalid}


def test_a_snapshot_root_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError, match="not a directory"):
        build_inventory(tmp_path / "missing")


def test_a_fifo_with_a_source_name_is_invalid_evidence(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "pipe.py")

    inventory = build_inventory(tmp_path)

    assert not inventory.eligible
    assert inventory.files[0].ignored_reason == "invalid_file"
    assert inventory.invalid[0].detail == "snapshot entry is not a regular file"


def test_a_symlink_to_a_fifo_is_invalid_evidence(tmp_path: Path) -> None:
    (tmp_path / "root").mkdir()
    os.mkfifo(tmp_path / "root" / "pipe")
    (tmp_path / "root" / "link.py").symlink_to("pipe")

    assert _issues(tmp_path / "root")["link.py"] == "symlink target is not a regular file"


def test_a_source_file_whose_bytes_cannot_be_read_is_invalid_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "locked.py").write_text("x = 1\n")
    original = Path.read_bytes

    def denied(path: Path) -> bytes:
        if path.name == "locked.py":
            raise PermissionError(13, "Permission denied", str(path))
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", denied)
    inventory = build_inventory(tmp_path)

    assert inventory.files[0].ignored_reason == "unreadable"
    assert inventory.invalid[0].detail == "unreadable: Permission denied"


def test_a_file_that_vanishes_between_listing_and_reading_is_invalid_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def listing(root: Path, **_: object) -> Iterator[tuple[str, list[str], list[str]]]:
        yield str(root), [], ["gone.py"]

    monkeypatch.setattr(inventory_module.os, "walk", listing)

    assert _issues(tmp_path) == {"gone.py": "unreadable: No such file or directory"}


def test_paths_that_are_not_utf8_are_invalid_evidence_and_excluded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def listing(root: Path, **_: object) -> Iterator[tuple[str, list[str], list[str]]]:
        yield str(root), ["\udcffdir"], ["\udcff.py"]

    monkeypatch.setattr(inventory_module.os, "walk", listing)
    inventory = build_inventory(tmp_path)

    assert not inventory.eligible
    assert [item.detail for item in inventory.invalid] == ["path is not UTF-8"] * 2
    assert {item.reason for item in inventory.exclusions} == {"non_utf8_path"}
    assert inventory.files == ()


def test_conftest_and_dot_test_or_spec_files_are_tests(tmp_path: Path) -> None:
    for name in ("conftest.py", "widget.spec.ts", "widget.test.js", "widget.ts"):
        (tmp_path / name).write_text("export const a = 1\n")

    roles = {item.path: item.role for item in build_inventory(tmp_path).files}

    assert roles == {
        "conftest.py": FileRole.TEST,
        "widget.spec.ts": FileRole.TEST,
        "widget.test.js": FileRole.TEST,
        "widget.ts": FileRole.PRODUCTION,
    }
