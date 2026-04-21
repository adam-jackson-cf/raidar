"""Codex authentication helpers shared by adapter and CLI setup flows."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

CODEX_AUTH_MODE_ENV = "CODEX_AUTH_MODE"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
VALID_CODEX_AUTH_MODES = ("auto", "chatgpt", "api")


@dataclass(frozen=True, slots=True)
class ResolvedCodexAuth:
    """Resolved Codex authentication source."""

    requested_mode: str
    resolved_mode: str
    auth_json_path: Path | None
    source_label: str

    def metadata(self) -> dict[str, str | None]:
        return {
            "requested_mode": self.requested_mode,
            "resolved_mode": self.resolved_mode,
            "source": self.source_label,
            "auth_json_path": str(self.auth_json_path) if self.auth_json_path else None,
        }


def normalize_codex_auth_mode(value: str | None = None) -> str:
    """Return the configured Codex auth mode."""
    raw = value if value is not None else os.environ.get(CODEX_AUTH_MODE_ENV, "auto")
    normalized = raw.strip().lower() or "auto"
    if normalized not in VALID_CODEX_AUTH_MODES:
        allowed = ", ".join(VALID_CODEX_AUTH_MODES)
        raise ValueError(f"Unsupported {CODEX_AUTH_MODE_ENV} '{raw}'. Expected one of: {allowed}.")
    return normalized


def codex_home_path() -> Path:
    """Return the configured Codex home directory."""
    configured = os.environ.get("CODEX_HOME", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex"


def codex_auth_json_path() -> Path:
    """Return the expected file-backed Codex auth.json path."""
    return codex_home_path() / "auth.json"


def has_file_backed_codex_auth(auth_json_path: Path | None = None) -> bool:
    """Return whether a file-backed Codex auth cache is available."""
    candidate = auth_json_path or codex_auth_json_path()
    if not candidate.is_file():
        return False
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and bool(payload)


def resolve_codex_auth(
    *,
    requested_mode: str | None = None,
    auth_json_path: Path | None = None,
    api_key_present: bool | None = None,
) -> ResolvedCodexAuth:
    """Resolve the Codex auth source according to the configured mode."""
    normalized_mode = normalize_codex_auth_mode(requested_mode)
    resolved_auth_json_path = auth_json_path or codex_auth_json_path()
    has_auth_json = has_file_backed_codex_auth(resolved_auth_json_path)
    has_api_key = (
        api_key_present if api_key_present is not None else bool(os.environ.get(OPENAI_API_KEY_ENV))
    )
    if normalized_mode == "chatgpt":
        if has_auth_json:
            return ResolvedCodexAuth(
                requested_mode=normalized_mode,
                resolved_mode="chatgpt",
                auth_json_path=resolved_auth_json_path,
                source_label="file-backed Codex session cache",
            )
        raise OSError(
            "Codex ChatGPT auth requires file-backed credentials. "
            "Run `make codex-auth-setup` or configure Codex to store credentials in "
            f"{resolved_auth_json_path}."
        )

    if normalized_mode == "api":
        if has_api_key:
            return ResolvedCodexAuth(
                requested_mode=normalized_mode,
                resolved_mode="api",
                auth_json_path=None,
                source_label="OPENAI_API_KEY",
            )
        raise OSError(
            "Codex API auth requires OPENAI_API_KEY. "
            "Set OPENAI_API_KEY or switch CODEX_AUTH_MODE to auto/chatgpt and run "
            "`make codex-auth-setup`."
        )

    if has_auth_json:
        return ResolvedCodexAuth(
            requested_mode=normalized_mode,
            resolved_mode="chatgpt",
            auth_json_path=resolved_auth_json_path,
            source_label="file-backed Codex session cache",
        )
    if has_api_key:
        return ResolvedCodexAuth(
            requested_mode=normalized_mode,
            resolved_mode="api",
            auth_json_path=None,
            source_label="OPENAI_API_KEY",
        )
    raise OSError(
        "Codex auth is not configured. "
        "Run `make codex-auth-setup` for ChatGPT login or set OPENAI_API_KEY for API-key auth."
    )
