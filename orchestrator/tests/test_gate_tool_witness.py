"""The gate tool names a watched state file written, restored or changed during a gate run."""

from __future__ import annotations

from typing import Any

from raidar.subject.primitive.gate_tool import witness_findings


def seen(sha: str, ctime: int) -> dict[str, Any]:
    return {"files": {"state.json": {"sha256": sha, "ctime_ns": ctime}}}


def test_a_watched_state_file_written_and_restored_or_changed_during_a_gate_run_is_named() -> None:
    same = witness_findings("define", seen("a", 1), seen("a", 1), set(), "w")
    touched = witness_findings("define", seen("a", 1), seen("a", 2), set(), "w")
    changed = witness_findings("define", seen("a", 1), seen("b", 2), set(), "w")
    added = witness_findings("define", {"files": {}}, seen("a", 1), set(), "w")
    allowed = witness_findings("define", seen("a", 1), seen("b", 2), {"state.json"}, "w")

    assert same == []
    assert allowed == []
    assert [f["name"] for f in touched] == ["define_state_touched"]
    assert [f["name"] for f in changed] == ["define_state_rewritten"]
    assert [f["name"] for f in added] == ["define_state_rewritten"]
