"""Harbor ATIF trajectories (``steps/<step>/agent/trajectory.json``) projected to spans.

Each ATIF step becomes one span under the checkpoint's parent span: system and
user messages are ``INTERNAL`` spans (the task prompt, the last user message
before the agent's first turn, is named as a prompt), agent turns are
``LLM_GENERATION`` spans carrying the model and token usage, and every tool call
is a ``TOOL_CALL`` span whose output is the matching observation. Embedded
sub-agent sessions follow their parent session under their own span. Harbor
owns the format: a document Harbor's ``Trajectory`` model rejects is shown as
one error span, never partially guessed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from harbor.models.trajectories import Step, ToolCall, Trajectory
from pydantic import ValidationError

from raidar.review_projection.spans import SpanBuilder, to_ms
from raidar.schemas.review import ReviewStatus

TRAJECTORY_FILE = "trajectory.json"
_NAME_LIMIT = 60
_COMMAND_KEYS = ("cmd", "command")
_EMBEDDED_COMMAND = re.compile(r"""\bcmd\s*:\s*["']((?:[^"'\\]|\\.)+)["']""")
_EXIT_CODE = re.compile(
    r"(?:process )?(?:exit(?:ed)? (?:with )?code|exit_code\W+)\s*:?\s*(-?\d+)", re.IGNORECASE
)
_FAILED_HEADERS = ("Script failed", "Script error")
_OK_HEADERS = ("Script completed",)
_HEADER_LINES = 3
_PARTS_PREFIXES = ("[{'type'", '[{"type"')
TRIAL_PROMPT = "user prompt"
CHECKPOINT_PROMPT = "checkpoint prompt"
SUBAGENT_PROMPT = "subagent prompt"


def _content_parts(text: str) -> str | None:
    """Harbor's Codex ATIF stores tool output as the Python repr of content parts
    (``"[{'type': 'input_text', 'text': '...'}, ...]"``); join their texts."""
    if not text.startswith(_PARTS_PREFIXES):
        return None
    try:
        parts = ast.literal_eval(text)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None
    if not isinstance(parts, list) or not all(isinstance(part, dict) for part in parts):
        return None
    return "".join(str(part.get("text") or "") for part in parts)


def _text(message: object) -> str:
    if isinstance(message, str):
        decoded = _content_parts(message)
        return message if decoded is None else decoded
    if isinstance(message, list):
        return "\n".join(part.text for part in message if getattr(part, "text", None))
    return ""


def _command(call: ToolCall) -> str | None:
    for key in _COMMAND_KEYS:
        value = call.arguments.get(key)
        if isinstance(value, list):
            return " ".join(str(item) for item in value)
        if isinstance(value, str):
            return value
    for value in call.arguments.values():
        match = _EMBEDDED_COMMAND.search(value) if isinstance(value, str) else None
        if match:
            return match.group(1)
    return None


def tool_label(call: ToolCall) -> str:
    """A short span name: the shell command when the call runs one, else the tool name."""
    command = _command(call)
    label = command if command else call.function_name
    return label.strip().splitlines()[0][:_NAME_LIMIT] if label.strip() else call.function_name


def _observation(step: Step, call: ToolCall) -> str | None:
    if step.observation is None:
        return None
    parts = [
        _text(result.content)
        for result in step.observation.results
        if result.source_call_id in (call.tool_call_id, None)
    ]
    return "\n".join(part for part in parts if part) or None


def _tool_status(output: str | None) -> ReviewStatus:
    """The harness's own verdict line, never text inside the command's output.

    Codex starts every tool result with ``Script completed`` or ``Script
    failed``; otherwise only an exit code in the header lines counts (a body
    line such as ``assert data["exit_code"] == 2`` is the program's text).
    """
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    first = lines[0] if lines else ""
    if first.startswith(_FAILED_HEADERS):
        return "ERROR"
    if first.startswith(_OK_HEADERS):
        return "OK"
    for line in lines[:_HEADER_LINES]:
        match = _EXIT_CODE.match(line)
        if match:
            return "ERROR" if int(match.group(1)) != 0 else "OK"
    return "OK"


def _durations(steps: list[Step]) -> list[int | None]:
    """Each step lasts until the next one starts (unknown for the last step)."""
    starts = [to_ms(step.timestamp) for step in steps]
    durations: list[int | None] = []
    for index, current in enumerate(starts):
        following = starts[index + 1] if index + 1 < len(starts) else None
        if current is None or following is None or following < current:
            durations.append(None)
        else:
            durations.append(following - current)
    return durations


def _agent_step(builder: SpanBuilder, step: Step, parent: str, duration: int | None) -> None:
    start = to_ms(step.timestamp)
    metrics = step.metrics
    turn = builder.add(
        name="assistant message",
        span_type="LLM_GENERATION",
        status="OK",
        parent=parent,
        start=start,
        duration=duration,
        model=step.model_name,
        output_value=_text(step.message) or None,
        attributes={"step_id": step.step_id, "reasoning": step.reasoning_content},
        tokens=(
            metrics.prompt_tokens if metrics else None,
            metrics.completion_tokens if metrics else None,
        ),
    )
    for call in step.tool_calls or ():
        output = _observation(step, call)
        builder.add(
            name=tool_label(call),
            span_type="TOOL_CALL",
            status=_tool_status(output),
            parent=turn,
            start=start,
            duration=duration,
            input_value={"tool": call.function_name, "arguments": call.arguments},
            output_value=output,
        )


def _prompt_index(steps: list[Step]) -> int | None:
    """The task prompt: the last user message before the agent's first turn."""
    first_agent = next((i for i, step in enumerate(steps) if step.source == "agent"), len(steps))
    users = [i for i, step in enumerate(steps[:first_agent]) if step.source == "user"]
    return users[-1] if users else None


def _message_name(step: Step, index: int, prompt: int | None, prompt_name: str) -> str:
    if step.source == "system":
        return "system prompt"
    return prompt_name if index == prompt else "user message"


def _session(builder: SpanBuilder, document: Trajectory, parent: str, prompt_name: str) -> None:
    prompt = _prompt_index(document.steps)
    durations = _durations(document.steps)
    for index, (step, duration) in enumerate(zip(document.steps, durations, strict=True)):
        if step.source == "agent":
            _agent_step(builder, step, parent, duration)
            continue
        builder.add(
            name=_message_name(step, index, prompt, prompt_name),
            span_type="INTERNAL",
            status="OK",
            parent=parent,
            start=to_ms(step.timestamp),
            duration=duration,
            input_value=_text(step.message),
        )
    for embedded in document.subagent_trajectories or ():
        session = builder.add(
            name=f"session {embedded.session_id or embedded.trajectory_id or 'subagent'}",
            span_type="AGENT_ROOT",
            status="OK",
            parent=parent,
            model=embedded.agent.model_name,
        )
        _session(builder, embedded, session, SUBAGENT_PROMPT)


def project_trajectory(
    builder: SpanBuilder, trace_dir: Path, parent: str, *, first_checkpoint: bool
) -> None:
    """Add a step's ATIF trajectory under ``parent`` (an error span when unreadable).

    The first checkpoint's task prompt is the trial's ``user prompt``; later
    checkpoints' prompts (new requirements) are ``checkpoint prompt`` spans.
    """
    path = trace_dir / TRAJECTORY_FILE
    try:
        document = Trajectory.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        builder.add(
            name="trajectory unreadable",
            span_type="INTERNAL",
            status="ERROR",
            parent=parent,
            output_value=f"{path}: {exc}",
        )
        return
    _session(builder, document, parent, TRIAL_PROMPT if first_checkpoint else CHECKPOINT_PROMPT)
