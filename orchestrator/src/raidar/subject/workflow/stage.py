"""The stage interface (design section 5.1) and the ``build`` stage type.

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

``build`` has nothing to set up and no checks: its outputs are the runner's per-turn records.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from harbor.environments.base import BaseEnvironment

from raidar.subject.workflow.definition import TASK_TOKEN, BuildSettings, StageType

DEADLINE = "deadline"
TURN_ERROR = "turn_error"


@dataclass(frozen=True)
class Checkpoint:
    """Where one checkpoint's stages work: the sandbox and the step's agent logs (host side)."""

    environment: BaseEnvironment
    logs_dir: Path


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

    async def check(self, checkpoint: Checkpoint, after: str) -> str | None:
        """Check the agent's work after the turn logged at ``after``; a message resumes it."""
        del checkpoint, after
        return None

    async def close(self, checkpoint: Checkpoint, cut: str | None) -> None:
        """Leave the stage's outputs once the checkpoint ends (``cut``: why it was cut)."""
        del checkpoint, cut

    def record(self) -> dict[str, Any] | None:
        """What the stage did this checkpoint, for ``workflow.json``."""
        return None


class Build(Stage):
    name = "build"
    token = None

    def __init__(self, settings: BuildSettings) -> None:
        self.settings = settings
        self.instructions = []

    def fragment(self) -> str:
        return self.settings.prompt


def compose(stages: Sequence[Stage], task: str) -> str:
    """The checkpoint's one prompt: build's frame with each stage's fragment, then the task.

    The task text goes in last, so a token inside the task is never filled.
    """
    frame, *others = stages
    prompt = frame.fragment()
    for stage in others:
        assert stage.token is not None  # only build frames
        prompt = prompt.replace(stage.token, stage.fragment())
    return prompt.replace(TASK_TOKEN, task)
