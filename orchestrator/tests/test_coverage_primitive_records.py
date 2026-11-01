"""Offline coverage consumes portable, integrity-checked primitive evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from raidar.scoring.coverage_inputs.records import (
    Defined,
    NotDefined,
    Unreadable,
    read_build,
    read_define,
)


def _trace(root: Path) -> Path:
    trace = root / "agent"
    output = trace / "workflow/package/spec/criteria.json"
    output.parent.mkdir(parents=True)
    data = json.dumps(
        {
            "contract": "criteria/1",
            "features": [
                {
                    "id": "F1",
                    "name": "report",
                    "outcome": "usable report",
                    "criteria": [
                        {
                            "id": "F1.AC1",
                            "criterion": "Render the total",
                            "verification": "run a test",
                            "sources": ["The report must show a total."],
                            "kind": "new",
                            "aspects": [{"situation": "empty input", "outcome": "zero total"}],
                            "examples": [],
                        }
                    ],
                }
            ],
        }
    ).encode()
    output.write_bytes(data)
    stdout = json.dumps(
        {"tokens": {"UNITS": "U1*: The report must show a total.\nU2: Background context."}}
    )
    record = {
        "outputs/1": {
            "acme/spec": {
                "spec": {
                    "contract": "criteria/1",
                    "host_path": "workflow/package/spec/criteria.json",
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                    "error": None,
                }
            }
        },
        "setup/1": {
            "acme/spec": {
                "call": {
                    "stdout": stdout,
                    "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
                }
            }
        },
    }
    (trace / "workflow/workflow.json").write_text(json.dumps(record))
    return trace


def test_portable_evidence_survives_moving_the_trial(tmp_path: Path) -> None:
    trace = _trace(tmp_path)
    before = read_define(trace)
    assert isinstance(before, Defined)
    assert [unit.text for unit in before.units if unit.required] == [
        "The report must show a total."
    ]
    assert before.criteria is not None
    assert before.criteria[0].cites(before.units[0])
    moved = tmp_path / "moved"
    trace.rename(moved)
    assert read_define(moved) == before


def test_changed_criteria_bytes_are_not_scored(tmp_path: Path) -> None:
    trace = _trace(tmp_path)
    output = trace / "workflow/package/spec/criteria.json"
    output.write_bytes(output.read_bytes() + b" ")
    assert isinstance(read_define(trace), Unreadable)


def test_symlinked_criteria_are_not_opened(tmp_path: Path) -> None:
    trace = _trace(tmp_path)
    output = trace / "workflow/package/spec/criteria.json"
    outside = tmp_path / "outside.json"
    output.rename(outside)
    output.symlink_to(outside)
    assert isinstance(read_define(trace), Unreadable)


@pytest.mark.parametrize(
    "setup", [None, [], {"acme/spec": []}, {"acme/spec": {"call": {"stdout": "changed"}}}]
)
def test_missing_or_changed_unit_evidence_is_not_zero_coverage(
    tmp_path: Path, setup: object
) -> None:
    trace = _trace(tmp_path)
    path = trace / "workflow/workflow.json"
    record = json.loads(path.read_text())
    record["setup/1"] = setup
    path.write_text(json.dumps(record))
    assert isinstance(read_define(trace), Unreadable)


def test_workflows_without_criteria_are_not_applicable(tmp_path: Path) -> None:
    trace = _trace(tmp_path)
    path = trace / "workflow/workflow.json"
    path.write_text(json.dumps({"outputs/1": {}}))
    assert isinstance(read_define(trace), NotDefined)


@pytest.mark.parametrize(
    "stdout",
    [
        "{",
        "{}",
        '{"tokens": {"UNITS": 42}}',
        '{"tokens": {"UNITS": "U1*: first\\nU1: repeated"}}',
        '{"tokens": {"UNITS": "not a numbered unit"}}',
    ],
)
def test_digest_valid_but_malformed_units_are_not_scored(tmp_path: Path, stdout: str) -> None:
    trace = _trace(tmp_path)
    path = trace / "workflow/workflow.json"
    record = json.loads(path.read_text())
    record["setup/1"]["acme/spec"]["call"] = {
        "stdout": stdout,
        "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
    }
    path.write_text(json.dumps(record))
    assert isinstance(read_define(trace), Unreadable)


@pytest.mark.parametrize("data", [b"{", b'{"contract": "criteria/1", "wrong": []}'])
def test_a_valid_digest_does_not_make_invalid_criteria_scoreable(
    tmp_path: Path, data: bytes
) -> None:
    trace = _trace(tmp_path)
    (trace / "workflow/package/spec/criteria.json").write_bytes(data)
    path = trace / "workflow/workflow.json"
    record = json.loads(path.read_text())
    record["outputs/1"]["acme/spec"]["spec"].update(
        bytes=len(data), sha256=hashlib.sha256(data).hexdigest()
    )
    path.write_text(json.dumps(record))
    assert isinstance(read_define(trace), Unreadable)


@pytest.mark.parametrize("outputs", [None, [], {"acme/spec": []}])
def test_malformed_output_records_are_not_a_workflow_without_criteria(
    tmp_path: Path, outputs: object
) -> None:
    trace = _trace(tmp_path)
    path = trace / "workflow/workflow.json"
    record = json.loads(path.read_text())
    record["outputs/1"] = outputs
    path.write_text(json.dumps(record))
    assert isinstance(read_define(trace), Unreadable)


def test_two_criteria_producers_are_ambiguous_even_with_identical_outputs(tmp_path: Path) -> None:
    trace = _trace(tmp_path)
    path = trace / "workflow/workflow.json"
    record = json.loads(path.read_text())
    record["outputs/1"]["other/spec"] = record["outputs/1"]["acme/spec"]
    path.write_text(json.dumps(record))
    assert isinstance(read_define(trace), Unreadable)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("acceptance-reply/1", None),
        ("ac_tests", []),
        ("ac_tests", {"F1.AC1": "test_report.py::test_total"}),
        ("ac_tests", {"F1.AC1": [42]}),
        ("sources", None),
        ("sources", "../outside"),
    ],
)
def test_bad_reply_maps_and_escaping_sources_are_not_test_coverage(
    tmp_path: Path, field: str, value: object
) -> None:
    trace = _trace(tmp_path)
    (trace / "sources").mkdir()
    path = trace / "workflow/workflow.json"
    reply = {"ac_tests": {"F1.AC1": ["test_report.py::test_total"]}, "sources": "sources"}
    record = json.loads(path.read_text())
    record["acceptance-reply/1"] = reply
    if field == "acceptance-reply/1":
        record[field] = value
    else:
        reply[field] = value
    path.write_text(json.dumps(record))
    assert isinstance(read_build(trace), Unreadable)
