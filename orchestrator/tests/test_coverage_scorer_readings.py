"""Coverage scorers: readings that decide nothing, and request layout under a size budget.

A definition with no required unit or no criterion is not applicable, a unit no criterion cites
is counted and never asked, and an undecided test keeps its reason without a score. A test whose
helpers would overflow a request is sent without them.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from raidar.scoring import definition_coverage as definition
from raidar.scoring import test_coverage as tests_scorer
from raidar.scoring.coverage_inputs.records import Criterion, Defined, Unit
from raidar.scoring.coverage_inputs.testsource import ReadTest
from raidar.scoring.framework.result import AssessStatus
from raidar.scoring.test_coverage.questions import STATE_CHARS, requests
from tests.jev_support import RecordedTransport
from tests.test_coverage_scorers import (
    DEFINITION,
    SOURCE,
    STRONG,
    _assess,
    _jev,
    _readings,
    _step,
    _trace,
    _value,
)

MAPPED = {"F1.AC1": ["tests/test_tally.py::test_report_invocation"]}


def _rewrite(agent: Path, change: Any) -> None:
    path = agent / "workflow/workflow.json"
    record = json.loads(path.read_text())
    change(record)
    path.write_text(json.dumps(record))


def _units(agent: Path, lines: list[str]) -> None:
    def change(record: dict[str, Any]) -> None:
        stdout = json.dumps({"tokens": {"UNITS": "\n".join(lines)}})
        call = record["setup/1"]["acme/spec"]["call"]
        call["stdout"] = stdout
        call["stdout_sha256"] = hashlib.sha256(stdout.encode()).hexdigest()

    _rewrite(agent, change)


def _quotes() -> list[str]:
    return list(dict.fromkeys(SOURCE.findall(DEFINITION.read_text(encoding="utf-8"))))


def test_a_definition_without_a_required_unit_is_not_applicable(tmp_path: Path) -> None:
    agent = _trace(tmp_path)
    _units(agent, [f"U{number}: {quote}" for number, quote in enumerate(_quotes(), 1)])
    transport = RecordedTransport(answer_for=lambda _i, _q: STRONG)
    scorer = definition.definition_coverage_scorer(_jev(transport))

    result, _ = _assess(scorer, _step(agent))

    assert result.status is AssessStatus.NOT_APPLICABLE
    assert transport.calls == []


def test_a_required_unit_no_criterion_cites_is_counted_and_never_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from raidar.scoring.definition_coverage import scorer as scorer_module

    found = Defined(
        "sealed",
        (Unit("U1", "It reports.", True), Unit("U99", "a requirement nothing cites", True)),
        (Criterion("F1.AC1", "It reports.", ("It reports.",), (), (), False),),
    )
    monkeypatch.setattr(scorer_module, "read_define", lambda trace: found)
    transport = RecordedTransport(answer_for=lambda _i, _q: STRONG)
    scorer = definition.definition_coverage_scorer(_jev(transport))

    result, raw = _assess(scorer, _step(tmp_path))

    assert _value(result, "uncited_units") == 1
    assert _value(result, "jev_calls") == len(transport.calls) == 1
    uncited = next(row for row in _readings(raw)["checkpoint_1"]["units"] if row["id"] == "U99")
    assert uncited["cited_by"] == []
    assert uncited["best_expected"] is None


def test_tests_whose_answers_never_arrive_have_no_score_and_keep_the_reason(
    tmp_path: Path,
) -> None:
    scorer = tests_scorer.make_test_coverage_scorer(_jev(RecordedTransport(status=500)))

    result, raw = _assess(scorer, _step(_trace(tmp_path, tests=MAPPED)))

    assert result.status is AssessStatus.OK
    assert _value(result, "judged_criteria") == 0
    assert _value(result, "undecided_criteria") == 1
    assert _value(result, "mean_best_expected_level") is None
    entry = _readings(raw)["checkpoint_1"]["criteria"][0]["tests"][0]
    assert entry["expected"] is None
    assert entry["levels"] is None
    assert entry["undecided"].startswith("http_error")
    assert scorer.awaiting_answers()


CRITERION = Criterion("F1.AC1", "It reports.", (), (), (), False)


def _test(name: str, helpers: str = "") -> ReadTest:
    return ReadTest(f"t.py::{name}", f"def {name}(): pass", helpers, True, False)


def test_a_test_whose_helpers_overflow_a_request_is_sent_without_them() -> None:
    [(state, asking)] = requests(CRITERION, [_test("a", "x" * STATE_CHARS)])

    assert state["tests"]["t1"]["helpers"] == ""  # type: ignore[index]
    assert state["tests"]["t1"]["source"] == "def a(): pass"  # type: ignore[index]
    assert list(asking) == ["t1"]


def test_tests_are_split_over_requests_so_that_none_exceeds_the_budget() -> None:
    helpers = "x" * (STATE_CHARS * 6 // 10)
    tests = [_test(name, helpers) for name in ("a", "b", "c")]

    asked = requests(CRITERION, tests)

    assert [list(asking) for _, asking in asked] == [["t1"], ["t2"], ["t3"]]
    assert all(len(json.dumps(state)) < STATE_CHARS for state, _ in asked)
