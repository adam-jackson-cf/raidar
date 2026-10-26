"""The questions of ``definition-coverage``: the wording, versioned with the scorer.

``QUESTION_SET`` names the wording. It is the process audit's stage-A question
(factory-x-loops ``assurance-audit-jev/2``, ``CAPTURED_INSTRUCTIONS`` and ``CAPTURED_LEVELS``)
with the quoted issue requirement replaced by the issue unit the definition must capture
(``unit.text``) and the issue context left out (the units are not placed in the issue here).
Answers are comparable only under one wording: a changed word is a new set.
"""

from __future__ import annotations

from raidar.judge.protocol import ScoreQuestion
from raidar.scoring.coverage_inputs.records import Criterion, Unit

QUESTION_SET = "raidar-definition-coverage-jev/1"
INSTRUCTIONS = (
    "How fully does the acceptance criterion in `criteria.{id}` state the "
    "requirement quoted in `unit.text`? Judge only what the "
    "criterion's statement, aspects and examples say."
)
LEVELS = (
    "The criterion is about other behaviour: none of its statement, aspects or "
    "examples concerns what the quote requires.",
    "The criterion concerns the quoted requirement but does not state it: the "
    "requirement is only implied by general wording or only shown by an example, "
    "or the criterion leaves out a value or condition that appears in the quote "
    "itself, or gives a different value or outcome.",
    "The criterion states the quoted requirement: its statement or an aspect says "
    "the outcome the quote requires, with the values the quote gives. Details "
    "that the quote does not mention do not matter.",
)


def question_id(criterion: Criterion) -> str:
    return criterion.id.lower().replace(".", "_")


def request(
    unit: Unit, criteria: list[Criterion]
) -> tuple[dict[str, object], dict[str, ScoreQuestion]]:
    """The state and the questions asking whether ``criteria`` capture ``unit``."""
    state: dict[str, object] = {
        "unit": {"text": unit.text},
        "criteria": {question_id(item): item.view() for item in criteria},
    }
    questions = {
        question_id(item): ScoreQuestion(
            instructions=INSTRUCTIONS.format(id=question_id(item)), criteria=LEVELS
        )
        for item in criteria
    }
    return state, questions
