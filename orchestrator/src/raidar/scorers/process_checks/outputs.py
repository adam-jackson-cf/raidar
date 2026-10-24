"""Did a test command run? What each tool call printed, and whether its runner was found.

A test command whose recorded output says its runner was not found never ran: it is
dropped before the checks run and counted separately (``test_commands_not_run``).

- a test command (a ``test`` event of the classifier: the default runners
  plus the assessment's ``extra_test_patterns``) **did not run** when the
  output its tool call recorded says its runner was not found:
  ``<runner>: command not found`` or ``<runner>: not found`` (shells),
  ``<runner>: No such file or directory``, ``Failed to spawn: `<runner>``` (uv)
  or ``No module named <module>`` for ``python -m <module>``. The runner is the
  command's first word after launcher prefixes (``uv run``, ``timeout 60``,
  ``FOO=1`` ...), or the module of ``python -m``;
- a command whose call recorded no output counts as run: the trace cannot
  show that it did not run.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from harbor.models.trajectories import Trajectory as HarborTrajectory
from pydantic import ValidationError

from raidar.scorers.process_checks.trace_events import LAUNCHER, EventKind, TraceEvent
from raidar.scorers.sdk.manifest import (
    Direction,
    Exactness,
    MetricLevel,
    MetricSpec,
    MetricType,
    Tier,
)
from raidar.scorers.sdk.result import TRAJECTORY_SUBJECT, AssessStatus, Observation

NOT_RUN = "test_commands_not_run"
TOTAL_NOT_RUN = f"total_{NOT_RUN}"
NOT_RUN_METRICS = (
    MetricSpec(
        id=NOT_RUN,
        description="Test commands in the step whose runner was not found (they never ran).",
        level=MetricLevel.CHECKPOINT,
        type=MetricType.COUNT,
        direction=Direction.LOWER_BETTER,
        tier=Tier.DETERMINISTIC,
        exactness=Exactness.NATIVE,
    ),
    MetricSpec(
        id=TOTAL_NOT_RUN,
        description="Test commands whose runner was not found, over the trajectory.",
        level=MetricLevel.TRAJECTORY,
        type=MetricType.COUNT,
        direction=Direction.LOWER_BETTER,
        tier=Tier.DETERMINISTIC,
        exactness=Exactness.NATIVE,
    ),
)
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
    launcher = LAUNCHER.match(command)
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


def not_run_total(observations: Sequence[Observation]) -> Observation:
    """``total_test_commands_not_run``: the steps' counts summed (unavailable if a step is)."""
    per_step = [item for item in observations if item.metric == NOT_RUN]
    gaps = [item.subject for item in per_step if item.status is AssessStatus.UNAVAILABLE]
    if gaps:
        return Observation(
            metric=TOTAL_NOT_RUN,
            subject=TRAJECTORY_SUBJECT,
            status=AssessStatus.UNAVAILABLE,
            reason=f"{NOT_RUN} is unavailable at {', '.join(gaps)}",
        )
    counted = [item.value or 0.0 for item in per_step if item.status is AssessStatus.OK]
    if not counted:
        return Observation(
            metric=TOTAL_NOT_RUN,
            subject=TRAJECTORY_SUBJECT,
            status=AssessStatus.NOT_APPLICABLE,
            reason="no reached step with a readable trajectory",
        )
    return Observation(
        metric=TOTAL_NOT_RUN, subject=TRAJECTORY_SUBJECT, status=AssessStatus.OK, value=sum(counted)
    )


__all__ = [
    "NOT_RUN",
    "NOT_RUN_METRICS",
    "TOTAL_NOT_RUN",
    "SplitEvents",
    "call_outputs",
    "not_found",
    "not_run_total",
    "runner_of",
    "split_unrun",
]
