"""Fast smoke-mode helpers for Harbor harnesses."""

from __future__ import annotations

import os
from pathlib import Path

from .config import Harness

FAST_MODE_ENV_VAR = "HARBOR_SMOKE_FAST"
FAST_IMAGE_REUSE_ENV_VAR = "HARBOR_SMOKE_FAST_REUSE_IMAGE"
FAST_IMAGE_PREFIX_ENV_VAR = "HARBOR_SMOKE_FAST_IMAGE_PREFIX"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}

FAST_HARNESS_IMPORT_PATHS: dict[Harness, str] = {
    Harness.CODEX_CLI: "raidar.agents.harbor_agents.fast_cli_agents:FastCodexCliAgent",
    Harness.CLAUDE_CODE: "raidar.agents.harbor_agents.fast_cli_agents:FastClaudeCodeCliAgent",
    Harness.GEMINI: "raidar.agents.harbor_agents.fast_cli_agents:FastGeminiCliAgent",
}


def _normalized_env(name: str) -> str:
    return os.environ.get(name, "").strip().lower()


def is_fast_mode_enabled() -> bool:
    """Return whether smoke fast mode is enabled."""
    return _normalized_env(FAST_MODE_ENV_VAR) in _TRUE_VALUES


def is_fast_image_reuse_enabled() -> bool:
    """Return whether fast mode should reuse prebuilt scenario images."""
    if not is_fast_mode_enabled():
        return False
    value = _normalized_env(FAST_IMAGE_REUSE_ENV_VAR)
    return value not in _FALSE_VALUES


def fast_image_prefix() -> str:
    """Return docker image repo prefix for fast-mode scenario images."""
    prefix = os.environ.get(FAST_IMAGE_PREFIX_ENV_VAR, "").strip()
    return prefix or "ts-ui-eval-smoke-fast"


def fast_harness_import_path(harness: Harness) -> str | None:
    """Return custom Harbor import path for supported CLI harnesses."""
    return FAST_HARNESS_IMPORT_PATHS.get(harness)


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
