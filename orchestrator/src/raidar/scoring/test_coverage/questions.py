"""The questions of ``test-coverage``: wording and request layout, versioned with the scorer.

``QUESTION_SET`` names the wording: the process audit's stage-B question (factory-x-loops
``assurance-audit-jev/2``, ``TESTED_INSTRUCTIONS`` and ``TESTED_LEVELS``) with the quoted issue
requirement replaced by the acceptance criterion (``criterion``: statement, aspects, examples) and
the issue context left out. One request carries one criterion and some of its mapped tests, one
question per test; the tests of a criterion are split over requests so that no state exceeds
``STATE_CHARS``. Answers are comparable only under one wording: a changed word is a new set.
"""

from __future__ import annotations

import json

from raidar.judge.protocol import ScoreQuestion
from raidar.scoring.coverage_inputs.records import Criterion
from raidar.scoring.coverage_inputs.testsource import ReadTest

QUESTION_SET = "raidar-test-coverage-jev/1"
STATE_CHARS = 80_000
INSTRUCTIONS = (
    "If the program got the behaviour in `criterion` (its statement, aspects and examples) "
    "wrong, would the test in `tests.{id}.source` (with the helpers in `tests.{id}.helpers`) "
    "fail? Only code that runs counts: a test's name, comments and docstring prove nothing."
)
LEVELS = (
    "The test never runs the program in the situation the criterion describes.",
    "The test runs that situation, but its assertions would still pass if the "
    "program got the criterion's behaviour wrong: it checks only that the run "
    "succeeds, or only another part of the result, or compares against a value "
    "computed the same way the program computes it.",
    "The test runs that situation and an assertion would fail if the program got "
    "the criterion's behaviour wrong, such as an exact comparison of the output, "
    "message, exit code or value the criterion gives. An exact comparison of a whole "
    "result checks every part of it.",
)


def _entry(test: ReadTest, *, helpers: bool) -> dict[str, str]:
    return {"id": test.id, "source": test.source, "helpers": test.helpers if helpers else ""}


def _size(state: dict[str, object]) -> int:
    return len(json.dumps(state, ensure_ascii=False, sort_keys=True))


def requests(
    criterion: Criterion, tests: list[ReadTest]
) -> list[tuple[dict[str, object], dict[str, ScoreQuestion]]]:
    """The requests asking whether ``tests`` exercise ``criterion``, tests grouped to fit."""
    groups: list[dict[str, dict[str, str]]] = []
    base = _size({"criterion": criterion.view(), "tests": {}})
    used = base
    for number, test in enumerate(tests, start=1):
        entry = _entry(test, helpers=True)
        if base + _size({"tests": entry}) > STATE_CHARS:
            entry = _entry(test, helpers=False)
        cost = _size({"t": entry})
        if not groups or used + cost > STATE_CHARS:
            groups.append({})
            used = base
        groups[-1][f"t{number}"] = entry
        used += cost
    return [
        (
            {"criterion": criterion.view(), "tests": group},
            {
                name: ScoreQuestion(instructions=INSTRUCTIONS.format(id=name), criteria=LEVELS)
                for name in group
            },
        )
        for group in groups
    ]
