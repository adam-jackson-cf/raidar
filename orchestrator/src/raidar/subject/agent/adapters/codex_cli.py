"""Codex CLI harness adapter."""

from __future__ import annotations

from pathlib import Path

from raidar.subject.agent.codex_auth import ResolvedCodexAuth, resolve_codex_auth

from ..config import AgentSpec
from .harbor_cli import HarborCliAdapter, SupportedModelProfile


class CodexCliAdapter(HarborCliAdapter):
    """Adapter enforcing Codex CLI harness + model pairing."""

    HARBOR_HARNESS_NAME = "codex"
    REQUIRED_PROVIDER = "openai"
    ADAPTER_LABEL = "Codex CLI adapter"
    CLI_ENV_VAR = "CODEX_CLI_PATH"
    DEFAULT_BINARY = "codex"
    SUPPORTED_MODELS: dict[str, SupportedModelProfile] = {
        "gpt-5.6-terra": SupportedModelProfile(reasoning_levels=("low",)),
        "gpt-5.6-luna": SupportedModelProfile(reasoning_levels=("low",)),
        "gpt-6-luna": SupportedModelProfile(reasoning_levels=("low",)),
        "gpt-5.5": SupportedModelProfile(reasoning_levels=("low", "medium", "high", "xhigh")),
        "gpt-5.2": SupportedModelProfile(reasoning_levels=("low", "medium", "high")),
        "gpt-5.3-codex-spark": SupportedModelProfile(
            reasoning_levels=("low", "medium", "high", "xhigh")
        ),
        "gpt-5.4": SupportedModelProfile(reasoning_levels=("low", "medium", "high", "xhigh")),
        "gpt-5.4-mini": SupportedModelProfile(reasoning_levels=("low",)),
    }

    def __init__(self, config: AgentSpec) -> None:
        super().__init__(config)
        self._resolved_auth: ResolvedCodexAuth | None = None

    def _resolve_auth(self) -> ResolvedCodexAuth:
        if self._resolved_auth is not None:
            return self._resolved_auth
        self._resolved_auth = resolve_codex_auth()
        return self._resolved_auth

    def credential_environment_sources(self) -> dict[str, str]:
        auth = self._resolve_auth()
        if auth.resolved_mode != "api":
            return {}
        return super().credential_environment_sources()

    def credential_file_sources(self) -> dict[str, Path]:
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
