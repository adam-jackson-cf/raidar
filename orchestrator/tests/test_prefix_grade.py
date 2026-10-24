"""Offline grading of guardrail-gate@3's pre-fix snapshots beside the final step snapshot.

A graded trial whose step has a pre-fix snapshot (recorded in its gate record
with its tree digest) gets that snapshot graded with the step's own bundle,
sandbox and timeout into the Assessment store, never the job; a stored grade is
not graded again; a changed snapshot is an error, never a grade; the regrade
compares a fresh grade with the stored one. ``raidar assess --rescore`` grades
first only when a scorer that reads the grades (gate-effort@10) runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from raidar.contracts.snapshot import tree_digest
from raidar.grading import offline_grading
from raidar.grading.prefix_grade import (
    grade_pre_fix_path,
    pre_fix_snapshots,
    regrade_pre_fix_path,
)
from raidar.grading.prefix_store import read_pre_fix_grade
from raidar.grading.records import load_step_record, step_record_dir
from raidar.grading.regrade import RegradeOptions
from tests.scorer_support import tree_state
from tests.test_verifier_and_regrade import (
    FakeAgentEnvironment,
    local_factory,
    make_verifier,
    place_step_artifact,
)
from tests.verifier_support import make_bundle, write_app

STEP = "checkpoint_2"


async def _graded_trial(tmp_path: Path) -> Path:
    """checkpoint_2 graded on its final tree (mul present), with a pre-fix tree without mul."""
    bundle = make_bundle(tmp_path / "fixture")
    place_step_artifact(tmp_path, STEP, mul=True)
    verifier = await make_verifier(
        tmp_path, FakeAgentEnvironment(), step=STEP, bundle_root=bundle.root
    )
    await verifier.verify()
    trial = tmp_path / "trial"
    agent = trial / "steps" / STEP / "agent"
    snapshot = write_app(agent / "guardrail-gate" / "pre_fix_1" / "app", mul=False)
    record = {
        "pre_fix_snapshots": [
            {
                "round": 1,
                "before_session": "stage_2_fix",
                "path": "guardrail-gate/pre_fix_1/app",
                "tree_digest": tree_digest(snapshot),
            }
        ]
    }
    (agent / "guardrail-gate" / "guardrail-gate.json").write_text(json.dumps(record))
    return trial


def _options(tmp_path: Path) -> RegradeOptions:
    return RegradeOptions(docker_context="test-ctx", sandbox_factory=local_factory(tmp_path))


async def test_a_pre_fix_snapshot_is_graded_into_the_store_never_the_job(tmp_path: Path) -> None:
    trial = await _graded_trial(tmp_path)
    store = tmp_path / "store"
    before = tree_state(trial)

    report = await grade_pre_fix_path(trial, store, _options(tmp_path))

    assert tree_state(trial) == before, "the job is never written"
    ((entry),) = report["snapshots"]
    assert (entry["status"], entry["step"], report["errors"]) == ("graded", STEP, 0)
    (snapshot,) = pre_fix_snapshots(trial)
    final = load_step_record(step_record_dir(trial, STEP))
    grade = read_pre_fix_grade(store, final.bundle.digest, STEP, snapshot.digest)
    assert grade is not None
    final_rate, pre_fix_rate = final.grade.pass_rate(), grade.pass_rate()
    assert final_rate is not None
    assert pre_fix_rate is not None
    assert pre_fix_rate < final_rate, "the pre-fix tree lacked what the step's tests need"

    again = await grade_pre_fix_path(trial, store, _options(tmp_path))
    assert again["snapshots"][0]["status"] == "stored"
    regrade = await regrade_pre_fix_path(trial, store, _options(tmp_path))
    assert regrade["match"] is True


async def test_a_changed_snapshot_is_an_error_never_a_grade(tmp_path: Path) -> None:
    trial = await _graded_trial(tmp_path)
    (snapshot,) = pre_fix_snapshots(trial)
    (snapshot.path / "app.py").write_text("print('edited after the fact')\n")

    report = await grade_pre_fix_path(trial, tmp_path / "store", _options(tmp_path))

    assert report["errors"] == 1
    assert "changed since it was recorded" in report["snapshots"][0]["error"]
    assert not (tmp_path / "store").exists()


async def test_a_trial_without_pre_fix_snapshots_grades_nothing(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path / "fixture")
    place_step_artifact(tmp_path, "checkpoint_1")
    verifier = await make_verifier(tmp_path, FakeAgentEnvironment(), bundle_root=bundle.root)
    await verifier.verify()

    report = await grade_pre_fix_path(tmp_path / "trial", tmp_path / "store", _options(tmp_path))

    assert report == {"kind": "raidar-pre-fix-grading", "snapshots": [], "errors": 0}


@pytest.mark.parametrize(
    ("scorers", "replay", "grades"),
    [
        (("gate-effort@10",), False, True),
        (("gate-effort@10", "correctness@1"), True, False),
        (("correctness@1", "code-quality@3"), False, False),
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

    monkeypatch.setattr(offline_grading, "grade_pre_fix_path", fake)
    options = RegradeOptions(docker_context="orbstack")

    result = offline_grading.grade_pre_fix(
        tmp_path / "job", tmp_path / "store", scorers, options, replay=replay, concurrency=3
    )

    assert (result is not None) is grades
    assert called == ([(tmp_path / "job", tmp_path / "store", "orbstack", 3)] if grades else [])
