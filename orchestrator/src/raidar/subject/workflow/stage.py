"""The stage interface (design section 5.1).

Every stage type implements the same five parts, and all of them happen inside the
checkpoint's one primary session:

1. **settings**: the workflow's own settings for the type, given when the stage is built;
2. **before the agent starts** (``prepare``): workspace setup and tools;
3. **prompt fragment** (``fragment``): its piece of the checkpoint's one prompt. ``build``'s
   fragment is the frame: it holds ``%%%TASK%%%`` and every other enabled stage's ``token``,
   which ``compose`` fills;
4. **checks when the agent stops** (``check``): a message resumes the same session, ``None``
   lets the checkpoint end as far as this stage is concerned. A stage bounds its own resumes;
5. **outputs** (``close`` then ``record``): what it leaves once the checkpoint ends, normally or
   cut (``cut`` names why: ``deadline`` for a cancelled phase, ``turn_error`` for a turn that
   failed), and the record the runner keeps in ``workflow.json`` under the stage's name.
   ``instructions`` lists the step records holding text the stage placed in the workspace's
   instruction files, so the isolation check can tell it from text from elsewhere.

``build`` (``raidar.subject.workflow.build``) has nothing to set up; with structured replies it
checks each turn's reply. A check returns a ``Resume`` (the message and the kind of turn it
starts: ``gate_fix``, ``rework``, ``repair``, ``define``; the checkpoint's first prompt is
``first_build``). ``Checkpoint.turns`` holds every finished turn's kind and seconds and
``Checkpoint.deadline`` the time the agent phase is cut at, for a stage that must fit a turn in the
time left. ``decorate`` lets a stage add to the prompt of a turn (the reply's shape).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from harbor.environments.base import BaseEnvironment

from raidar.subject.workflow.definition import TASK_TOKEN, StageType

DEADLINE = "deadline"
TURN_ERROR = "turn_error"
FIRST_BUILD = "first_build"
GATE_FIX = "gate_fix"
REWORK = "rework"
REPAIR = "repair"
DEFINE_ROUND = "define"
"""The kinds of turn: the checkpoint's first prompt, and what a stage's check resumes it with."""


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


class Stage(ABC):
    """One enabled stage type with the workflow's settings for it (module docstring)."""

    name: ClassVar[StageType]
    token: ClassVar[str | None]
    instructions: list[str]

    async def prepare(self, checkpoint: Checkpoint) -> None:
        """Set the stage up before the agent starts."""
        del checkpoint

    @abstractmethod
    def fragment(self) -> str:
        """The stage's piece of the checkpoint's prompt."""

    async def check(self, checkpoint: Checkpoint, after: str) -> Resume | None:
        """Check the agent's work after the turn logged at ``after``; a ``Resume`` continues it."""
        del checkpoint, after
        return None

    def decorate(self, kind: str, prompt: str) -> str:
        """The prompt a turn of ``kind`` is sent, from the one the stage or runner built."""
        del kind
        return prompt

    async def close(self, checkpoint: Checkpoint, cut: str | None) -> None:
        """Leave the stage's outputs once the checkpoint ends (``cut``: why it was cut)."""
        del checkpoint, cut

    def record(self) -> dict[str, Any] | None:
        """What the stage did this checkpoint, for ``workflow.json``."""
        return None


def compose(stages: Sequence[Stage], task: str) -> str:
    """The checkpoint's one prompt: build's frame with each stage's fragment, then the task.

    The task text goes in last, so a token inside the task is never filled.
    """
    frame = next(stage for stage in stages if stage.name == "build")
    prompt = frame.fragment()
    for stage in stages:
        if stage.token is not None:
            prompt = prompt.replace(stage.token, stage.fragment())
    return prompt.replace(TASK_TOKEN, task)
