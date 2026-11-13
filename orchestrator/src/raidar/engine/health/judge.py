"""J1–J3: independently audit retained requests, answers and aspect closure.

Missing evidence is unmeasured, never a passing judge. These checks read records;
they do not call Jev or import the workflow's closure implementation.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.contract.file_io import load_text
from raidar.engine.health.base import Audit, make_check
from raidar.engine.health.model import PASS, UNMEASURED, VIOLATION, HealthCheck


@dataclass(frozen=True)
class JudgeStep:
    step: str
    agent: Path
    verify: dict[str, Any]


def _json(path: Path) -> Any:
    try:
        return json.loads(load_text(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _artifact_path(step: JudgeStep, relative: Any) -> Path | None:
    if not isinstance(relative, str):
        return None
    path = (step.agent / relative).resolve()
    return path if path.is_relative_to(step.agent.resolve()) else None


def _artifact(step: JudgeStep, relative: Any) -> Any:
    path = _artifact_path(step, relative)
    return _json(path) if path is not None else None


def _probability(result: Any) -> float | None:
    if not isinstance(result, dict):
        return None
    answers = result.get("answers")
    answer = answers.get("aspect_asserted") if isinstance(answers, dict) else None
    value = answer.get("noul") if isinstance(answer, dict) else None
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
        return None
    return float(value) if result.get("ok") is True else None


def _raw_evidence(
    step: JudgeStep, call: dict[str, Any], result: dict[str, Any]
) -> tuple[list[str], list[str]]:
    bad, missing = [], []
    request = _artifact(step, call.get("request_file"))
    response = _artifact(step, call.get("response_file"))
    response_path = _artifact_path(step, call.get("response_file"))
    unsent = result.get("reason") in {"missing_key", "state_too_large"}
    if not isinstance(request, dict) and not unsent:
        missing.append("raw request missing")
    if call.get("response_file") is not None and (
        response_path is None or not response_path.is_file()
    ):
        missing.append("raw response missing")
    if result.get("ok"):
        if not isinstance(response, dict):
            missing.append("successful raw response missing")
        elif response.get("answers") != result.get("answers"):
            bad.append("raw answers differ from interpreted answers")
    if call.get("probability") != _probability(result):
        bad.append("probability differs from retained answer")
    return bad, missing


def _attempt_evidence(step: JudgeStep, index: int, call: Any) -> tuple[list[str], list[str]]:
    if not isinstance(call, dict):
        return [], ["malformed record"]
    result = _artifact(step, call.get("result_file"))
    if not isinstance(result, dict):
        return [], ["interpreted result missing"]
    bad, missing = _raw_evidence(step, call, result)
    if call.get("index") != index or result != call.get("result"):
        bad.append("ledger differs from retained result")
    return bad, missing


def _call_evidence(step: JudgeStep) -> tuple[list[str], list[str]]:
    bad, missing = [], []
    calls = step.verify.get("judge_calls")
    if not isinstance(calls, list):
        return [], ["judge calls are missing"]
    retained = list((step.agent / "workflow/judge").rglob("call-*.result.json"))
    if retained and len(retained) != len(calls):
        bad.append(f"{len(retained)} retained attempts but {len(calls)} ledger entries")
    for index, call in enumerate(calls):
        found, absent = _attempt_evidence(step, index, call)
        bad += [f"call {index}: {message}" for message in found]
        missing += [f"call {index}: {message}" for message in absent]
    return bad, missing


def _aspect_status(value: float | None, settings: dict[str, Any]) -> str:
    if value is None:
        return "undecided"
    if value <= settings["open_at"]:
        return "open"
    return "closed" if value >= settings["closed_at"] else "undecided"


def _aspect_calls(aspect: dict[str, Any], calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indices = aspect.get("calls")
    if not isinstance(indices, list):
        return []
    selected = [
        calls[i]
        for i in indices
        if isinstance(i, int) and 0 <= i < len(calls) and isinstance(calls[i], dict)
    ]
    return selected if len(selected) == len(indices) else []


def _aspect_evidence(
    aspect: dict[str, Any], calls: list[dict[str, Any]], settings: dict[str, Any]
) -> tuple[list[str], list[str]]:
    if aspect.get("status") == "not_asked":
        return [], []
    selected = _aspect_calls(aspect, calls)
    if not selected:
        return [], [f"{aspect.get('id')}: referenced call missing"]
    bad = []
    if any(c.get("aspect") != aspect.get("id") for c in selected):
        bad.append(f"{aspect.get('id')}: references another aspect's answer")
    value = _probability(selected[-1].get("result", {}))
    if aspect.get("probability") != value or aspect.get("status") != _aspect_status(
        value, settings
    ):
        bad.append(f"{aspect.get('id')}: closure disagrees with recorded answer")
    return bad, []


def _aspects(
    criterion: dict[str, Any], calls: list[dict[str, Any]], settings: dict[str, Any]
) -> tuple[list[str], list[str]]:
    bad, missing = [], []
    for aspect in criterion.get("aspects", []):
        found, absent = _aspect_evidence(aspect, calls, settings)
        bad += found
        missing += absent
    return bad, missing


def _regression_status(
    criterion: dict[str, Any], passed: dict[str, Any], verify: dict[str, Any]
) -> str:
    baseline = set(verify.get("baseline_passed", []))
    known = bool(baseline.intersection(criterion.get("tests", [])))
    closes = passed.get("gate_verdict") == "passed" and (
        not verify["judge"].get("regression_needs_baseline") or known
    )
    return "closed" if closes else "open"


def _closed_criterion(criterion: dict[str, Any], statuses: list[Any]) -> bool:
    tests = criterion.get("test_results", {})
    return bool(
        statuses
        and all(status == "closed" for status in statuses)
        and tests
        and all(result == "passed" for result in tests.values())
    )


def _criterion_status(
    criterion: dict[str, Any], passed: dict[str, Any], verify: dict[str, Any]
) -> str:
    if criterion.get("kind") == "regression":
        return _regression_status(criterion, passed, verify)
    if set(criterion.get("failing", [])).intersection({"unmapped", "no_aspects", "precheck"}):
        return "open"
    statuses = [a.get("status") for a in criterion.get("aspects", [])]
    if "open" in statuses:
        return "open"
    return "closed" if _closed_criterion(criterion, statuses) else "undecided"


def _objects(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _criterion_readable(criterion: Any) -> bool:
    if not isinstance(criterion, dict) or not isinstance(criterion.get("id"), str):
        return False
    for field in ("tests", "failing"):
        values = criterion.get(field, [])
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            return False
    aspects = criterion.get("aspects", [])
    return (
        isinstance(criterion.get("test_results", {}), dict)
        and isinstance(aspects, list)
        and len(_objects(aspects)) == len(aspects)
    )


def _criteria(passed: Any) -> list[dict[str, Any]]:
    if not isinstance(passed, dict):
        return []
    criteria = passed.get("criteria")
    if not isinstance(criteria, list) or not all(_criterion_readable(c) for c in criteria):
        return []
    return criteria


def _pass_closure(passed: dict[str, Any], step: JudgeStep) -> tuple[list[str], list[str]]:
    bad, missing = [], []
    verify = step.verify
    criteria = _criteria(passed)
    if not criteria:
        return [], ["pass has missing or malformed criteria"]
    for criterion in criteria:
        found, absent = _aspects(criterion, verify["judge_calls"], verify["judge"])
        bad += found
        missing += absent
        expected = _criterion_status(criterion, passed, verify)
        if criterion.get("status") != expected:
            bad.append(f"{criterion.get('id')}: criterion closure disagrees with its evidence")
    for status in ("closed", "open", "undecided"):
        expected_ids = [c["id"] for c in criteria if c.get("status") == status]
        if passed.get(status) != expected_ids:
            bad.append(f"pass {passed.get('pass')}: {status} summary disagrees with criteria")
    return bad, missing


def _closure(step: JudgeStep) -> tuple[list[str], list[str]]:
    bad, missing = [], []
    verify = step.verify
    settings, calls, passes = verify.get("judge"), verify.get("judge_calls"), verify.get("judged")
    if (
        not isinstance(settings, dict)
        or not all(isinstance(settings.get(key), int | float) for key in ("open_at", "closed_at"))
        or not isinstance(calls, list)
        or not isinstance(passes, list)
        or not passes
    ):
        return [], ["judge settings, calls or passes missing"]
    for passed in passes:
        found, absent = _pass_closure(passed, step)
        bad += found
        missing += absent
    return bad, missing


def _undecided(step: JudgeStep) -> tuple[list[str], list[str]]:
    passes = step.verify.get("judged")
    if not isinstance(passes, list) or not passes:
        return [], ["no final judge pass"]
    last = passes[-1]
    criteria = _criteria(last)
    if not criteria:
        return [], ["no readable final criterion readings"]
    if step.verify.get("ending") == "verified" and (
        last.get("gate_verdict") != "passed" or any(c.get("status") != "closed" for c in criteria)
    ):
        return ["verified despite an unclosed criterion or unaccepted gate"], []
    return [], []


def _judge_steps(steps: Sequence[tuple[str, Path]]) -> tuple[list[JudgeStep], list[str]]:
    found, unread = [], []
    for name, agent in steps:
        workflow = _json(agent / "workflow/workflow.json")
        if not isinstance(workflow, dict):
            unread.append(f"{name}: missing workflow record")
            continue
        record = workflow.get("jev-judge/1")
        if isinstance(record, dict) and record.get("judge") is not None:
            found.append(JudgeStep(name, agent, record))
    return found, unread


def _pass_undecided_ids(passed: dict[str, Any]) -> Iterator[Any]:
    return (
        aspect.get("id")
        for criterion in _objects(passed.get("criteria"))
        for aspect in _objects(criterion.get("aspects"))
        if aspect.get("status") == "undecided"
    )


def _undecided_ids(step: JudgeStep) -> list[Any]:
    return [
        aspect_id
        for passed in _objects(step.verify.get("judged"))
        for aspect_id in _pass_undecided_ids(passed)
    ]


def _family_check(
    check_id: str,
    inspect: Callable[[JudgeStep], tuple[list[str], list[str]]],
    steps: list[JudgeStep],
    unread: list[str],
) -> HealthCheck:
    bad, missing = [], list(unread)
    for step in steps:
        violations, absent = inspect(step)
        bad += [f"{step.step}: {s}" for s in violations]
        missing += [f"{step.step}: {s}" for s in absent]
    status = VIOLATION if bad else UNMEASURED if missing else PASS
    detail = "; ".join(bad or missing) or f"{len(steps)} checkpoint(s): judge evidence agrees"
    return make_check(
        check_id,
        status,
        detail,
        findings=bad,
        unmeasured=missing,
        undecided={step.step: _undecided_ids(step) for step in steps},
    )


def judge_family(steps: Sequence[tuple[str, Path]]) -> tuple[HealthCheck, ...]:
    """Audit judge-enabled checkpoints only; a missing judge record stays unmeasured."""
    found, unread = _judge_steps(steps)
    if not found:
        return ()
    return tuple(
        _family_check(check_id, inspect, found, unread)
        for check_id, inspect in (("J1", _call_evidence), ("J2", _closure), ("J3", _undecided))
    )


def check_judge(audit: Audit) -> tuple[HealthCheck, ...]:
    return judge_family(
        [(step.step, audit.trial_dir / "steps" / step.step / "agent") for step in audit.attempted]
    )
