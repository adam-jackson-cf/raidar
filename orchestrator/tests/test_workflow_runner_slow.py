"""Session integrity across filesystem timestamp changes."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from raidar.subject.workflow.runner import SessionWatch

pytestmark = pytest.mark.slow


def test_a_session_log_only_appended_to_is_clean_and_a_write_then_restore_is_seen(
    tmp_path: Path,
) -> None:
    source, staged = tmp_path / "source", tmp_path / "staged"
    (source / "sessions").mkdir(parents=True)
    (staged / "sessions").mkdir(parents=True)
    original = b'{"a": 1}\n'
    watch = SessionWatch()

    def remote() -> dict[str, int]:  # what the container reports for the file's ctime
        return {"sessions/r.jsonl": (source / "sessions" / "r.jsonl").stat().st_ctime_ns}

    def keep(text: bytes, *, grew: bool = True) -> list[dict[str, str]]:
        (source / "sessions" / "r.jsonl").write_bytes(text)
        (staged / "sessions" / "r.jsonl").write_bytes(text)
        return watch.observe(staged, remote(), ("sessions",), grew_expected=grew)

    assert keep(original) == []
    assert keep(original + b'{"a": 2}\n') == []  # appended
    appended = original + b'{"a": 2}\n'
    time.sleep(0.05)  # a file system's ctime is coarse
    (source / "sessions" / "r.jsonl").write_bytes(b"tampered\n")  # a write ...
    (source / "sessions" / "r.jsonl").write_bytes(appended)  # ... and a restore
    (staged / "sessions" / "r.jsonl").write_bytes(appended)
    found = watch.observe(staged, remote(), ("sessions",), grew_expected=True)
    assert [item["name"] for item in found] == ["session_log_touched"]
    (staged / "sessions" / "r.jsonl").unlink()
    removed = watch.observe(staged, remote(), ("sessions",), grew_expected=True)
    assert [item["name"] for item in removed] == ["session_log_removed"]
