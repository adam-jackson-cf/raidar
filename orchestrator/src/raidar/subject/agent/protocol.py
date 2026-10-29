"""The boundary every native agent (a Harbor installed agent run by Raidar) implements.

The workflow runner drives one native agent per turn and knows nothing of the harness behind it:
what must differ between harnesses is behind these six methods and two classmethods, implemented
by ``raidar.subject.agent.adapters.isolated_codex.IsolatedCodex`` and
``raidar.subject.agent.adapters.isolated_claude.IsolatedClaudeCode`` (private to the adapters:
callers reach a native agent through ``raidar.subject.agent.native``).

- ``isolate`` prepares the turn's home, flags and environment on every dispatch (first turn and
  resumed) and sets ``harness_isolation`` (what was applied, or ``None`` before the first call).
- ``process_facts`` is what the agent process ran with (user, ``PATH``, ``TMPDIR``), read by the
  same shell start-up as the command, for the trial's parity record.
- ``accounting`` turns the turn's populated ``AgentContext`` into ``(turn delta, session total)``:
  a harness whose context holds a running session total subtracts the previous total, one whose
  context holds only the turn's usage adds it to the previous total. Usage the harness did not
  record is unknown (``None``), never zero.
- ``last_message`` is the turn's own final text, read from this turn's records only: a resumed
  turn that said nothing new has no last message, it never inherits the previous turn's reply.
- ``session_store`` names what must stay in place, relative to the agent log root, for the next
  turn to resume. The runner copies logs and never moves or deletes these.
- ``session_audit`` is what the isolation check needs, normalized from the harness's own
  records; see ``session_audit_record``. Records that are missing or cannot show something are
  ``unmeasured``, never a pass.
- ``credentials`` (class-level, no instance) says whether a host environment can authenticate the
  harness and where a file-backed session login is kept; see ``Credentials``.
- ``state_paths`` (class-level) lists the harness state the per-step reset returns to baseline;
  see ``StatePath``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class IsolationError(RuntimeError):
    """The container or the agent's configuration would let host settings reach the harness."""


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

    @classmethod
    def of(cls, context: AgentContext) -> Tokens | None:
        counts = (context.n_input_tokens, context.n_cache_tokens, context.n_output_tokens)
        if any(count is None for count in counts):
            return None
        n_input, n_cached, n_output = cast(tuple[int, int, int], counts)
        return cls(n_input, n_cached, n_output, context.cost_usd)

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
            self.input + other.input, self.cached + other.cached, self.output + other.output, cost
        )

    def record(self) -> dict[str, Any]:
        return {
            "input": self.input,
            "cached": self.cached,
            "output": self.output,
            "cost_usd": self.cost_usd,
        }


ZERO = Tokens(0, 0, 0, 0.0)


class NativeAgent(Protocol):
    """A Harbor installed agent that Raidar isolates, resumes, meters and audits."""

    harness_isolation: dict[str, Any] | None

    async def isolate(self, environment: BaseEnvironment) -> None: ...

    def process_facts(self) -> dict[str, str | None] | None: ...

    def accounting(
        self, context: AgentContext, previous: Tokens | None
    ) -> tuple[Tokens | None, Tokens | None]: ...

    def last_message(self, turn_dir: Path) -> str | None: ...

    def session_store(self) -> tuple[str, ...]: ...

    def session_audit(self, turn_dir: Path) -> dict[str, Any]: ...

    @classmethod
    def credentials(cls, env: Mapping[str, str]) -> Credentials: ...

    @classmethod
    def state_paths(cls) -> tuple[StatePath, ...]: ...


def cumulative_accounting(
    context: AgentContext, previous: Tokens | None
) -> tuple[Tokens | None, Tokens | None]:
    """``(delta, total)`` when the context holds the session's running total."""
    total = Tokens.of(context)
    if total is None:
        return None, None
    return (total if previous is None else total.minus(previous)), total


def per_turn_accounting(
    context: AgentContext, previous: Tokens | None
) -> tuple[Tokens | None, Tokens | None]:
    """``(delta, total)`` when the context holds only this turn's usage."""
    delta = Tokens.of(context)
    if delta is None:
        return None, None
    return delta, (delta if previous is None else previous.plus(delta))


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def unread_json_lines(path: Path, understood: Callable[[dict[str, Any]], bool]) -> int:
    """Non-blank lines of a JSON-lines record that are not JSON objects the adapter understands.

    ``understood`` accepts only the record shapes the adapter's parser reads. An audit that skips
    a line (not JSON, or valid JSON in an unsupported shape such as ``{"foo": "bar"}``) never saw
    what it held, so it reports the count as ``unmeasured``, never as a clean record.
    """
    unread = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            unread += 1
            continue
        unread += not (isinstance(record, dict) and understood(record))
    return unread


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
