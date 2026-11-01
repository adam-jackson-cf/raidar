"""The portable call protocol of ``stop/1``: the call context, the stop result and its bounds.

Pure data and decisions. Running a call, attributing failures, repairs and the container's
closed flag are lab policy and stay with the host; what a conforming host and a package share is
here:

- the call context a package reads (``task, params, round, carry, state, outputs`` and, for gate
  calls, the root-owned ``base``);
- the stop result a package prints (``{status, message, tokens, carry}``) and the refusals of
  anything else;
- what a valid result means: ``done`` (outputs present), ``resume`` (a nonempty message, at most
  ``max_rounds`` times) and ``closed`` (terminal: once closed, no later call is made).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictInt, StrictStr

from raidar_contract.package.manifest import MAX_STOP_ROUNDS

STOP_STATUSES = ("done", "resume", "closed")
RESULT_FIELDS = frozenset({"status", "message", "tokens", "carry"})
STOP_NOT_CALLED = "no stop call"
"""Why a package with no stop, or one already closed, is decided ``closed`` without a call."""
CLOSED_BY_PACKAGE = "closed by the package"
RESUME_REQUESTED = "requested"
RESUME_BEYOND_BOUND = "resume beyond max_rounds"
DONE_REPORTED = "reported"


class CallContext(BaseModel):
    """The JSON a package call reads (``RAIDAR_STAGE_CONTEXT``).

    ``round`` counts the ordinary resumes so far, so it never exceeds ``MAX_STOP_ROUNDS``.
    ``base`` exists only in a gate call's root-owned copy.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    task: StrictStr
    params: dict[str, JsonValue]
    round: StrictInt = Field(ge=0, le=MAX_STOP_ROUNDS)
    carry: dict[str, JsonValue]
    state: StrictStr | None
    outputs: dict[str, StrictStr]
    base: StrictStr | None = None

    def document(self) -> dict[str, JsonValue]:
        """The context file's content: ``base`` only when set."""
        return self.model_dump(
            mode="json",
            exclude_none=False,
            exclude={"base"} if self.base is None else set(),
        )


@dataclass(frozen=True)
class StopResult:
    """A valid stop result."""

    status: str
    message: str
    tokens: dict[str, str]
    carry: dict[str, JsonValue]


@dataclass(frozen=True)
class StopStep:
    """What a valid result means for one package: ``unusable`` carries why ``done`` is refused."""

    action: Literal["done", "resume", "closed", "unusable"]
    reason: str


def _document(stdout: bytes) -> dict[str, Any]:
    """The JSON object in ``stdout`` with exactly the stop result's fields."""
    try:
        data = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f"stop output is not JSON: {error}") from error
    if not isinstance(data, dict) or set(data) != RESULT_FIELDS:
        raise ValueError("stop output must have exactly status, message, tokens and carry")
    return data


def _check_tokens(tokens: object, declared: tuple[str, ...]) -> None:
    if not isinstance(tokens, dict) or not all(isinstance(v, str) for v in tokens.values()):
        raise ValueError("stop tokens must map names to strings")
    if set(tokens) - set(declared):
        raise ValueError(f"stop returned undeclared tokens {sorted(set(tokens) - set(declared))}")


def parse_stop_result(stdout: bytes, declared: tuple[str, ...]) -> StopResult:
    """The stop result in ``stdout``; ``ValueError`` names what makes it unusable."""
    data = _document(stdout)
    status, message, tokens, carry = (
        data["status"],
        data["message"],
        data["tokens"],
        data["carry"],
    )
    if status not in STOP_STATUSES:
        raise ValueError(f"stop status {status!r} is not done, resume or closed")
    if not isinstance(message, str):
        raise ValueError("stop message must be a string")
    _check_tokens(tokens, declared)
    if not isinstance(carry, dict):
        raise ValueError("stop carry must be a JSON object")
    if status == "resume" and not message.strip():
        raise ValueError("a resume needs a nonempty message")
    return StopResult(status, message, tokens, carry)


def judge_stop_result(
    result: StopResult, *, rounds: int, max_rounds: int, outputs_problem: str | None
) -> StopStep:
    """What a valid ``result`` decides after ``rounds`` ordinary resumes.

    Only ``done`` owes outputs (``outputs_problem`` is why they are unusable, if they are): a
    resume or close with outputs absent is still valid. A resume beyond ``max_rounds`` closes.
    """
    if result.status == "closed":
        return StopStep("closed", CLOSED_BY_PACKAGE)
    if result.status == "resume":
        if rounds >= max_rounds:
            return StopStep("closed", RESUME_BEYOND_BOUND)
        return StopStep("resume", RESUME_REQUESTED)
    if outputs_problem is not None:
        return StopStep("unusable", outputs_problem)
    return StopStep("done", DONE_REPORTED)
