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
from pathlib import Path
from typing import Any, Protocol, cast

from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from raidar_contract.agent_records import Tokens

from raidar.subject.agent.records import Credentials, StatePath


class IsolationError(RuntimeError):
    """The container or the agent's configuration would let host settings reach the harness."""


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


def tokens_of(context: AgentContext) -> Tokens | None:
    """The token counts of ``context``; ``None`` when the harness recorded no count."""
    counts = (context.n_input_tokens, context.n_cache_tokens, context.n_output_tokens)
    if any(count is None for count in counts):
        return None
    n_input, n_cached, n_output = cast(tuple[int, int, int], counts)
    return Tokens(n_input, n_cached, n_output, context.cost_usd)


def cumulative_accounting(
    context: AgentContext, previous: Tokens | None
) -> tuple[Tokens | None, Tokens | None]:
    """``(delta, total)`` when the context holds the session's running total."""
    total = tokens_of(context)
    if total is None:
        return None, None
    return (total if previous is None else total.minus(previous)), total


def per_turn_accounting(
    context: AgentContext, previous: Tokens | None
) -> tuple[Tokens | None, Tokens | None]:
    """``(delta, total)`` when the context holds only this turn's usage."""
    delta = tokens_of(context)
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
