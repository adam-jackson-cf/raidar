"""Jev Decisions API contract, ported from the local ``jev.ts`` (design memo §3.5).

Jev (TypeSafe System One) answers typed questions about a JSON ``state``:
``noul`` (a probability that a statement holds), ``choice`` (one of 2-255
options) and ``score`` (an ordinal level of 2-10). Requests always name the
floating ``~typesafe/jev-latest`` selector, because OpenRouter rejects
versioned selectors; the model the provider *resolved* is checked against
``JEV_PINNED_MODEL`` instead, and any other model is rejected.

Malformed question blocks are programmer errors and raise before any network
call. Everything the provider returns is validated here, never trusted.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

JEV_MODEL = "~typesafe/jev-latest"
JEV_PINNED_MODEL = "typesafe/jev-1.13-20260917"
JEV_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
# Jev 1.13 accepts ~32k tokens of state plus questions; the state stays well under it.
JEV_STATE_CHAR_LIMIT = 96_000
DEFAULT_TIMEOUT_S = 15.0

QUESTION_ID = re.compile(r"[a-z][a-z0-9_]{0,63}")
_MODEL_ID = re.compile(r"[\w./~-]{1,120}", re.ASCII)
INVALID_MODEL_ID = "invalid-model-identifier"

Probability = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class NoulCriteria(_Frozen):
    true: str | None = None
    false: str | None = None


class NoulQuestion(_Frozen):
    type: Literal["noul"] = "noul"
    instructions: str
    criteria: NoulCriteria | None = None


class ChoiceQuestion(_Frozen):
    type: Literal["choice"] = "choice"
    instructions: str
    criteria: dict[str, str | None]


class ScoreQuestion(_Frozen):
    type: Literal["score"] = "score"
    instructions: str
    criteria: tuple[str, ...]


JevQuestion = Annotated[NoulQuestion | ChoiceQuestion | ScoreQuestion, Field(discriminator="type")]
JevQuestions = Mapping[str, JevQuestion]
_QUESTIONS = TypeAdapter(dict[str, JevQuestion])


class _Answer(BaseModel):
    # The provider may add fields; only the contract's fields are read. Strict,
    # like jev.ts ``z.number()``/``z.string()``: a string "0.4" or a boolean is
    # not a probability and is rejected, never coerced.
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)


class NoulAnswer(_Answer):
    type: Literal["noul"]
    noul: Probability


class ChoiceAnswer(_Answer):
    type: Literal["choice"]
    choice: str
    probabilities: dict[str, Probability]
    confidence: Probability


class ScoreAnswer(_Answer):
    type: Literal["score"]
    score: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    probabilities: dict[str, Probability]
    confidence: Probability
    legend: dict[str, str] | None = None


JevAnswer = Annotated[NoulAnswer | ChoiceAnswer | ScoreAnswer, Field(discriminator="type")]
JevAnswers = dict[str, JevAnswer]
_ANSWERS = TypeAdapter(JevAnswers)


class DecisionUsage(_Answer):
    input_tokens: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    output_tokens: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    cost: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]


class DecisionResponse(_Answer):
    id: str
    model: str
    provider: str | None = None
    answers: JevAnswers
    usage: DecisionUsage


def parse_questions(raw: Any) -> dict[str, JevQuestion]:
    """Typed questions from their wire form (for example a stored audit record)."""
    return _QUESTIONS.validate_python(raw)


def parse_answers(raw: Any) -> JevAnswers:
    return _ANSWERS.validate_python(raw)


def questions_wire(questions: JevQuestions) -> dict[str, Any]:
    """The request's ``questions`` object, exactly as sent and hashed."""
    dumped: dict[str, Any] = _QUESTIONS.dump_python(dict(questions), mode="json", exclude_none=True)
    return dumped


def answers_wire(answers: JevAnswers) -> dict[str, Any]:
    dumped: dict[str, Any] = _ANSWERS.dump_python(answers, mode="json", exclude_none=True)
    return dumped


def validate_questions(questions: JevQuestions) -> None:
    """Reject malformed question blocks before any network call."""
    if not questions:
        raise ValueError("Jev needs at least one question.")
    for question_id, question in questions.items():
        if not QUESTION_ID.fullmatch(question_id):
            raise ValueError(f'Jev question id "{question_id}" must match {QUESTION_ID.pattern}.')
        if not question.instructions.strip():
            raise ValueError(f'Jev question "{question_id}" has empty instructions.')
        if isinstance(question, ChoiceQuestion) and not 2 <= len(question.criteria) <= 255:
            raise ValueError(f'Jev choice "{question_id}" needs 2-255 options.')
        if isinstance(question, ScoreQuestion) and not 2 <= len(question.criteria) <= 10:
            raise ValueError(f'Jev score "{question_id}" needs 2-10 levels.')


def answer_matches(question: JevQuestion, given: JevAnswer | None) -> bool:
    """True when ``given`` is a well-formed answer to ``question``."""
    if given is None or given.type != question.type:
        return False
    if isinstance(question, ChoiceQuestion) and isinstance(given, ChoiceAnswer):
        return given.choice in question.criteria and all(
            option in given.probabilities for option in question.criteria
        )
    if isinstance(question, ScoreQuestion) and isinstance(given, ScoreAnswer):
        return given.score <= len(question.criteria) - 1
    return True


def safe_model_id(model: str) -> str:
    """The resolved model id, or a fixed marker when it is not a plausible identifier."""
    return model if _MODEL_ID.fullmatch(model) else INVALID_MODEL_ID
