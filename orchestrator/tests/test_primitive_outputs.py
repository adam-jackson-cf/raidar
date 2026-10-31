"""``outputs/1`` contract: bounded host-side copy and hash, and ``criteria/1`` as data."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from raidar_contract.package.manifest import StagePackage
from raidar_contract.package.registry import load_stage_package

from raidar.subject.primitive.calls import TransportError
from raidar.subject.primitive.outputs import collect_outputs
from raidar.subject.primitive.site import TrialInvalid
from tests.lifecycle_support import NOTE, NOTE_STATE, WATCH, Rig, build_rig

REPORT = f"{WATCH}/report.json"
CRITERION = {
    "id": "c1",
    "criterion": "It answers",
    "verification": "ask",
    "sources": ["the task says so"],
    "kind": "new",
    "aspects": [{"situation": "asked", "outcome": "answered"}],
    "examples": [],
}
CRITERIA = {
    "contract": "criteria/1",
    "features": [{"id": "f1", "name": "F", "outcome": "O", "criteria": [CRITERION]}],
}


def with_report(package: StagePackage) -> StagePackage:
    outputs = {
        **package.manifest.outputs,
        "report": package.manifest.outputs["note"].model_copy(
            update={"path": REPORT, "contract": "criteria/1"}
        ),
    }
    return replace(package, manifest=package.manifest.model_copy(update={"outputs": outputs}))


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rig:
    return build_rig(tmp_path, monkeypatch, with_report(load_stage_package(NOTE_STATE)))


def write_report(rig: Rig, document: Any) -> None:
    path = rig.container.host(REPORT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


async def test_outputs_are_copied_hashed_and_validated_host_side(rig: Rig) -> None:
    rig.container.write_note("hello there world")
    write_report(rig, CRITERIA)

    result = await collect_outputs(rig.site, "close")

    assert result.problems == {}
    note = result.outputs["note"]
    assert note.sha256 == hashlib.sha256(b"hello there world").hexdigest()
    assert Path(note.host_path or "").read_text() == "hello there world"


async def test_a_missing_output_is_reported_not_raised(rig: Rig) -> None:
    rig.container.write_note()

    result = await collect_outputs(rig.site, "close")

    assert result.problems == {"report": "missing"}
    assert result.digests()["report"] is None


@pytest.mark.parametrize(
    "document",
    [
        {"contract": "criteria/1", "features": []},
        {**CRITERIA, "extra": 1},
        {"contract": "criteria/1", "features": [{"id": "f1", "name": "F", "outcome": "O"}]},
        {"contract": "criteria/1", "features": [CRITERIA["features"][0]] * 2},
        [],
        "text",
    ],
)
async def test_a_criteria_output_that_breaks_the_contract_is_a_problem(
    rig: Rig, document: Any
) -> None:
    rig.container.write_note()
    write_report(rig, document)

    result = await collect_outputs(rig.site, "close")

    assert "criteria/1" in result.problems["report"]


async def test_a_non_json_criteria_output_is_a_problem(rig: Rig) -> None:
    rig.container.write_note()
    report = rig.container.host(REPORT)
    report.write_bytes(b"\xff\xfe not json")

    result = await collect_outputs(rig.site, "close")

    assert "criteria/1" in result.problems["report"]


async def test_a_symlinked_or_directory_output_is_refused_without_following(rig: Rig) -> None:
    os.makedirs(rig.container.host(WATCH), exist_ok=True)
    os.symlink("/etc/passwd", rig.container.host(NOTE))
    rig.container.host(REPORT).mkdir()
    (rig.container.host(REPORT) / "a").write_text("x")
    (rig.container.host(REPORT) / "b").write_text("y")

    result = await collect_outputs(rig.site, "close")

    assert "symlink" in result.problems["note"]
    assert result.problems["report"] == "not a single regular file"
    assert result.outputs["note"].host_path is None


async def test_output_transport_failure_is_invalid_not_an_absent_output(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def disconnected(*args: Any, **kwargs: Any) -> Any:
        raise TransportError("container disconnected")

    monkeypatch.setattr("raidar.subject.primitive.outputs.snapshot_state", disconnected)

    with pytest.raises(TrialInvalid) as invalid:
        await collect_outputs(rig.site, "close")

    assert invalid.value.kind == "integrity"
