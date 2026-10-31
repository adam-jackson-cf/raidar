"""What Claude Code recorded of one turn: its final text, its usage and the session audit.

Two records, both from claude itself. The turn's ``--output-format=stream-json`` output
(``claude-code.txt``, rewritten by every turn) holds the ``system``/``init`` event (the skills,
plugins and MCP servers loaded and the version) and one ``result`` envelope with the turn's final
text and its usage. The session transcripts (``sessions/projects/<cwd>/*.jsonl`` under
``CLAUDE_CONFIG_DIR``, which accumulate over every turn of the session) hold the instruction
files merged into the context (``Contents of <path> (<kind>):`` blocks), the directory of each
skill that was invoked (``Base directory for this skill:``) and the ``system`` events.

Claude does not record where a listed skill came from, so skill roots are only what an invoked
skill's base directory shows; a turn whose init lists skills is ``unmeasured`` for roots.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any, cast

from raidar_contract.agent_records import Tokens, session_audit_record, source_record

from raidar.subject.agent.pricing import price_tokens
from raidar.subject.agent.protocol import digest, unread_json_lines

HARNESS = "claude-code"
STREAM_FILE = "claude-code.txt"
SESSIONS_DIR = "sessions"
INSTRUCTION_BLOCK = re.compile(
    r"Contents of (?P<path>\S+) \((?P<kind>[^)\n]*)\):\n\n(?P<text>.*?)"
    r"(?=\n\nContents of \S+ \(|</system-reminder>|\Z)",
    re.DOTALL,
)
SKILL_BASE = re.compile(r"Base directory for this skill: (?P<dir>\S+)")
TRANSCRIPT_TYPES = frozenset(
    {"user", "assistant", "system", "summary", "attachment", "file-history-snapshot"}
    | {"queue-operation", "progress"}
)
STREAM_TYPES = frozenset(
    {"system", "assistant", "user", "result", "stream_event", "rate_limit_event"}
)


def _understood_event(event: dict[str, Any]) -> bool:
    """A transcript event of a known type with the container this audit reads its text from.

    ``user``/``assistant`` events carry a ``message`` object whose content is text or a list of
    objects, ``attachment`` events an ``attachment`` object and ``system`` events text content;
    any other shape, or a type from a newer version, could hold instructions and is unread.
    """
    kind = event.get("type")
    if kind in ("user", "assistant"):
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        return isinstance(content, str) or (
            isinstance(content, list) and all(isinstance(part, dict) for part in content)
        )
    if kind == "attachment":
        return isinstance(event.get("attachment"), dict)
    if kind == "system":
        return isinstance(event.get("content"), str)
    return kind in TRANSCRIPT_TYPES


def _understood_stream_event(event: dict[str, Any]) -> bool:
    return event.get("type") in STREAM_TYPES


def _events(path: Path) -> Iterator[dict[str, Any]]:
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _stream_events(turn_dir: Path) -> list[dict[str, Any]] | None:
    path = turn_dir / STREAM_FILE
    return list(_events(path)) if path.is_file() else None


def _result(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    found = [event for event in events if event.get("type") == "result"]
    return found[-1] if found else None


def claude_last_message(turn_dir: Path) -> str | None:
    """The ``result`` text of the turn's own stream; ``None`` without a successful envelope."""
    events = _stream_events(turn_dir)
    envelope = _result(events) if events else None
    if envelope is None or envelope.get("is_error") or envelope.get("subtype") != "success":
        return None
    text = envelope.get("result")
    return text if isinstance(text, str) else None


def _priced(envelope: dict[str, Any]) -> float | None:
    """The envelope's spend from the shared pricing table, per model of its ``modelUsage``.

    The CLI's own ``total_cost_usd`` is a different table's figure and is never read. A missing
    or incomplete ``modelUsage``, or an unpriced model, gives ``None`` (unavailable).
    """
    models = envelope.get("modelUsage")
    if not isinstance(models, dict) or not models:
        return None
    fields = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
    total = 0.0
    for model, usage in models.items():
        if not isinstance(usage, dict) or not all(isinstance(usage.get(k), int) for k in fields):
            return None
        read, written = usage["cacheReadInputTokens"], usage["cacheCreationInputTokens"]
        cost = price_tokens(
            str(model),
            prompt=usage["inputTokens"] + read + written,
            completion=usage["outputTokens"],
            cache_read=read,
            cache_write=written,
        )
        if cost is None:
            return None
        total += cost
    return total


def _usage_tokens(envelope: dict[str, Any]) -> Tokens | None:
    usage = envelope.get("usage")
    if not isinstance(usage, dict):
        return None
    if not all(isinstance(usage.get(key), int) for key in ("input_tokens", "output_tokens")):
        return None
    read = usage.get("cache_read_input_tokens") or 0
    written = usage.get("cache_creation_input_tokens") or 0
    return Tokens(
        usage["input_tokens"] + read + written,
        read,
        usage["output_tokens"],
        _priced(envelope),
    )


def claude_turn_usage(turn_dir: Path) -> Tokens | None:
    """The turn's tokens from the ``result`` envelopes of its own stream (claude counts per call).

    Input is the prompt as Harbor counts it (input plus cache reads plus cache writes), cached is
    the cache reads. ``None`` when the stream holds no envelope or an envelope lacks its usage.
    """
    results = [e for e in _stream_events(turn_dir) or [] if e.get("type") == "result"]
    parts = [_usage_tokens(envelope) for envelope in results]
    if not parts or any(part is None for part in parts):
        return None
    total = Tokens(0, 0, 0, 0.0)
    for part in parts:
        total = total.plus(cast(Tokens, part))
    return total


def _transcripts(turn_dir: Path) -> list[Path]:
    return sorted((turn_dir / SESSIONS_DIR / "projects").rglob("*.jsonl"))


def _init_event(stream: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    return next(
        (e for e in stream or [] if e.get("type") == "system" and e.get("subtype") == "init"),
        None,
    )


def _version(init: dict[str, Any] | None) -> str | None:
    found = init.get("claude_code_version") if init else None
    return found if isinstance(found, str) else None


def _sources(turn_dir: Path, transcripts: list[Path], has_stream: bool) -> list[dict[str, str]]:
    sources = [source_record(turn_dir, path) for path in transcripts]
    if has_stream:
        sources.append(source_record(turn_dir, turn_dir / STREAM_FILE))
    return sources


def claude_session_audit(turn_dir: Path) -> dict[str, Any]:
    """The normalized audit of the turn's stream and the session transcripts (module docstring)."""
    stream = _stream_events(turn_dir)
    transcripts = _transcripts(turn_dir)
    roots: list[str] = []
    instructions: dict[tuple[str | None, str], dict[str, str | None]] = {}
    messages: list[dict[str, str]] = []
    violations: list[str] = []
    unmeasured: list[str] = []
    records = len(stream or [])
    init = _init_event(stream)
    version = _version(init)
    if init is None:
        unmeasured.append("no system init event in the claude stream")
    else:
        _init_findings(init, violations, unmeasured)
    stream_path = turn_dir / STREAM_FILE
    skipped = unread_json_lines(stream_path, _understood_stream_event) if stream is not None else 0
    if skipped:
        unmeasured.append(
            f"{skipped} line(s) of the claude stream are not JSON objects of a shape this audit "
            "reads: anything in them is unread"
        )
    if not transcripts:
        unmeasured.append("no session transcript recorded")
    for transcript in transcripts:
        unread = unread_json_lines(transcript, _understood_event)
        if unread:
            unmeasured.append(
                f"{unread} line(s) of the session transcript {transcript.name} are not JSON "
                "objects of a shape this audit reads: any instruction in them is unread"
            )
        for event in _events(transcript):
            records += 1
            version = version or _event_version(event)
            _transcript_findings(event, roots, instructions, messages)
    return session_audit_record(
        HARNESS,
        version=version,
        records=records,
        sources=_sources(turn_dir, transcripts, stream is not None),
        skill_roots=roots,
        instructions=list(instructions.values()),
        messages=messages,
        violations=violations,
        unmeasured=unmeasured,
    )


def _event_version(event: dict[str, Any]) -> str | None:
    found = event.get("version")
    return found if isinstance(found, str) else None


def _init_findings(init: dict[str, Any], violations: list[str], unmeasured: list[str]) -> None:
    plugins = [
        plugin
        for plugin in init.get("plugins") or []
        if not isinstance(plugin, dict) or plugin.get("path") != "builtin"
    ]
    if plugins:
        violations.append(f"claude loaded {len(plugins)} plugin(s)")
    servers = init.get("mcp_servers")
    if servers:
        violations.append(f"claude loaded {len(servers)} MCP server(s)")
    skills = init.get("skills")
    if skills:
        unmeasured.append(
            f"claude listed {len(skills)} skill(s) by name only: their roots are not recorded"
        )


def _transcript_findings(
    event: dict[str, Any],
    roots: list[str],
    instructions: dict[tuple[str | None, str], dict[str, str | None]],
    messages: list[dict[str, str]],
) -> None:
    if event.get("type") == "system" and isinstance(event.get("content"), str):
        messages.append({"role": "system", "sha256": digest(event["content"])})
    for text in _strings({key: event.get(key) for key in ("message", "attachment")}):
        for block in INSTRUCTION_BLOCK.finditer(text):
            key = (block["path"], block["text"])
            instructions[key] = {"path": block["path"], "text": block["text"]}
        roots.extend(str(PurePosixPath(base["dir"]).parent) for base in SKILL_BASE.finditer(text))
