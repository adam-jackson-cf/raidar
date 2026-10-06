"""Did a test command run? What each tool call printed, and whether its runner was found.

``process-compliance@4`` drops a test command whose recorded output says its
runner was not found (``raidar.scorers.process_checks_v4.scorer``).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from harbor.models.trajectories import Trajectory as HarborTrajectory
from pydantic import ValidationError

from raidar.scorers.process_checks.trace_events import _LAUNCHER, EventKind, TraceEvent

_PYTHON_MODULE = re.compile(r"python[0-9.]*\s+(?:-\w+\s+)*-m\s+([\w.]+)")
_FIRST_WORD = re.compile(r"\S+")


# ---- what each tool call printed --------------------------------------------------------


def _text(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(getattr(part, "text", "") or "") for part in content)
    return str(content)


def _documents(document: HarborTrajectory) -> Iterator[HarborTrajectory]:
    yield document
    for sub in document.subagent_trajectories or ():
        yield from _documents(sub)


def call_outputs(path: Path) -> dict[str, str]:
    """Every tool call's recorded output, by ``tool_call_id`` (every session included)."""
    try:
        document = HarborTrajectory.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return {}
    outputs: dict[str, str] = {}
    for session in _documents(document):
        for step in session.steps:
            for result in step.observation.results if step.observation else ():
                if result.source_call_id:
                    outputs[result.source_call_id] = outputs.get(result.source_call_id, "") + _text(
                        result.content
                    )
    return outputs


# ---- did a test command run ----------------------------------------------------------


def runner_of(command: str) -> tuple[str, str | None]:
    """(first word after launcher prefixes, the module of ``python -m``) of a test command."""
    launcher = _LAUNCHER.match(command)
    rest = command[launcher.end() :] if launcher else command
    module = _PYTHON_MODULE.match(rest)
    word = _FIRST_WORD.match(rest)
    return (word.group(0) if word else rest.strip()), (module.group(1) if module else None)


def not_found(command: str, output: str) -> bool:
    """Whether ``output`` says the command's runner (or ``python -m`` module) was not found."""
    word, module = runner_of(command)
    if module is not None:
        top = re.escape(module.split(".")[0])
        if re.search(rf"No module named ['\"]?{top}\b", output):
            return True
    name = re.escape(word)
    patterns = (
        rf"(?<![\w.-]){name}: (?:command )?not found",
        rf"(?<![\w.-]){name}: No such file or directory",
        rf"Failed to spawn: `{name}`",
    )
    return any(re.search(pattern, output) for pattern in patterns)


@dataclass(frozen=True)
class SplitEvents:
    kept: tuple[TraceEvent, ...]
    not_run: tuple[TraceEvent, ...]


def split_unrun(events: Sequence[TraceEvent], outputs: dict[str, str]) -> SplitEvents:
    """Test events whose runner the call's output says was not found, apart from the rest."""
    kept: list[TraceEvent] = []
    not_run: list[TraceEvent] = []
    for event in events:
        output = outputs.get(event.call_id or "")
        if event.kind is EventKind.TEST and output and not_found(event.detail, output):
            not_run.append(event)
        else:
            kept.append(event)
    return SplitEvents(tuple(kept), tuple(not_run))


__all__ = ["SplitEvents", "call_outputs", "not_found", "runner_of", "split_unrun"]
