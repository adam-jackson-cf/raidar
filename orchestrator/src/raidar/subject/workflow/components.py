"""Workflow skills placed for the agent.

A workflow's skills travel inside its resolved definition (``components``: path -> text) and are
placed where the agent's ``placement.skills`` says, below the workspace. Only the skills of the
workflow's enabled stages are placed.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from raidar.subject.workflow.definition import SKILLS_DIR, WorkflowDefinition


class PlacementError(ValueError):
    """An agent's skills placement is not a workspace-relative directory."""


def skill_files(definition: WorkflowDefinition, placement: str) -> dict[str, str]:
    """``{workspace-relative path: text}`` of every file of the enabled stages' skills.

    ``placement`` is the agent's ``placement.skills``: a relative directory without ``..``, and
    without ``{home}`` (a skills directory outside the workspace is not a workflow placement).
    """
    root = PurePosixPath(placement)
    if (
        not placement
        or root.is_absolute()
        or ".." in root.parts
        or "{" in placement
        or root.parts in ((), (".",))
    ):
        raise PlacementError(f"skills placement {placement!r} must be a workspace-relative path")
    files: dict[str, str] = {}
    for settings in definition.stages.enabled.values():
        for skill in settings.skills:
            prefix = f"{SKILLS_DIR}/{skill}/"
            for path, text in definition.components.items():
                if path.startswith(prefix):
                    files[(root / path.removeprefix(f"{SKILLS_DIR}/")).as_posix()] = text
    return files
