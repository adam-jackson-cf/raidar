"""Concurrent store writers must agree on the one complete Assessment."""

from __future__ import annotations

import json
import multiprocessing
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from raidar.scoring.framework.result import AssessResult, AssessStatus
from raidar.scoring.framework.store import (
    Assessment,
    AssessmentConflictError,
    AssessmentStore,
    ScorerIdentity,
)

pytestmark = pytest.mark.slow


def _promote(root: Path, value: int, writer: int, start: Path) -> None:
    store = AssessmentStore(root)
    provisional = store.get("a" * 64)
    assert provisional is not None
    complete = provisional.model_copy(
        update={
            "provisional": False,
            "result": AssessResult(
                status=AssessStatus.UNAVAILABLE, reason=str(value), observations=()
            ),
        }
    )
    original = AssessmentStore._supersede

    def delayed(self: AssessmentStore, path: Path, stored: bytes, data: bytes, key: str) -> None:
        # Enlarge the real read/replace race; the store must serialize this window.
        time.sleep(0.2)
        original(self, path, stored, data, key)

    (root / f"ready-{writer}").touch()
    deadline = time.monotonic() + 5
    while not start.exists():
        if time.monotonic() > deadline:
            raise TimeoutError("writers were not started")
        time.sleep(0.01)
    with patch.object(AssessmentStore, "_supersede", delayed):
        try:
            outcome = store.put(complete).value
        except AssessmentConflictError:
            outcome = "conflict"
    (root / f"result-{writer}.json").write_text(json.dumps({"outcome": outcome, "value": value}))


@pytest.mark.parametrize("values", [(1, 1), (1, 2)])
def test_competing_promotions_preserve_one_complete_result_and_detect_divergence(
    tmp_path: Path, values: tuple[int, int]
) -> None:
    store = AssessmentStore(tmp_path / "store")
    provisional = Assessment(
        key="a" * 64,
        scorer=ScorerIdentity(ref="test@1", manifest_digest="test", implementation_digest=None),
        config={},
        tool_lock={},
        inputs={},
        result=AssessResult(status=AssessStatus.UNAVAILABLE, reason="awaiting", observations=()),
        provisional=True,
    )
    store.put(provisional)
    start = store.root / "start"
    context = multiprocessing.get_context("spawn")
    writers = [
        context.Process(target=_promote, args=(store.root, value, writer, start))
        for writer, value in enumerate(values)
    ]
    try:
        for writer in writers:
            writer.start()
        deadline = time.monotonic() + 10
        while not all((store.root / f"ready-{writer}").exists() for writer in range(2)):
            assert time.monotonic() < deadline, "writers failed to become ready"
            time.sleep(0.01)
        start.touch()
        for writer in writers:
            writer.join(timeout=10)
            assert writer.exitcode == 0
    finally:
        for writer in writers:
            if writer.is_alive():
                writer.terminate()
                writer.join(timeout=5)
    results = [
        json.loads((store.root / f"result-{writer}.json").read_text()) for writer in range(2)
    ]
    expected = ["identical", "superseded"] if values[0] == values[1] else ["conflict", "superseded"]
    assert sorted(result["outcome"] for result in results) == expected
    winner = next(result for result in results if result["outcome"] == "superseded")
    complete = store.get(provisional.key)
    assert complete is not None
    assert not complete.provisional
    assert complete.result.reason == str(winner["value"])
    assert (
        store.superseded_path(provisional.key, provisional.render()).read_bytes()
        == provisional.render()
    )
