"""Offline grading of the pre-fix trees gate/1 retained beside the final step snapshot.

A graded trial whose step has a retained pre-fix tree (recorded in its ``gate/1`` record with its
tree digest) gets that tree graded with the step's own bundle, sandbox and timeout into the
Assessment store, never the job; a stored grade is not graded again; a changed or escaping
tree is an error, never a grade; the regrade compares a fresh grade with the stored one.
``raidar assess --rescore`` grades first only when a scorer that reads the grades
(verification-effort@1) runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from raidar.grading import offline
from raidar.grading.regrade import RegradeOptions


@pytest.mark.parametrize(
    ("scorers", "replay", "grades"),
    [
        (("verification-effort@1",), False, True),
        (("verification-effort@1", "correctness@10"), True, False),
        (("correctness@10", "code-quality@9"), False, False),
    ],
)
def test_rescore_grades_first_only_for_a_scorer_that_reads_the_grades(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scorers: tuple[str, ...],
    replay: bool,
    grades: bool,
) -> None:
    called: list[Any] = []

    async def fake(
        path: Path, store: Path, options: RegradeOptions, concurrency: int = 1
    ) -> dict[str, Any]:
        called.append((path, store, options.docker_context, concurrency))
        return {"snapshots": [], "errors": 0}

    monkeypatch.setattr(offline, "grade_pre_fix_path", fake)
    options = RegradeOptions(docker_context="orbstack")

    result = offline.grade_pre_fix(
        tmp_path / "job", tmp_path / "store", scorers, options, replay=replay, concurrency=3
    )

    assert (result is not None) is grades
    assert called == ([(tmp_path / "job", tmp_path / "store", "orbstack", 3)] if grades else [])
