"""The coverage scorers over retained workflow artifacts, with a fake Jev transport (no network)."""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from define_tool.stage import criteria_document
from raidar.judge.live import RecordedJev
from raidar.scoring import definition_coverage as definition
from raidar.scoring import test_coverage as tests_scorer
from raidar.scoring.framework.context import AssessContext, StepInputs
from raidar.scoring.framework.result import AssessResult, AssessStatus
from tests.jev_support import TEST_KEY, RecordedTransport

DEFINITION = Path(__file__).parent / "fixtures/define/compact_replay/definition-a-cp1.md"
SOURCE = re.compile(r'^\s+Source: "(.*)"$', re.MULTILINE)
CRITERION = re.compile(r"^- F\d+\.AC\d+:", re.MULTILINE)
STRONG = {"type": "score", "score": 2, "probabilities": {"0": 0.0, "1": 0.2, "2": 0.8}}
STRONG = {**STRONG, "confidence": 0.8}
SCORERS = [definition.definition_coverage_scorer, tests_scorer.make_test_coverage_scorer]


def _unit_count() -> int:
    return len(set(SOURCE.findall(DEFINITION.read_text(encoding="utf-8"))))


TEST_FILE = """\
import subprocess


def run(*args):
    return subprocess.run(["python", "tally.py", *args], capture_output=True, text=True).stdout


def test_report_invocation():
    assert run("report", "f.csv") == "food: 4.50\\n"


def test_standard_library_runtime():
    run("report", "f.csv")
"""


def _jev(transport: RecordedTransport, key: str | None = TEST_KEY) -> Any:
    environ = {"OPENROUTER_API_KEY": key} if key else {}
    return lambda directory: RecordedJev(directory, transport=transport, environ=environ)


def _step(trace: Path | None, name: str = "checkpoint_1", reached: bool = True) -> StepInputs:
    return StepInputs(
        step=name,
        reached=reached,
        checkpoint=name,
        snapshot=None,
        snapshot_digest=None,
        record=None,
        record_digest=None,
        trace_dir=trace,
        trace_digest=None,
        agent_result=None,
    )


@functools.cache
def _criteria(text: str) -> dict[str, Any]:
    return criteria_document(text)


def _trace(
    root: Path,
    *,
    define: bool = True,
    tests: dict[str, list[str]] | None = None,
    regression: bool = False,
) -> Path:
    text = DEFINITION.read_text(encoding="utf-8")
    quotes = list(dict.fromkeys(SOURCE.findall(text)))
    agent = root / "agent"
    destination = agent / "workflow/package/spec/criteria.json"
    destination.parent.mkdir(parents=True)
    document = copy.deepcopy(_criteria(text))
    if regression:
        document["features"].append(
            {
                "id": "F9",
                "name": "Regression",
                "outcome": "Existing ordering is preserved",
                "criteria": [
                    {
                        "id": "F9.AC1",
                        "criterion": "Existing ordering is unchanged.",
                        "verification": "Run the regression test",
                        "sources": [],
                        "kind": "regression",
                        "aspects": [],
                        "examples": [],
                    }
                ],
            }
        )
    data = json.dumps(document).encode()
    destination.write_bytes(data)
    stdout = json.dumps(
        {"tokens": {"UNITS": "\n".join(f"U{n}*: {quote}" for n, quote in enumerate(quotes, 1))}}
    )
    sources = agent / "workflow/sources/final/tests"
    sources.mkdir(parents=True)
    (sources / "test_tally.py").write_text(TEST_FILE)
    record: dict[str, Any] = {
        "outputs/1": {
            "acme/spec": {
                "criteria": {
                    "contract": "criteria/1",
                    "host_path": "workflow/package/spec/criteria.json",
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                    "error": None,
                }
            }
        }
        if define
        else {},
        "setup/1": {
            "acme/spec": {
                "call": {
                    "stdout": stdout,
                    "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
                }
            }
        },
        "acceptance-reply/1": {
            "ac_tests": tests if tests is not None else {},
            "sources": "workflow/sources/final",
        },
    }
    (agent / "workflow/workflow.json").write_text(json.dumps(record))
    return agent


def _assess(scorer: Any, *steps: StepInputs) -> tuple[AssessResult, dict[str, bytes]]:
    return scorer.assess_with_raw(AssessContext(problem="toy", steps=steps))


def _value(result: AssessResult, metric: str, step: str = "checkpoint_1") -> float | None:
    found = next(o for o in result.observations if o.metric == metric and o.subject == step)
    return found.value


def _readings(raw: dict[str, bytes]) -> dict[str, Any]:
    return json.loads(raw["readings.json"])["steps"]


def test_definition_coverage_reads_each_required_unit_and_keeps_every_call(tmp_path: Path) -> None:
    transport = RecordedTransport(answer_for=lambda _i, _q: STRONG)
    scorer = definition.definition_coverage_scorer(_jev(transport))
    result, raw = _assess(scorer, _step(_trace(tmp_path)))
    units = _unit_count()

    assert result.status is AssessStatus.OK
    assert _value(result, "required_units") == units
    assert _value(result, "jev_calls") == units
    assert _value(result, "uncited_units") == 0
    assert _value(result, "mean_best_expected_level") == pytest.approx(1.8)
    rows = _readings(raw)["checkpoint_1"]["units"]
    assert len(rows) == units
    assert rows[0]["cited_by"][0]["criterion"] == "F1.AC1"
    assert rows[0]["cited_by"][0]["levels"] == [0.0, 0.2, 0.8]
    assert result.provenance.jev_model_resolved == "typesafe/jev-1.13-20260917"
    assert not scorer.awaiting_answers()
    assert "jev/checkpoint_1/call-0001.request.json" in raw
    assert "jev/checkpoint_1/call-0001.response.json" in raw
    assert all(TEST_KEY.encode() not in data for data in raw.values())


def test_definition_coverage_retries_then_records_the_undecided(tmp_path: Path) -> None:
    transport = RecordedTransport(status=500)
    scorer = definition.definition_coverage_scorer(_jev(transport))
    result, raw = _assess(scorer, _step(_trace(tmp_path)))

    assert result.status is AssessStatus.OK
    assert _value(result, "judged_units") == 0
    assert _value(result, "mean_best_expected_level") is None
    assert _value(result, "jev_calls") == 3 * _unit_count()
    assert scorer.awaiting_answers()
    first = _readings(raw)["checkpoint_1"]["units"][0]
    assert first["cited_by"][0]["undecided"].startswith("http_error")


def test_a_missing_key_is_recorded_once_per_request_and_not_retried(tmp_path: Path) -> None:
    transport = RecordedTransport()
    scorer = definition.definition_coverage_scorer(_jev(transport, key=None))
    result, _ = _assess(scorer, _step(_trace(tmp_path)))

    assert transport.calls == []
    assert _value(result, "jev_calls") == _value(result, "required_units")
    assert scorer.awaiting_answers()


def test_a_second_attempt_can_decide_a_question(tmp_path: Path) -> None:
    seen: list[int] = []

    def answer(_id: str, _question: dict[str, Any]) -> dict[str, Any]:
        seen.append(1)
        return {"type": "noul", "noul": 0.5} if len(seen) == 1 else STRONG

    scorer = definition.definition_coverage_scorer(_jev(RecordedTransport(answer_for=answer)))
    result, _ = _assess(scorer, _step(_trace(tmp_path)))

    assert _value(result, "undecided_questions") == 0
    assert _value(result, "jev_calls") == _unit_count() + 1


@pytest.mark.parametrize("scorer", SCORERS)
def test_arms_without_define_and_unreached_steps_are_not_applicable(
    tmp_path: Path, scorer: Any
) -> None:
    transport = RecordedTransport()
    built = scorer(_jev(transport))
    steps = (_step(_trace(tmp_path, define=False)), _step(None, "checkpoint_2", False))
    result, _ = _assess(built, *steps)

    assert result.status is AssessStatus.NOT_APPLICABLE
    assert {o.status for o in result.observations} == {AssessStatus.NOT_APPLICABLE}
    assert transport.calls == []


@pytest.mark.parametrize("scorer", SCORERS)
def test_missing_artifacts_are_insufficient_evidence_not_zero(tmp_path: Path, scorer: Any) -> None:
    agent = _trace(tmp_path)
    record_path = agent / "workflow/workflow.json"
    record = json.loads(record_path.read_text())
    record["setup/1"] = {}
    record_path.write_text(json.dumps(record))
    result, _ = _assess(scorer(_jev(RecordedTransport())), _step(agent), _step(None))

    assert result.status is AssessStatus.INSUFFICIENT_EVIDENCE
    assert all(o.value is None for o in result.observations)


def test_an_unsealed_definition_is_insufficient_evidence(tmp_path: Path) -> None:
    agent = _trace(tmp_path)
    record = json.loads((agent / "workflow/workflow.json").read_text())
    record["outputs/1"]["acme/spec"]["criteria"]["error"] = "not a single regular file"
    (agent / "workflow/workflow.json").write_text(json.dumps(record))
    scorer = definition.definition_coverage_scorer(_jev(RecordedTransport()))
    result, _ = _assess(scorer, _step(agent))

    assert result.status is AssessStatus.INSUFFICIENT_EVIDENCE


def test_test_coverage_scores_mapped_tests_with_helpers_and_holds_weak_ones(tmp_path: Path) -> None:
    mapped = {
        "F1.AC1": [
            "tests/test_tally.py::test_report_invocation",
            "tests/test_tally.py::test_standard_library_runtime",
            "tests/test_tally.py::test_absent",
        ]
    }
    transport = RecordedTransport(answer_for=lambda _i, _q: STRONG)
    scorer = tests_scorer.make_test_coverage_scorer(_jev(transport))
    result, raw = _assess(scorer, _step(_trace(tmp_path, tests=mapped)))
    criteria = len(CRITERION.findall(DEFINITION.read_text(encoding="utf-8")))

    assert result.status is AssessStatus.OK
    assert _value(result, "criteria") == criteria
    assert _value(result, "unmapped_criteria") == criteria - 1
    assert _value(result, "unread_tests") == 1
    assert _value(result, "judged_criteria") == 1
    assert _value(result, "jev_calls") == 1
    assert _value(result, "mean_best_expected_level") == pytest.approx(1.8)
    row = _readings(raw)["checkpoint_1"]["criteria"][0]
    tests = {entry["test"].split("::")[1]: entry for entry in row["tests"]}
    assert tests["test_report_invocation"]["has_assertion"]
    assert not tests["test_standard_library_runtime"]["has_assertion"]
    assert tests["test_standard_library_runtime"]["expected"] == pytest.approx(1.0)
    assert row["unread"] == ["tests/test_tally.py::test_absent"]
    assert "def run(" in json.dumps(transport.calls[0]["body"])


def test_test_coverage_judges_a_mapped_regression_criterion(tmp_path: Path) -> None:
    mapped = {"F9.AC1": ["tests/test_tally.py::test_report_invocation"]}
    transport = RecordedTransport(answer_for=lambda _i, _q: STRONG)
    scorer = tests_scorer.make_test_coverage_scorer(_jev(transport))
    result, raw = _assess(scorer, _step(_trace(tmp_path, tests=mapped, regression=True)))

    row = next(r for r in _readings(raw)["checkpoint_1"]["criteria"] if r["id"] == "F9.AC1")
    assert row["regression"] is True
    assert row["tests"][0]["expected"] == pytest.approx(1.8)
    assert _value(result, "judged_criteria") == 1
    assert _value(result, "criteria") == len(CRITERION.findall(DEFINITION.read_text())) + 1


@pytest.mark.parametrize("scorer", SCORERS)
def test_a_step_with_no_workflow_record_is_insufficient_evidence(
    tmp_path: Path, scorer: Any
) -> None:
    agent = _trace(tmp_path)
    (agent / "workflow/workflow.json").unlink()
    result, _ = _assess(scorer(_jev(RecordedTransport())), _step(agent))

    assert result.status is AssessStatus.INSUFFICIENT_EVIDENCE


def test_test_coverage_without_retained_sources_is_insufficient_evidence(tmp_path: Path) -> None:
    agent = _trace(tmp_path, tests={"F1.AC1": ["tests/test_tally.py::test_report_invocation"]})
    record = json.loads((agent / "workflow/workflow.json").read_text())
    record["acceptance-reply/1"]["sources"] = "workflow/sources/missing"
    (agent / "workflow/workflow.json").write_text(json.dumps(record))
    scorer = tests_scorer.make_test_coverage_scorer(_jev(RecordedTransport()))
    result, _ = _assess(scorer, _step(agent))

    assert result.status is AssessStatus.INSUFFICIENT_EVIDENCE


def test_skipped_and_class_tests_are_read_by_their_syntax(tmp_path: Path) -> None:
    agent = _trace(tmp_path, tests={"F1.AC1": ["tests/test_more.py::TestA::test_x[1]"]})
    (agent / "workflow/sources/final/tests/test_more.py").write_text(
        "import pytest\n\n\nclass TestA:\n    @pytest.mark.skip\n"
        "    def test_x(self):\n        assert 1 == 2\n"
    )
    transport = RecordedTransport(answer_for=lambda _i, _q: STRONG)
    scorer = tests_scorer.make_test_coverage_scorer(_jev(transport))
    result, raw = _assess(scorer, _step(agent))

    entry = _readings(raw)["checkpoint_1"]["criteria"][0]["tests"][0]
    assert entry["skipped"] is True
    assert entry["expected"] == pytest.approx(1.0)
    assert _value(result, "undecided_questions") == 0


@pytest.mark.parametrize(("marker", "expected"), [("slow", 1.8), ("skip", 1.0)])
def test_only_skip_module_marks_cap_the_test_reading(
    tmp_path: Path, marker: str, expected: float
) -> None:
    agent = _trace(tmp_path, tests={"F1.AC1": ["tests/test_tally.py::test_report_invocation"]})
    source = agent / "workflow/sources/final/tests/test_tally.py"
    source.write_text(f"import pytest\npytestmark = pytest.mark.{marker}\n" + TEST_FILE)
    scorer = tests_scorer.make_test_coverage_scorer(
        _jev(RecordedTransport(answer_for=lambda _i, _q: STRONG))
    )
    result, _ = _assess(scorer, _step(agent))
    assert _value(result, "mean_best_expected_level") == pytest.approx(expected)
