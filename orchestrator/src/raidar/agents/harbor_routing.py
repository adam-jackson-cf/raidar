"""Canonical Harbor routing helpers for repository-local harnesses."""

from __future__ import annotations

import os
from pathlib import Path

from .config import Harness

TASK_IMAGE_REUSE_ENV_VAR = "RAIDAR_TASK_IMAGE_REUSE"
TASK_IMAGE_PREFIX_ENV_VAR = "RAIDAR_TASK_IMAGE_PREFIX"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}

HARBOR_AGENT_IMPORT_PATHS: dict[Harness, str] = {
    Harness.CODEX_CLI: "raidar.agents.harbor_agents.cli_agents:CodexCliHarborAgent",
    Harness.CLAUDE_CODE: "raidar.agents.harbor_agents.cli_agents:ClaudeCodeCliHarborAgent",
    Harness.GEMINI: "raidar.agents.harbor_agents.cli_agents:GeminiCliHarborAgent",
}


def _normalized_env(name: str) -> str:
    return os.environ.get(name, "").strip().lower()


def is_task_image_reuse_enabled() -> bool:
    """Return whether task environment images should be reused."""
    value = _normalized_env(TASK_IMAGE_REUSE_ENV_VAR)
    return value not in _FALSE_VALUES


def task_image_prefix() -> str:
    """Return docker image repo prefix for task environment images."""
    prefix = os.environ.get(TASK_IMAGE_PREFIX_ENV_VAR, "").strip()
    return prefix or "raidar-task-env"


def harbor_agent_import_path(harness: Harness) -> str | None:
    """Return repository-local Harbor import path for supported CLI harnesses."""
    return HARBOR_AGENT_IMPORT_PATHS.get(harness)


def harness_src_path() -> Path:
    """Return absolute path to orchestrator/src for PYTHONPATH injection."""
    return Path(__file__).resolve().parents[2]


def with_harness_pythonpath(env: dict[str, str]) -> dict[str, str]:
    """Ensure Harbor process can import repository-local Harbor harnesses."""
    path_parts = [str(harness_src_path())]
    current = env.get("PYTHONPATH") or os.environ.get("PYTHONPATH")
    if current:
        path_parts.append(current)
    env["PYTHONPATH"] = os.pathsep.join(path_parts)
    return env
