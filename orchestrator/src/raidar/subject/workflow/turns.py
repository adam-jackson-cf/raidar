"""What one agent turn leaves behind: its record, its session evidence and the phase's usage.

The runner drives turns; this module owns their evidence. A ``Turn`` is the record of one prompt
sent to the native agent, ``Run`` the turns of one phase (their session continuity and summed
tokens), and the ``native_*`` functions read a turn's isolation, session and usage facts from the
native agent behind the harness (``raidar.subject.agent.protocol``).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from harbor.models.agent.context import AgentContext
from raidar_contract.agent_records import ZERO, Tokens

from raidar.subject.agent.protocol import NativeAgent

WORKFLOW_DIR = "workflow"
TRAJECTORY_FILE = "trajectory.json"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def session_id(turn_dir: Path) -> str | None:
    path = turn_dir / TRAJECTORY_FILE
    try:
        value = json.loads(path.read_text()).get("session_id")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and value else None


@dataclass
class Turn:
    stage: str
    kind: str
    index: int
    resumed: bool
    prompt_sha256: str
    started_at: str
    started: float
    finished_at: str | None = None
    seconds: float | None = None
    error: str | None = None
    settle_error: str | None = None
    session_id: str | None = None
    session_total: Tokens | None = None
    tokens: Tokens | None = None
    harness_isolation: dict[str, Any] | None = None
    agent_process: dict[str, str | None] | None = None
    session_audit: dict[str, Any] | None = None
    session_store: tuple[str, ...] = ()
    integrity: list[dict[str, str]] = field(default_factory=list)

    @property
    def log_dir(self) -> str:
        return f"{WORKFLOW_DIR}/{self.stage}-{self.index}"

    def stop(self, error: str | None = None) -> None:
        self.finished_at = now()
        self.seconds = round(time.monotonic() - self.started, 3)
        self.error = error

    def record(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "kind": self.kind,
            "turn": self.index,
            "log_dir": self.log_dir,
            "session_id": self.session_id,
            "resumed": self.resumed,
            "prompt_sha256": self.prompt_sha256,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "seconds": self.seconds,
            "error": self.error,
            "settle_error": self.settle_error,
            "session_total": self.session_total.record() if self.session_total else None,
            "tokens": self.tokens.record() if self.tokens else None,
            "harness_isolation": self.harness_isolation,
            "agent_process": self.agent_process,
            "session_audit": self.session_audit,
            "session_store": list(self.session_store),
            "integrity": self.integrity,
        }


@dataclass
class Run:
    turns: list[Turn] = field(default_factory=list)

    def previous(self, turn: Turn) -> Tokens | None:
        """The preceding normalized total of this session, if one was measured."""
        return next(
            (
                item.session_total
                for item in reversed(self.turns)
                if item is not turn and item.session_id == turn.session_id
            ),
            None,
        )

    def usage(self) -> Tokens | None:
        """The phase's tokens (sum of turn deltas); ``None`` when any turn's is unknown."""
        total = ZERO
        for turn in self.turns:
            if turn.tokens is None:
                return None
            total = total.plus(turn.tokens)
        return total if self.turns else None


def native_session_store(inner: object) -> tuple[str, ...]:
    """What must stay in place, relative to the agent log root, for the next turn to resume."""
    return cast(NativeAgent, inner).session_store()


def native_observe(turn: Turn, inner: object) -> None:
    """Read the turn's isolation, process and session-store facts from the native agent."""
    native = cast(NativeAgent, inner)
    turn.harness_isolation = native.harness_isolation
    turn.agent_process = native.process_facts()
    turn.session_store = native.session_store()


def native_account(turn: Turn, run: Run, inner: object, context: AgentContext) -> None:
    """Set the turn's token delta and session total, subtracting the session's earlier total."""
    previous = run.previous(turn)
    if not turn.resumed or previous is not None:
        turn.tokens, turn.session_total = cast(NativeAgent, inner).accounting(context, previous)


def native_audit(turn: Turn, inner: object, turn_dir: Path) -> None:
    """Set the turn's normalized session audit from the harness's own records."""
    turn.session_audit = cast(NativeAgent, inner).session_audit(turn_dir)


def native_last_message(inner: object, turn_dir: Path) -> str | None:
    """The turn's own final text (``None`` when the turn said nothing new)."""
    return cast(NativeAgent, inner).last_message(turn_dir)
