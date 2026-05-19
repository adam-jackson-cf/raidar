"""Shared harness log loading helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _harness_event_stream_pointer(harness_dir: Path, harness: str) -> Path:
    if harness == "codex-cli":
        return harness_dir / "codex.txt"
    if harness == "claude-code":
        return harness_dir / "commands"
    if harness == "gemini":
        return harness_dir / "commands"
    if harness == "cursor":
        return harness_dir / "commands"
    if harness == "copilot":
        return harness_dir / "commands"
    if harness == "pi":
        return harness_dir / "commands"
    raise ValueError(f"Unsupported harness for artifact summary: {harness}")


def _read_jsonl_dicts(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries: list[dict] = []
    for line in path.read_text(errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def _extract_item_completed(entry: dict) -> dict | None:
    if entry.get("type") != "item.completed":
        return None
    item = entry.get("item")
    return item if isinstance(item, dict) else None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
