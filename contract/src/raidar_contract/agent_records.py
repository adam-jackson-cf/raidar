"""Transport-neutral native-agent token accounting and normalized audit records.

Credential handling and shell-based reset paths belong to each runtime, not this contract.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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


@dataclass(frozen=True, kw_only=True)
class SessionAudit:
    """The normalized ``session_audit`` of one turn; ``record`` always holds every key.

    ``skill_roots`` are the skill directories the harness recorded, ``instructions`` the
    instruction files it merged (``path`` when recorded, ``text``), ``messages`` the SHA-256 of
    each developer or system message it recorded, ``records`` the number of session rows read and
    ``sources`` the audited files (turn-relative ``path`` and ``sha256``), so a check can tell a
    removed source from a clean one. ``violations`` are what the records themselves show reached
    the agent; ``unmeasured`` is what they could not show.
    """

    harness: str
    version: str | None = None
    records: int = 0
    sources: list[dict[str, str]] = field(default_factory=list)
    skill_roots: list[str] = field(default_factory=list)
    instructions: list[dict[str, str | None]] = field(default_factory=list)
    messages: list[dict[str, str]] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    unmeasured: list[str] = field(default_factory=list)

    def record(self) -> dict[str, Any]:
        return {
            "harness": self.harness,
            "version": self.version,
            "records": self.records,
            "sources": list(self.sources),
            "skill_roots": sorted(set(self.skill_roots)),
            "instructions": list(self.instructions),
            "messages": list(self.messages),
            "violations": list(dict.fromkeys(self.violations)),
            "unmeasured": list(dict.fromkeys(self.unmeasured)),
        }
