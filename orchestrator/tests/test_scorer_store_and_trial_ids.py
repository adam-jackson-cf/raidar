"""Assessment store write safety, trial review ids and ATIF session reading.

The store never leaves a temporary file behind, treats a lost write race for identical bytes
as already stored, and keeps a provisional copy when completing it fails. A trial's review id
comes from the nearest ``job.json`` above it, and every embedded ATIF session is read once.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from raidar.scoring.framework.atif import TrajectoryError, load_trajectory
from raidar.scoring.framework.rescore import RescoreError, qualified_trial_id
from raidar.scoring.framework.result import AssessResult, AssessStatus, Observation
from raidar.scoring.framework.store import (
    Assessment,
    AssessmentStore,
    PutOutcome,
    ScorerIdentity,
)
from tests.trace_trial_support import agent_step, delegate, session, trajectory

KEY = "ab" * 32
SHA = "cd" * 32


def _assessment(*, provisional: bool = False, raw: dict[str, str] | None = None) -> Assessment:
    observation = Observation(metric="m", subject="trajectory", status=AssessStatus.OK, value=1)
    return Assessment(
        key=KEY,
        scorer=ScorerIdentity(ref="s@1", manifest_digest="m", implementation_digest=None),
        config={},
        tool_lock={},
        inputs={},
        result=AssessResult(status=AssessStatus.OK, observations=(observation,)),
        raw_payloads=raw or {},
        provisional=provisional,
    )


def _files(root: Path) -> list[str]:
    return sorted(path.name for path in root.rglob("*") if path.is_file())


def test_raw_payload_names_must_be_safe_relative_names() -> None:
    with pytest.raises(ValidationError, match="raw payload names must match"):
        _assessment(raw={"../escape": SHA})


def test_an_empty_store_has_nothing_and_an_empty_log(tmp_path: Path) -> None:
    store = AssessmentStore(tmp_path)

    assert not store.has(KEY)
    assert store.read_log() == []

    assert store.put(_assessment()) is PutOutcome.ADDED
    assert store.has(KEY)


def test_losing_the_write_race_for_identical_bytes_is_not_a_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AssessmentStore(tmp_path)
    assessment = _assessment()
    real_link = os.link

    def other_writer_wins(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        real_link(source, target)
        raise FileExistsError(17, "File exists", str(target))

    monkeypatch.setattr(os, "link", other_writer_wins)

    assert store.put(assessment) is PutOutcome.IDENTICAL
    assert store.get(KEY) == assessment


def test_a_failed_supersede_keeps_the_provisional_assessment_and_leaves_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AssessmentStore(tmp_path)
    store.put(_assessment(provisional=True))

    def refuse(source: object, target: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError, match="No space left"):
        store.put(_assessment())

    assert store.get(KEY) == _assessment(provisional=True)
    assert not any(name.endswith(".tmp") for name in _files(tmp_path))


def test_a_failed_temporary_write_leaves_no_file(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        AssessmentStore._temporary(tmp_path, "text, not bytes")  # type: ignore[arg-type]

    assert _files(tmp_path) == []


def test_a_trial_directory_is_its_own_id_without_a_job_manifest(tmp_path: Path) -> None:
    trial = tmp_path / "run" / "trials" / "t1"
    trial.mkdir(parents=True)

    assert qualified_trial_id(trial) == "t1"


def test_a_trial_id_is_qualified_by_the_experiment_and_job_two_levels_up(tmp_path: Path) -> None:
    job = tmp_path / "job-a"
    (job / "trials" / "t1").mkdir(parents=True)
    (job / "job.json").write_text(json.dumps({"experiment": "exp 1", "job_id": "j1"}))

    assert qualified_trial_id(job / "trials" / "t1") == "exp-1.j1.t1"


def test_a_job_manifest_without_an_experiment_gives_job_slash_trial(tmp_path: Path) -> None:
    job = tmp_path / "job-a"
    (job / "t1").mkdir(parents=True)
    (job / "job.json").write_text("{}")

    assert qualified_trial_id(job / "t1") == "job-a/t1"


def test_an_unreadable_job_manifest_is_a_rescore_error(tmp_path: Path) -> None:
    job = tmp_path / "job-a"
    (job / "t1").mkdir(parents=True)
    (job / "job.json").write_text("{broken")

    with pytest.raises(RescoreError, match="cannot read"):
        qualified_trial_id(job / "t1")


def _load(tmp_path: Path, document: dict) -> list[tuple[str, str]]:
    (tmp_path / "trajectory.json").write_text(json.dumps(document))
    loaded = load_trajectory(tmp_path)
    assert loaded is not None
    return [(step.session, step.message) for step in loaded.steps]


def test_message_parts_are_joined_and_empty_parts_are_dropped(tmp_path: Path) -> None:
    parts = [{"type": "text", "text": text} for text in ("one", "", "two")]
    document = trajectory(agent_step(1, message=parts))  # type: ignore[arg-type]

    assert _load(tmp_path, document)[-1] == ("main", "one\ntwo")


def test_embedded_sessions_are_read_in_place_once_and_unreferenced_ones_last(
    tmp_path: Path,
) -> None:
    document = trajectory(
        {"source": "system", "message": "delegating", **delegate("a", "ghost", "a")},
        {
            "source": "system",
            "message": "by path",
            "observation": {
                "results": [{"subagent_trajectory_ref": [{"trajectory_path": "x.json"}]}]
            },
        },
        subagents=(
            session("a", agent_step(1, message="inside a")),
            session("orphan", agent_step(1, message="inside orphan")),
        ),
    )

    steps = _load(tmp_path, document)
    sessions = [(name, text) for name, text in steps if text.startswith("inside")]

    assert sessions == [("a", "inside a"), ("orphan", "inside orphan")]


def test_a_trajectory_that_is_not_atif_is_refused(tmp_path: Path) -> None:
    (tmp_path / "trajectory.json").write_text('{"steps": 1}')

    with pytest.raises(TrajectoryError, match="not an ATIF trajectory"):
        load_trajectory(tmp_path)
