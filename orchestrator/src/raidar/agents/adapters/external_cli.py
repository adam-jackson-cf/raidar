"""Reusable adapter for harness CLIs discovered via env or PATH."""

from __future__ import annotations

import os
from collections.abc import Iterable

from .base import HarnessAdapter


class ExternalCliAdapter(HarnessAdapter):
    """Shared adapter for harnesses launched via a dedicated CLI binary."""

    CLI_ENV_VAR: str = ""
    DEFAULT_BINARY: str = ""
    REQUIRED_ENV_VARS: tuple[str, ...] = ()
    ALLOWED_PROVIDERS: tuple[str, ...] | None = None

    @classmethod
    def supported_model_summary(cls) -> str:
        if not cls.ALLOWED_PROVIDERS:
            return "(any provider/model)"
        return ", ".join(f"{provider}/*" for provider in cls.ALLOWED_PROVIDERS)

    # ------------------------------------------------------------------
    # Adapter overrides
    # ------------------------------------------------------------------
    def validate(self) -> None:  # noqa: D401
        self._resolve_cli()
        for env_var in self.REQUIRED_ENV_VARS:
            if not os.environ.get(env_var):
                raise OSError(
                    f"Environment variable {env_var} must be set for {self.config.harness.value}."
                )
        if self.ALLOWED_PROVIDERS and self.config.model.provider not in self.ALLOWED_PROVIDERS:
            allowed = ", ".join(self.ALLOWED_PROVIDERS)
            raise ValueError(
                f"{self.config.harness.value} harness only supports providers: {allowed}. "
                f"Received '{self.config.model.provider}'."
            )

    def runtime_env(self) -> dict[str, str]:
        env = super().runtime_env()
        if self.CLI_ENV_VAR:
            env[self.CLI_ENV_VAR] = self._resolve_cli()
        return env

    def extra_harbor_args(self) -> Iterable[str]:
        # Harbor expects the harness binary on PATH. Provide a hint via env var only.
        return []
