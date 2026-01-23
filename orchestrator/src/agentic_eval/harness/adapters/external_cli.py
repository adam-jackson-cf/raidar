"""Reusable adapter for harness CLIs discovered via env or PATH."""

from __future__ import annotations

import os
import shutil
from typing import Iterable

from ..config import HarnessConfig
from .base import HarnessAdapter


class ExternalCliAdapter(HarnessAdapter):
    """Shared adapter for harnesses launched via a dedicated CLI binary."""

    CLI_ENV_VAR: str = ""
    DEFAULT_BINARY: str = ""
    REQUIRED_ENV_VARS: tuple[str, ...] = ()

    def __init__(self, config: HarnessConfig) -> None:
        super().__init__(config)
        self._resolved_cli: str | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _resolve_cli(self) -> str:
        if self._resolved_cli:
            return self._resolved_cli
        candidate = os.environ.get(self.CLI_ENV_VAR)
        if not candidate and self.DEFAULT_BINARY:
            candidate = shutil.which(self.DEFAULT_BINARY)
        if not candidate:
            hint = f"Set {self.CLI_ENV_VAR}" if self.CLI_ENV_VAR else "Install the CLI"
            raise FileNotFoundError(f"CLI binary for {self.config.agent.value} not found. {hint}.")
        self._resolved_cli = candidate
        return candidate

    # ------------------------------------------------------------------
    # Adapter overrides
    # ------------------------------------------------------------------
    def validate(self) -> None:  # noqa: D401
        self._resolve_cli()
        for env_var in self.REQUIRED_ENV_VARS:
            if not os.environ.get(env_var):
                raise EnvironmentError(
                    f"Environment variable {env_var} must be set for {self.config.agent.value}."
                )

    def runtime_env(self) -> dict[str, str]:
        env = super().runtime_env()
        if self.CLI_ENV_VAR:
            env[self.CLI_ENV_VAR] = self._resolve_cli()
        return env

    def extra_harbor_args(self) -> Iterable[str]:
        # Harbor expects the agent binary on PATH. Provide a hint via env var only.
        return []
