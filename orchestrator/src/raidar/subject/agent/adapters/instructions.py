"""Placing a workflow's instructions in a harness's per-turn home (``placement.instructions``)."""

from __future__ import annotations

import base64
import posixpath
import shlex

from raidar.subject.agent.protocol import IsolationError


def instructions_target(template: str | None, home: str) -> str:
    """The file ``placement.instructions`` names, ``{home}`` expanded to the isolated ``home``.

    Instructions go only inside the isolated home, never the task's own instruction files: no
    template, or one resolving outside ``home``, is refused (there is no default path).
    """
    if not template:
        raise IsolationError("workflow instructions were given without a placement path")
    target = posixpath.normpath(template.replace("{home}", home))
    if not target.startswith(posixpath.normpath(home) + "/"):
        raise IsolationError(
            f"instructions placement {template!r} resolves to {target}, outside the isolated "
            f"home {home}"
        )
    return target


def place_instructions_command(path: str, text: str) -> str:
    """A shell command writing ``text`` to ``path`` byte for byte (creating its directory)."""
    payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
    folder = shlex.quote(posixpath.dirname(path))
    return f"mkdir -p {folder} && printf %s {payload} | base64 -d > {shlex.quote(path)}"
