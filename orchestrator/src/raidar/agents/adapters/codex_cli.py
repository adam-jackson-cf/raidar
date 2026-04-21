"""Codex CLI harness adapter."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from pathlib import Path

from ..config import AgentSpec
from ..fast_mode import fast_harness_import_path, harness_src_path, with_harness_pythonpath
from .base import HarnessAdapter
from .codex_auth import OPENAI_API_KEY_ENV, ResolvedCodexAuth, resolve_codex_auth


class CodexCliAdapter(HarnessAdapter):
    """Adapter enforcing Codex CLI harness + model pairing."""

    HARBOR_HARNESS_NAME = "codex"
    CLI_ENV_VAR = "CODEX_CLI_PATH"
    OPENAI_API_ENV = OPENAI_API_KEY_ENV
    HARBOR_IMPORT_PATH = "raidar.agents.harbor_agents.fast_cli_agents:CodexCliHarborAgent"
    SUPPORTED_MODELS: set[str] = {
        "gpt-5.2",
        "gpt-5.3-codex-spark",
        "gpt-5.4",
        "gpt-5.4-mini",
    }
    SUPPORTED_REASONING: dict[str, tuple[str, ...]] = {
        "gpt-5.2": ("low", "medium", "high"),
        "gpt-5.3-codex-spark": ("low", "medium", "high", "xhigh"),
        "gpt-5.4": ("low", "medium", "high", "xhigh"),
        "gpt-5.4-mini": ("low",),
    }

    @classmethod
    def supported_model_summary(cls) -> str:
        return ", ".join(f"openai/{model}" for model in sorted(cls.SUPPORTED_MODELS))

    def __init__(self, config: AgentSpec) -> None:
        super().__init__(config)
        self._cli_path: str | None = None
        self._resolved_auth: ResolvedCodexAuth | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @classmethod
    def resolve_cli_path(cls) -> str:
        """Resolve the Codex CLI executable path."""
        candidate = os.environ.get(cls.CLI_ENV_VAR)
        if not candidate:
            candidate = shutil.which("codex")
        if not candidate:
            raise FileNotFoundError(
                "Codex CLI not found. Set CODEX_CLI_PATH or add 'codex' to PATH."
            )
        return candidate

    def _resolve_cli(self) -> str:
        if self._cli_path:
            return self._cli_path
        self._cli_path = self.resolve_cli_path()
        return self._cli_path

    def _resolve_auth(self) -> ResolvedCodexAuth:
        if self._resolved_auth is not None:
            return self._resolved_auth
        self._resolved_auth = resolve_codex_auth()
        return self._resolved_auth

    def validate(self) -> None:
        provider = self.config.model.provider
        if provider != "openai":
            raise ValueError(
                "Codex CLI adapter only supports models with provider 'openai'. "
                f"Received '{provider}'."
            )
        if self.config.model.name not in self.SUPPORTED_MODELS:
            supported = ", ".join(sorted(self.SUPPORTED_MODELS))
            raise ValueError(
                "Codex CLI adapter only supports models: "
                f"{supported}. Received '{self.config.model.name}'."
            )
        reasoning_effort = self.config.model.reasoning_effort
        if reasoning_effort is not None:
            allowed = self.SUPPORTED_REASONING.get(self.config.model.name, ())
            if reasoning_effort not in allowed:
                allowed_rendered = ", ".join(allowed) if allowed else "(none)"
                raise ValueError(
                    f"Model '{self.config.model.name}' only supports reasoning levels: "
                    f"{allowed_rendered}. Received '{reasoning_effort}'."
                )
        self._resolve_cli()
        self._resolve_auth()

    def harbor_harness(self) -> str:
        return self.HARBOR_HARNESS_NAME

    def harbor_harness_import_path(self) -> str | None:
        if self._resolve_auth().resolved_mode == "chatgpt":
            return self.HARBOR_IMPORT_PATH
        return fast_harness_import_path(self.config.harness)

    def model_argument(self) -> str:
        return f"{self.config.model.provider}/{self.config.model.name}"

    def extra_harbor_args(self) -> Iterable[str]:
        reasoning_effort = self.config.model.reasoning_effort
        if not reasoning_effort:
            return []
        return ["--ak", f"reasoning_effort={reasoning_effort}"]

    def runtime_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        cli_path = self._resolve_cli()
        env[self.CLI_ENV_VAR] = cli_path
        if self.harbor_harness_import_path():
            if fast_harness_import_path(self.config.harness):
                return with_harness_pythonpath(env)
            path_parts = [str(harness_src_path())]
            current = os.environ.get("PYTHONPATH")
            if current:
                path_parts.append(current)
            env["PYTHONPATH"] = os.pathsep.join(path_parts)
            return env
        return env

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

    def prepare_workspace(self, workspace: Path) -> None:
        # Ensure Codex CLI trace artifacts always have a stable home.
        codex_session_dir = workspace / ".codex"
        codex_session_dir.mkdir(exist_ok=True)
