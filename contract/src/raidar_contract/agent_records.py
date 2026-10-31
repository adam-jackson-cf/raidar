"""Record shapes of the native-agent protocol: pure data, no Harbor types.

The orchestrator's ``raidar.subject.agent.protocol`` holds the Harbor-typed boundary (the
``NativeAgent`` protocol, the accounting functions that read an ``AgentContext``); these are the
shapes it and the adapters exchange: credentials, reset state paths, token counts and the
normalized ``session_audit`` of one turn.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class Credentials:
    """Whether a host environment can authenticate a harness.

    ``issue`` is why it cannot (``None`` when it can). ``session_file`` is where a file-backed
    session login is kept (``None`` for a harness with none), ``session_present`` whether that
    file holds a login and ``session_used`` whether the harness would authenticate from it.
    ``mode_env`` names the variable that selects the authentication source, if any.
    """

    issue: str | None
    session_file: Path | None = None
    session_present: bool = False
    session_used: bool = False
    mode_env: str | None = None


@dataclass(frozen=True, slots=True)
class StatePath:
    """One harness-persisted location the per-step reset returns to the harness baseline.

    ``target`` is a shell expression the reset script evaluates (``$HOME_DIR``, ``$LOGS_DIR``),
    ``baseline`` the entry of the harness baseline it is restored from (removed when the baseline
    has none) and ``kind`` whether it is a directory or a file.
    """

    target: str
    baseline: str
    kind: Literal["dir", "file"]


@dataclass(frozen=True)
class Tokens:
    """A session's token counts (cumulative) or one turn's share of them (a delta)."""

    input: int
    cached: int
    output: int
    cost_usd: float | None

    def minus(self, earlier: Tokens) -> Tokens:
        cost = (
            None
            if self.cost_usd is None or earlier.cost_usd is None
            else round(self.cost_usd - earlier.cost_usd, 10)
        )
        return Tokens(
            self.input - earlier.input,
            self.cached - earlier.cached,
            self.output - earlier.output,
            cost,
        )

    def plus(self, other: Tokens) -> Tokens:
        cost = (
            None
            if self.cost_usd is None or other.cost_usd is None
            else self.cost_usd + other.cost_usd
        )
        return Tokens(
            self.input + other.input,
            self.cached + other.cached,
            self.output + other.output,
            cost,
        )

    def record(self) -> dict[str, Any]:
        return {
            "input": self.input,
            "cached": self.cached,
            "output": self.output,
            "cost_usd": self.cost_usd,
        }


ZERO = Tokens(0, 0, 0, 0.0)


def source_record(turn_dir: Path, path: Path) -> dict[str, str]:
    """One audited source file: its path relative to the turn directory and its digest."""
    data = path.read_bytes()
    relative = path.relative_to(turn_dir).as_posix()
    return {"path": relative, "sha256": hashlib.sha256(data).hexdigest()}


def session_audit_record(
    harness: str,
    *,
    version: str | None = None,
    records: int = 0,
    sources: list[dict[str, str]] | None = None,
    skill_roots: list[str] | None = None,
    instructions: list[dict[str, str | None]] | None = None,
    messages: list[dict[str, str]] | None = None,
    violations: list[str] | None = None,
    unmeasured: list[str] | None = None,
) -> dict[str, Any]:
    """The normalized ``session_audit`` of one turn (every key always present).

    ``skill_roots`` are the skill directories the harness recorded, ``instructions`` the
    instruction files it merged (``path`` when recorded, ``text``), ``messages`` the SHA-256 of
    each developer or system message it recorded, ``records`` the number of session rows read and
    ``sources`` the audited files (turn-relative ``path`` and ``sha256``), so a check can tell a
    removed source from a clean one. ``violations`` are what the records themselves show reached
    the agent; ``unmeasured`` is what they could not show.
    """
    return {
        "harness": harness,
        "version": version,
        "records": records,
        "sources": sources or [],
        "skill_roots": sorted(set(skill_roots or [])),
        "instructions": instructions or [],
        "messages": messages or [],
        "violations": list(dict.fromkeys(violations or [])),
        "unmeasured": list(dict.fromkeys(unmeasured or [])),
    }
