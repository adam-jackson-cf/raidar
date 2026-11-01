"""Asking Jev score questions: retries stop at once or when every question is decided.

Only unanswered questions are asked again, at most three times; an answer that is not a
3-level score with every level's probability never decides a question, and a missing key or
an oversized state cannot change on retry.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from raidar.judge.client import FailureReason, JevFailure, JevResult, JevSuccess
from raidar.judge.protocol import NoulAnswer, ScoreAnswer, ScoreQuestion
from raidar.scoring.coverage_inputs.asking import Asker, expected_level

QUESTIONS = {
    "a": ScoreQuestion(instructions="a?", criteria=("x",)),
    "b": ScoreQuestion(instructions="b?", criteria=("x",)),
}


class ScriptedJev:
    """A recorded Jev whose answers are scripted; each attempt records one call."""

    def __init__(self, *results: JevResult) -> None:
        self._results = list(results)
        self.calls: list[dict[str, Any]] = [{"index": 0}]  # an earlier request's call
        self.asked: list[list[str]] = []

    def decide(self, state: Mapping[str, Any], wanted: Mapping[str, ScoreQuestion]) -> JevResult:
        self.asked.append(list(wanted))
        result = self._results.pop(0)
        self.calls.append(
            {
                "index": len(self.calls),
                "request_file": "request.json",
                "response_file": "response.json",
                "result_file": "result.json",
                "result": {"ok": isinstance(result, JevSuccess), "reason": None, "error": None},
                "seconds": 0.0,
                "model": "m",
                "resolved_model": "m-1",
            }
        )
        return result


def _score(*probabilities: float, levels: int = 3) -> ScoreAnswer:
    return ScoreAnswer(
        type="score",
        score=1.0,
        probabilities={str(level): value for level, value in enumerate(probabilities[:levels])},
        confidence=0.9,
    )


def _success(**answers: Any) -> JevSuccess:
    return JevSuccess(answers, "m-1", "id", 0.0, 1, 1, 1)


def _failure(reason: FailureReason) -> JevFailure:
    return JevFailure(reason, f"{reason.value} happened", 1)


def test_only_unanswered_questions_are_asked_again() -> None:
    jev = ScriptedJev(
        _success(a=_score(0.2, 0.3, 0.5), b=NoulAnswer(type="noul", noul=0.5)),
        _success(b=_score(0.6, 0.2, 0.2)),
    )

    asked = Asker(jev).ask({}, QUESTIONS)  # type: ignore[arg-type]

    assert jev.asked == [["a", "b"], ["b"]]
    assert asked.levels == {"a": (0.2, 0.3, 0.5), "b": (0.6, 0.2, 0.2)}
    assert asked.undecided == {}
    assert asked.resolved_model == "m-1"
    assert [call["index"] for call in asked.calls] == [1, 2]


def test_an_answer_missing_a_level_never_decides_a_question() -> None:
    short = _score(0.5, 0.5, levels=2)
    jev = ScriptedJev(*[_success(a=short, b=_score(0.1, 0.1, 0.8)) for _ in range(3)])

    asked = Asker(jev).ask({}, QUESTIONS)  # type: ignore[arg-type]

    assert len(jev.asked) == 3
    assert list(asked.levels) == ["b"]
    assert asked.undecided == {
        "a": "the answer is not a 3-level score with every level's probability"
    }


def test_a_question_the_answer_omits_is_undecided() -> None:
    jev = ScriptedJev(*[_success(a=_score(0.1, 0.1, 0.8)) for _ in range(3)])

    asked = Asker(jev).ask({}, QUESTIONS)  # type: ignore[arg-type]

    assert list(asked.levels) == ["a"]
    assert list(asked.undecided) == ["b"]


def test_a_transient_failure_is_retried_three_times_then_reported_per_question() -> None:
    jev = ScriptedJev(*[_failure(FailureReason.NETWORK) for _ in range(3)])

    asked = Asker(jev).ask({}, QUESTIONS)  # type: ignore[arg-type]

    assert len(jev.asked) == 3
    assert asked.levels == {}
    assert asked.undecided == dict.fromkeys(QUESTIONS, "network: network happened")
    assert asked.resolved_model is None


def test_a_missing_key_or_oversized_state_stops_at_once() -> None:
    for reason in (FailureReason.MISSING_KEY, FailureReason.STATE_TOO_LARGE):
        jev = ScriptedJev(_failure(reason))

        asked = Asker(jev).ask({}, QUESTIONS)  # type: ignore[arg-type]

        assert len(jev.asked) == 1
        assert asked.undecided["a"].startswith(reason.value)


def test_the_expected_level_weights_the_second_level_twice() -> None:
    assert expected_level((0.2, 0.3, 0.5)) == pytest.approx(1.3)
