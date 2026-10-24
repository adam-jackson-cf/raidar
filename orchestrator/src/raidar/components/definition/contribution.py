"""What one component use contributes to a trial's Harbor inputs.

Contributions are merged per condition by the compiler into:

- a workspace overlay (applied through the first step's ``workdir/``);
- support files materialised outside the workspace by the per-step ``setup.sh``;
- the harness-native config passed as the agent's ``config`` kwarg (``--ak config``);
- skill directories (``--skill``) and MCP servers (``mcp_servers``);
- a prompt template (``prompt_template_path``, Harbor's instruction rendering);
- an optional agent wrapper (the ``workflow`` kind).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from harbor.models.task.config import MCPServerConfig
from pydantic import JsonValue

# Container paths owned by the component layer (never inside the workspace).
COMPONENT_ROOT = PurePosixPath("/tmp/raidar-components")
EVIDENCE_DIR = PurePosixPath("/logs/agent/raidar-components")


def component_dir(slug: str) -> PurePosixPath:
    return COMPONENT_ROOT / slug


def evidence_log(slug: str, channel: str) -> PurePosixPath:
    """Per-component JSONL log written inside the agent logs dir (hooks, MCP shim)."""
    return EVIDENCE_DIR / f"{slug}.{channel}.jsonl"


@dataclass(frozen=True)
class SupportFile:
    content: bytes
    executable: bool = False


@dataclass(frozen=True)
class AgentWrapper:
    """A Harbor agent that wraps the arm's agent (``workflow`` components)."""

    import_path: str
    kwargs: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class Contribution:
    overlay: Mapping[str, bytes] = field(default_factory=dict)
    support_files: Mapping[str, SupportFile] = field(default_factory=dict)
    native_config: Mapping[str, JsonValue] = field(default_factory=dict)
    skills: tuple[Path, ...] = ()
    mcp_servers: tuple[MCPServerConfig, ...] = ()
    prompt_template: Path | None = None
    agent_wrapper: AgentWrapper | None = None


class ContributionConflictError(ValueError):
    """Two component uses set the same Harbor input incompatibly."""


# Config lists that hold independent entries, so two uses may each add some:
# Claude Code ``hooks.<Event>`` matcher lists. Every other list is one value
# (e.g. Codex ``notify`` is a single argv), so two different lists conflict.
APPENDABLE_LIST_PATH = re.compile(r"^hooks\.[A-Za-z]+$")


def merge_native_config(
    base: Mapping[str, JsonValue], extra: Mapping[str, JsonValue], *, path: str = ""
) -> dict[str, JsonValue]:
    """Deep-merge harness config: tables merge, entry lists append, other values must agree."""
    merged: dict[str, JsonValue] = dict(base)
    for key, value in extra.items():
        where = f"{path}.{key}" if path else key
        if key not in merged:
            merged[key] = value
            continue
        current = merged[key]
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = merge_native_config(current, value, path=where)
        elif (
            isinstance(current, list)
            and isinstance(value, list)
            and APPENDABLE_LIST_PATH.fullmatch(where)
        ):
            merged[key] = [*current, *value]
        elif current != value:
            raise ContributionConflictError(
                f"harness config key {where!r} is set to both {current!r} and {value!r}"
            )
    return merged
