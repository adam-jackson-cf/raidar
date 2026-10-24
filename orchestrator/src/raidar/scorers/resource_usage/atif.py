"""Read Harbor ATIF trajectories (``<trace_dir>/trajectory.json``) as ordered sessions.

Harbor owns the format (plan N4): the file is validated with Harbor's
``Trajectory`` model, so a document Harbor would reject is unreadable here
too. A step's trace is one ATIF document. Agents that run several sessions in
one step (the review loop, sub-agents, sub-agents of sub-agents) embed them
under ``subagent_trajectories`` at any depth and point at them from a parent
step's observation (``subagent_trajectory_ref``).

``Trajectory.steps`` flattens the document into its sessions' steps in
execution order, recursively: a referenced session is placed where its
parent refers to it, and embedded sessions nobody refers to follow their
parent in listed order. Harbor's own walker (``harbor.utils.trajectory_utils``)
also visits every session, but places sub-agents after their parent, which
loses the order the process checks need. Every session belongs to the step,
so its usage and actions count for the arm that ran it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harbor.models.trajectories import Step as HarborStep
from harbor.models.trajectories import Trajectory as HarborTrajectory
from pydantic import ValidationError

TRAJECTORY_FILE = "trajectory.json"
AGENT_SOURCE = "agent"


class TrajectoryError(ValueError):
    """The stored trajectory cannot be read as ATIF."""


@dataclass(frozen=True)
class AtifStep:
    """One ATIF step of one session."""

    session: str
    step_id: object
    source: str
    message: str
    tool_calls: tuple[Mapping[str, Any], ...]
    model_turn: bool = False


@dataclass(frozen=True)
class Trajectory:
    """A step's trace file and its sessions' steps in execution order."""

    path: Path
    steps: tuple[AtifStep, ...]

    @property
    def agent_turns(self) -> int:
        """Model turns: agent steps that called the model, over every session.

        An agent step with ``llm_call_count == 0`` is a deterministic dispatch
        (ATIF v1.7), not a turn.
        """
        return sum(1 for step in self.steps if step.model_turn)


def _message(step: HarborStep) -> str:
    if isinstance(step.message, str):
        return step.message
    return "\n".join(part.text for part in step.message if part.text)


def _step(session: str, step: HarborStep) -> AtifStep:
    return AtifStep(
        session=session,
        step_id=step.step_id,
        source=step.source,
        message=_message(step),
        tool_calls=tuple(
            {
                "tool_call_id": call.tool_call_id,
                "function_name": call.function_name,
                "arguments": call.arguments,
            }
            for call in step.tool_calls or ()
        ),
        model_turn=step.source == AGENT_SOURCE and step.llm_call_count != 0,
    )


def _refs(step: HarborStep) -> Iterator[str]:
    for result in step.observation.results if step.observation else ():
        for ref in result.subagent_trajectory_ref or ():
            if ref.trajectory_id is not None:
                yield ref.trajectory_id


def _walk(document: HarborTrajectory, session: str) -> Iterator[AtifStep]:
    """One session's steps, with its embedded sessions (at any depth) in place."""
    embedded = {
        sub.trajectory_id: sub for sub in document.subagent_trajectories or () if sub.trajectory_id
    }
    placed: set[str] = set()
    for step in document.steps:
        yield _step(session, step)
        for ref in _refs(step):
            if ref in embedded and ref not in placed:
                placed.add(ref)
                yield from _walk(embedded[ref], ref)
    for ref, sub in embedded.items():
        if ref not in placed:
            yield from _walk(sub, ref)


def _json_invalid(exc: ValidationError) -> bool:
    return any(error["type"] == "json_invalid" for error in exc.errors())


def load_trajectory(trace_dir: Path | None) -> Trajectory | None:
    """The step's ATIF trajectory, or None when the trace has none.

    Raises ``TrajectoryError`` for a file that exists but is not valid ATIF.
    """
    if trace_dir is None or not (trace_dir / TRAJECTORY_FILE).is_file():
        return None
    path = trace_dir / TRAJECTORY_FILE
    try:
        document = HarborTrajectory.model_validate_json(path.read_bytes())
    except ValidationError as exc:
        kind = "not valid JSON" if _json_invalid(exc) else "not an ATIF trajectory"
        raise TrajectoryError(f"{path} is {kind}: {exc}") from exc
    session = document.trajectory_id or document.session_id or "main"
    return Trajectory(path=path, steps=tuple(_walk(document, session)))
