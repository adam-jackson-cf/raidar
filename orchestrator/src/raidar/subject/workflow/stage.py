"""The checkpoint vocabulary the runner and the package runtime share (design section 5.1).

A checkpoint runs one primary session, one agent *turn* at a time. ``Checkpoint`` is where its
package runtime works: the sandbox, the step's host-side agent logs, the task text, the time the
agent phase is cut at (``deadline``) and every finished turn (``Spent``), for a check that must
fit a turn in the time left. A check returns a ``Resume`` (the message and the kind of turn it
starts: ``gate_fix``, ``rework`` or ``repair``; the checkpoint's first prompt is ``first_build``).
The runtime itself is ``raidar.subject.workflow.package_stage.PackageStage``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from harbor.environments.base import BaseEnvironment

DEADLINE = "deadline"
TURN_ERROR = "turn_error"
FIRST_BUILD = "first_build"
GATE_FIX = "gate_fix"
REWORK = "rework"
REPAIR = "repair"
"""The kinds of turn: the checkpoint's first prompt, and what a check resumes it with."""


@dataclass(frozen=True)
class Resume:
    """A check's message that resumes the session, and the kind of turn it starts."""

    message: str
    kind: str


@dataclass(frozen=True)
class Spent:
    """A finished turn's kind and seconds."""

    kind: str
    seconds: float


@dataclass(frozen=True)
class Checkpoint:
    """Where one checkpoint's stages work: the sandbox and the step's agent logs (host side).

    ``deadline`` is the monotonic time the agent phase is cut at (``None``: unbounded) and
    ``turns`` every finished turn so far, which the runner appends to.
    """

    environment: BaseEnvironment
    logs_dir: Path
    task: str
    deadline: float | None = None
    turns: list[Spent] = field(default_factory=list)
    session_paths: tuple[str, ...] = ()

    def remaining(self) -> float | None:
        """Seconds left before the deadline, or ``None`` when there is none."""
        return None if self.deadline is None else self.deadline - time.monotonic()

    def last_seconds(self, kind: str) -> float:
        """How long the last turn of ``kind`` took, the repairs that followed it included."""
        total = 0.0
        for spent in reversed(self.turns):
            if spent.kind == REPAIR:
                total += spent.seconds
            elif spent.kind == kind:
                return total + spent.seconds
            else:
                total = 0.0
        return 0.0

    def expected_seconds(self, kind: str) -> float | None:
        """How long the next turn of ``kind`` is expected to take, for a turn that must fit.

        The last turn of its kind. The first rework has none, so it is expected to take as long as
        the checkpoint's first build turn (the same agent, resumed, on the same task), never 0.
        ``None`` means the estimate is unavailable (no first build turn recorded): the caller
        must not schedule the turn, and no number is invented.
        """
        if kind == REWORK and not any(spent.kind == REWORK for spent in self.turns):
            if not any(spent.kind == FIRST_BUILD for spent in self.turns):
                return None
            return self.last_seconds(FIRST_BUILD)
        return self.last_seconds(kind)
