"""Asking Jev score questions on the host, retrying and recording every attempt.

One request carries a state and one 3-level Score question per subject. Following the workflow
judge's rule (a request is retried up to three times; an unanswered question is undecided, never
guessed), ``Asker.ask`` calls ``RecordedJev.decide`` again while some question has no usable
answer. A missing key or a state too large to send cannot change on retry, so they stop at once.
Every attempt is kept by ``RecordedJev`` (raw request, raw response, interpreted result).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from raidar.judge.client import FailureReason, JevFailure
from raidar.judge.live import RecordedJev
from raidar.judge.protocol import ScoreAnswer, ScoreQuestion

ATTEMPTS = 3
LEVELS = 3
_FINAL = frozenset({FailureReason.MISSING_KEY, FailureReason.STATE_TOO_LARGE})


def expected_level(levels: tuple[float, ...]) -> float:
    """A 3-level Score's expected level, ``P1 + 2 * P2``."""
    return levels[1] + 2 * levels[2]


@dataclass(frozen=True)
class Asked:
    """What one request produced: each decided question's level probabilities, the rest why not."""

    levels: dict[str, tuple[float, ...]]
    undecided: dict[str, str]
    calls: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    resolved_model: str | None = None


def _levels(answer: object) -> tuple[float, ...] | None:
    if not isinstance(answer, ScoreAnswer):
        return None
    values = [answer.probabilities.get(str(level)) for level in range(LEVELS)]
    if any(value is None for value in values):
        return None
    return tuple(float(value) for value in values if value is not None)


def _summary(call: Mapping[str, Any]) -> dict[str, Any]:
    result = call["result"]
    return {
        "index": call["index"],
        "request_file": call["request_file"],
        "response_file": call["response_file"],
        "result_file": call["result_file"],
        "ok": result.get("ok"),
        "reason": result.get("reason"),
        "error": result.get("error"),
        "seconds": call["seconds"],
        "model": call["model"],
        "resolved_model": call["resolved_model"],
    }


class Asker:
    """Asks Jev through a ``RecordedJev``; see the module docstring."""

    def __init__(self, jev: RecordedJev) -> None:
        self._jev = jev

    def ask(self, state: Mapping[str, Any], questions: Mapping[str, ScoreQuestion]) -> Asked:
        first = len(self._jev.calls)
        levels: dict[str, tuple[float, ...]] = {}
        why = dict.fromkeys(questions, "not asked")
        resolved: str | None = None
        for _ in range(ATTEMPTS):
            wanted = {name: question for name, question in questions.items() if name not in levels}
            result = self._jev.decide(state, wanted)
            if isinstance(result, JevFailure):
                why.update(dict.fromkeys(wanted, f"{result.reason.value}: {result.error}"))
                if result.reason in _FINAL:
                    break
                continue
            resolved = result.resolved_model
            _absorb(result.answers, list(wanted), levels, why)
            if len(levels) == len(questions):
                break
        undecided = {name: reason for name, reason in why.items() if name not in levels}
        calls = tuple(_summary(call) for call in self._jev.calls[first:])
        return Asked(levels, undecided, calls, resolved)


def _absorb(
    answers: Mapping[str, object],
    wanted: list[str],
    levels: dict[str, tuple[float, ...]],
    why: dict[str, str],
) -> None:
    """Keep each wanted question's usable answer, else why it is not one."""
    for name in wanted:
        found = _levels(answers.get(name))
        if found is None:
            why[name] = "the answer is not a 3-level score with every level's probability"
        else:
            levels[name] = found
