"""Read one step's agent-log evidence for discovery probes (F5).

Harbor archives each step's agent logs to ``steps/<name>/agent/`` on the host.
Probes read only that directory:

- ``raidar-components.json``: workspace state recorded by the per-step
  ``setup.sh`` just before the agent started;
- ``raidar-components/<slug>.<channel>.jsonl``: logs written by component shims
  (stop-hook wrapper, MCP handshake recorder);
- Codex rollouts (``sessions/**/rollout-*.jsonl``) and Claude Code output
  (``claude-code.txt`` stream-json, ``sessions/projects/**.jsonl`` transcripts).

Every lookup is recursive so a ``workflow`` wrapper that files stage logs in
sub-directories is still covered.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

COMPONENT_REPORT = "raidar-components.json"
RESET_REPORT = "raidar-reset.json"
CLAUDE_STREAM = "claude-code.txt"
CODEX_STREAM = "codex.txt"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSON objects from a JSONL file; non-JSON lines (warnings) are skipped."""
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _message_text(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [part.get("text", "") for part in content if isinstance(part, dict)]
    return "\n".join(part for part in parts if isinstance(part, str))


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


@dataclass(frozen=True)
class StepEvidence:
    agent_dir: Path

    def _files(self, pattern: str) -> Iterator[Path]:
        return (path for path in sorted(self.agent_dir.rglob(pattern)) if path.is_file())

    def json_reports(self, name: str) -> list[dict[str, Any]]:
        reports = []
        for path in self._files(name):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if isinstance(payload, dict):
                reports.append(payload)
        return reports

    @cached_property
    def component_reports(self) -> list[dict[str, Any]]:
        return self.json_reports(COMPONENT_REPORT)

    @cached_property
    def reset_reports(self) -> list[dict[str, Any]]:
        return self.json_reports(RESET_REPORT)

    @cached_property
    def workspace_files(self) -> dict[str, str | None]:
        """Workspace overlay paths recorded at step start: sha256, or ``None`` when absent."""
        recorded: dict[str, str | None] = {}
        for report in self.component_reports:
            files = report.get("workspace_files")
            if not isinstance(files, dict):
                continue
            for path, sha in files.items():
                recorded.setdefault(str(path), sha if isinstance(sha, str) else None)
        return recorded

    def component_log(self, slug: str, channel: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in self._files(f"{slug}.{channel}.jsonl"):
            records.extend(read_jsonl(path))
        return records

    @cached_property
    def codex_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in self._files("rollout-*.jsonl"):
            records.extend(read_jsonl(path))
        return records

    @cached_property
    def codex_messages(self) -> list[tuple[str, str]]:
        """(role, text) of every developer/user/assistant message in Codex rollouts."""
        messages = []
        for record in self.codex_records:
            payload = record.get("payload")
            if (
                record.get("type") == "response_item"
                and isinstance(payload, dict)
                and payload.get("type") == "message"
            ):
                messages.append((str(payload.get("role")), _message_text(payload)))
        return messages

    @cached_property
    def codex_turn_contexts(self) -> list[dict[str, Any]]:
        return [
            record["payload"]
            for record in self.codex_records
            if record.get("type") == "turn_context" and isinstance(record.get("payload"), dict)
        ]

    @cached_property
    def claude_events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for path in self._files(CLAUDE_STREAM):
            events.extend(read_jsonl(path))
        return events

    @cached_property
    def claude_init_events(self) -> list[dict[str, Any]]:
        return [
            event
            for event in self.claude_events
            if event.get("type") == "system" and event.get("subtype") == "init"
        ]

    @cached_property
    def claude_transcript_text(self) -> str:
        """Every string value in Claude session transcripts (JSON-decoded)."""
        texts: list[str] = []
        for path in self._files("*.jsonl"):
            if "projects" in path.relative_to(self.agent_dir).parts:
                for record in read_jsonl(path):
                    texts.extend(_strings(record))
        return "\n".join(texts)

    def codex_text(self, *roles: str) -> str:
        return "\n".join(text for role, text in self.codex_messages if not roles or role in roles)
