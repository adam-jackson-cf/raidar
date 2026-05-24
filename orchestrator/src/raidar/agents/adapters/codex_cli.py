"""Codex CLI harness adapter."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from raidar.codex_auth import OPENAI_API_KEY_ENV, ResolvedCodexAuth, resolve_codex_auth

from ..config import AgentSpec
from .harbor_cli import HarborCliAdapter, SupportedModelProfile


class CodexCliAdapter(HarborCliAdapter):
    """Adapter enforcing Codex CLI harness + model pairing."""

    HARBOR_HARNESS_NAME = "codex"
    REQUIRED_PROVIDER = "openai"
    ADAPTER_LABEL = "Codex CLI adapter"
    CLI_ENV_VAR = "CODEX_CLI_PATH"
    DEFAULT_BINARY = "codex"
    WORKSPACE_SESSION_DIR = ".codex"
    OPENAI_API_ENV = OPENAI_API_KEY_ENV
    SUPPORTED_MODELS: dict[str, SupportedModelProfile] = {
        "gpt-5.5": SupportedModelProfile(
            display_label="GPT-5.5",
            reasoning_levels=("low", "medium", "high", "xhigh"),
        ),
        "gpt-5.2": SupportedModelProfile(
            display_label="GPT-5.2",
            reasoning_levels=("low", "medium", "high"),
        ),
        "gpt-5.3-codex-spark": SupportedModelProfile(
            display_label="GPT-5.3 Codex Spark",
            reasoning_levels=("low", "medium", "high", "xhigh"),
        ),
        "gpt-5.4": SupportedModelProfile(
            display_label="GPT-5.4",
            reasoning_levels=("low", "medium", "high", "xhigh"),
        ),
        "gpt-5.4-mini": SupportedModelProfile(
            display_label="GPT-5.4 Mini",
            reasoning_levels=("low",),
        ),
    }

    def __init__(self, config: AgentSpec) -> None:
        super().__init__(config)
        self._resolved_auth: ResolvedCodexAuth | None = None

    def _resolve_auth(self) -> ResolvedCodexAuth:
        if self._resolved_auth is not None:
            return self._resolved_auth
        self._resolved_auth = resolve_codex_auth()
        return self._resolved_auth

    def _validate_credentials(self) -> None:
        self._resolve_auth()

    def extra_harbor_args(self) -> Iterable[str]:
        reasoning_effort = self.config.model.reasoning_effort
        if not reasoning_effort:
            return []
        return ["--ak", f"reasoning_effort={reasoning_effort}"]

    def excluded_run_env_keys(self) -> set[str]:
        auth = self._resolve_auth()
        if auth.resolved_mode == "chatgpt":
            return {self.OPENAI_API_ENV}
        return set()

    def local_secret_files(self) -> dict[str, Path]:
        auth = self._resolve_auth()
        if auth.resolved_mode != "chatgpt" or auth.auth_json_path is None:
            return {}
        return {"CODEX_AUTH_JSON": auth.auth_json_path}

    def execution_metadata(self) -> dict[str, str | None]:
        auth = self._resolve_auth()
        return {
            "auth_mode": auth.resolved_mode,
            "auth_mode_requested": auth.requested_mode,
            "auth_source": auth.source_label,
            "auth_json_path": str(auth.auth_json_path) if auth.auth_json_path else None,
        }
