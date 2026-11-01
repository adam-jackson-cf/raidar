"""Judge evidence must support closure; absence never implies a pass."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from raidar.engine.health.judge import judge_family


def _write(root: Path, path: str, value: Any) -> None:
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(value))


def _step(root: Path, probability: float | None = 0.9) -> dict[str, Any]:
    status = (
        "closed"
        if probability is not None and probability >= 0.8
        else "open"
        if probability is not None and probability <= 0.3
        else "undecided"
    )
    result = (
        {
            "ok": probability is not None,
            "answers": {"aspect_asserted": {"type": "noul", "noul": probability}},
        }
        if probability is not None
        else {"ok": False, "reason": "timeout"}
    )
    _write(root, "request.json", {"questions": {"aspect_asserted": {"type": "noul"}}})
    if probability is not None:
        _write(root, "response.json", {"answers": result["answers"]})
    _write(root, "result.json", result)
    aspect = {"id": "F1.AC1.A1", "probability": probability, "status": status, "calls": [0]}
    criterion = {
        "id": "F1.AC1",
        "kind": "behaviour",
        "status": status,
        "failing": [],
        "tests": ["test_a.py::test_a"],
        "test_results": {"test_a.py::test_a": "passed"},
        "aspects": [aspect],
    }
    passed = {
        "pass": 1,
        "gate_verdict": "passed",
        "criteria": [criterion],
        "closed": [],
        "open": [],
        "undecided": [],
    }
    passed[status] = ["F1.AC1"]
    verify = {
        "ending": "verified" if status == "closed" else "pass_limit",
        "judge": {"open_at": 0.3, "closed_at": 0.8, "regression_needs_baseline": True},
        "judge_calls": [
            {
                "index": 0,
                "aspect": aspect["id"],
                "request_file": "request.json",
                "response_file": "response.json" if probability is not None else None,
                "result_file": "result.json",
                "result": result,
                "probability": probability,
            }
        ],
        "judged": [passed],
    }
    _save(root, verify)
    return verify


def _save(root: Path, verify: dict[str, Any]) -> None:
    _write(root, "workflow/workflow.json", {"stages": ["build"], "jev-judge/1": verify})


def _checks(root: Path) -> dict[str, Any]:
    return {c.id: c for c in judge_family([("checkpoint_1", root)])}


@pytest.mark.parametrize("probability", [0.9, 0.8, 0.3, 0.1, 0.5, None])
def test_recorded_answers_support_closed_open_or_undecided_readings(
    tmp_path: Path, probability: float | None
) -> None:
    _step(tmp_path, probability)
    assert {c.status for c in _checks(tmp_path).values()} == {"pass"}
    if probability is None or probability == 0.5:
        assert _checks(tmp_path)["J3"].evidence["undecided"] == {"checkpoint_1": ["F1.AC1.A1"]}


def test_missing_raw_response_is_unmeasured(tmp_path: Path) -> None:
    _step(tmp_path)
    (tmp_path / "response.json").unlink()
    assert _checks(tmp_path)["J1"].status == "unmeasured"


def test_falsified_interpreted_answer_is_a_violation(tmp_path: Path) -> None:
    _step(tmp_path)
    _write(
        tmp_path, "response.json", {"answers": {"aspect_asserted": {"type": "noul", "noul": 0.1}}}
    )
    assert _checks(tmp_path)["J1"].status == "violation"


def test_closed_aspect_cannot_hide_an_open_answer(tmp_path: Path) -> None:
    verify = _step(tmp_path, 0.1)
    verify["judged"][0]["criteria"][0]["aspects"][0]["status"] = "closed"
    _save(tmp_path, verify)
    assert _checks(tmp_path)["J2"].status == "violation"


def test_an_undecided_criterion_cannot_end_verified(tmp_path: Path) -> None:
    verify = _step(tmp_path, None)
    verify["ending"] = "verified"
    _save(tmp_path, verify)
    assert _checks(tmp_path)["J3"].status == "violation"


def test_regression_closes_only_with_a_passing_mapped_baseline(tmp_path: Path) -> None:
    verify = _step(tmp_path)
    criterion = verify["judged"][0]["criteria"][0]
    criterion.update(kind="regression", aspects=[])
    _save(tmp_path, verify)
    assert _checks(tmp_path)["J2"].status == "violation"
    verify["baseline_passed"] = criterion["tests"]
    _save(tmp_path, verify)
    assert _checks(tmp_path)["J2"].status == "pass"


def test_an_unpassed_mapped_test_cannot_close_a_criterion(tmp_path: Path) -> None:
    verify = _step(tmp_path)
    verify["judged"][0]["criteria"][0]["test_results"] = {"test_a.py::test_a": "skipped"}
    _save(tmp_path, verify)
    assert _checks(tmp_path)["J2"].status == "violation"


def test_missing_pass_evidence_is_unmeasured(tmp_path: Path) -> None:
    verify = _step(tmp_path)
    verify.pop("judged")
    _save(tmp_path, verify)
    assert _checks(tmp_path)["J2"].status == "unmeasured"
    assert _checks(tmp_path)["J3"].status == "unmeasured"


def test_no_judge_means_no_j_family(tmp_path: Path) -> None:
    _write(tmp_path, "workflow/workflow.json", {"stages": ["build"]})
    assert judge_family([("checkpoint_1", tmp_path)]) == ()


def test_a_retained_attempt_cannot_disappear_from_the_judge_ledger(tmp_path: Path) -> None:
    _step(tmp_path)
    _write(tmp_path, "workflow/judge/w1/call-0001.result.json", {"ok": False})
    _write(tmp_path, "workflow/judge/w2/call-0001.result.json", {"ok": False})
    assert _checks(tmp_path)["J1"].status == "violation"


def test_a_retained_invalid_provider_body_is_evidence_not_a_missing_response(
    tmp_path: Path,
) -> None:
    verify = _step(tmp_path, None)
    call = verify["judge_calls"][0]
    call["response_file"] = "response.json"
    call["result"]["reason"] = "invalid_response"
    _write(tmp_path, "result.json", call["result"])
    (tmp_path / "response.json").write_text("not provider JSON")
    _save(tmp_path, verify)
    assert _checks(tmp_path)["J1"].status == "pass"


@pytest.mark.parametrize(
    "location",
    ["pass", "criteria", "aspects", "tests", "test_results"],
)
def test_malformed_retained_closure_is_unmeasured(tmp_path: Path, location: str) -> None:
    verify = _step(tmp_path)
    passed = verify["judged"][0]
    criterion = passed["criteria"][0]
    if location == "pass":
        verify["judged"] = [None]
    elif location == "criteria":
        passed["criteria"] = [None]
    elif location == "aspects":
        criterion["aspects"] = [None]
    else:
        criterion[location] = None
    _save(tmp_path, verify)
    checks = _checks(tmp_path)
    assert checks["J2"].status == "unmeasured"
    assert checks["J3"].status == "unmeasured"
