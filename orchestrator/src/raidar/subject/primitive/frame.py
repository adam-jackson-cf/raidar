"""``frame/1``: the build prompt with one slot per enabled package, plus the task.

The build prompt holds ``%%%TASK%%%`` exactly once and exactly one ``%%%<PACKAGE>%%%`` slot for
every enabled package (its leaf name, uppercased, hyphens as underscores): an absent, repeated,
unknown or colliding slot is refused. Each package's prompt fragment is rendered once from its
parameters and the tokens its setup returned; the build prompt is then filled in one pass, so the
task text and every rendered value are inserted verbatim and never interpreted again. Instructions
are joined in workflow order for the native agent's own instruction placement (never over the
task's ``AGENTS.md``); a disabled package contributes nothing at all.

This module is pure text: it runs no package code and writes no file.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

TASK_SLOT = "TASK"
PLACEHOLDER = re.compile(r"%%%([A-Z][A-Z0-9_]*)%%%")
Scalar = str | int | float | bool | None


class FrameError(ValueError):
    """The build prompt, a fragment or a token set cannot be framed."""


@dataclass(frozen=True)
class FramePart:
    """One enabled package's contribution, in workflow order."""

    name: str
    """The package's leaf name."""
    fragment: str
    """``prompt.md``, with ``%%%PARAM%%%`` and ``%%%TOKEN%%%`` placeholders."""
    params: Mapping[str, Scalar] = field(default_factory=dict)
    """Effective parameters; each one supplies the token of its uppercased name."""
    tokens: Mapping[str, str] = field(default_factory=dict)
    """The tokens the package's setup returned."""
    instructions: str | None = None


@dataclass(frozen=True)
class Frame:
    """The framed prompt and the instruction text the agent receives."""

    prompt: str
    instructions: str
    fragments: Mapping[str, str]
    """The rendered fragment of every package, by slot name."""


def token_name(name: str) -> str:
    """The placeholder name of a package or parameter: uppercased, hyphens as underscores."""
    return name.upper().replace("-", "_")


def _scalar_text(value: Scalar) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def render_fragment(part: FramePart) -> str:
    """One pass over the fragment: parameters and setup tokens fill their placeholders."""
    values = {token_name(key): _scalar_text(value) for key, value in part.params.items()}
    clash = sorted(values.keys() & part.tokens.keys())
    if clash:
        raise FrameError(f"{part.name}: tokens collide with parameters: {', '.join(clash)}")
    values.update(part.tokens)

    def fill(found: re.Match[str]) -> str:
        key = found.group(1)
        if key not in values:
            raise FrameError(f"{part.name}: the prompt names {key}, which is not a token")
        return values[key]

    return PLACEHOLDER.sub(fill, part.fragment)


def _check_slots(build: str, slots: Sequence[str]) -> None:
    """Exactly one ``TASK`` and one slot per package; nothing else."""
    counts = Counter(PLACEHOLDER.findall(build))
    unknown = sorted(counts.keys() - {TASK_SLOT, *slots})
    if unknown:
        raise FrameError(f"the build prompt has unknown slots: {', '.join(unknown)}")
    for slot in (TASK_SLOT, *slots):
        if counts[slot] != 1:
            raise FrameError(f"the build prompt needs %%%{slot}%%% once, not {counts[slot]} times")


def _check_unique(slots: Sequence[str]) -> None:
    if TASK_SLOT in slots:
        raise FrameError(f"a package slot cannot take the reserved %%%{TASK_SLOT}%%% name")
    repeated = sorted(slot for slot, count in Counter(slots).items() if count > 1)
    if repeated:
        raise FrameError(f"packages share a slot: {', '.join(repeated)}")


def _instructions(parts: Sequence[FramePart]) -> str:
    """Each enabled package's instructions, in order, joined into one text."""
    texts = [part.instructions.strip("\n") for part in parts if part.instructions]
    return "\n\n".join(text for text in texts if text)


def render_frame(build: str, task: str, parts: Sequence[FramePart]) -> Frame:
    """Frame ``build`` for ``task`` with the enabled packages ``parts`` (workflow order)."""
    slots = [token_name(part.name) for part in parts]
    _check_unique(slots)
    _check_slots(build, slots)
    fragments = {slot: render_fragment(part) for slot, part in zip(slots, parts, strict=True)}
    prompt = PLACEHOLDER.sub(
        lambda found: task if found.group(1) == TASK_SLOT else fragments[found.group(1)], build
    )
    return Frame(prompt, _instructions(parts), fragments)
