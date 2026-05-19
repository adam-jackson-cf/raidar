"""Shared adapter behaviors for repository-local Harbor CLI harnesses."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..harbor_routing import harbor_agent_import_path, with_harness_pythonpath
from .base import HarnessAdapter


@dataclass(frozen=True, slots=True)
class SupportedModelProfile:
    """Model support metadata for adapter validation and reporting."""

    display_label: str
    reasoning_levels: tuple[str, ...] = ()
    default_reasoning: str | None = None


class HarborCliAdapter(HarnessAdapter):
    """Reusable base for harnesses executed via repository-local Harbor agents."""

    CLI_ENV_VAR: str = ""
    DEFAULT_BINARY: str = ""
    HARBOR_HARNESS_NAME: str = ""
    WORKSPACE_SESSION_DIR: str = ""
    SUPPORTED_MODELS: dict[str, SupportedModelProfile] = {}
    REQUIRED_PROVIDER: str = ""
    ADAPTER_LABEL: str = ""
    REASONING_UNSUPPORTED_MESSAGE: str | None = None

    @classmethod
    def supported_model_summary(cls) -> str:
        if not cls.REQUIRED_PROVIDER:
            return super().supported_model_summary()
        return ", ".join(
            f"{cls.REQUIRED_PROVIDER}/{model}" for model in sorted(cls.SUPPORTED_MODELS)
        )

    def validate(self) -> None:
        self._validate_model()
        self._resolve_cli()
        self._validate_credentials()

    def _validate_model(self) -> None:
        label = self.ADAPTER_LABEL or self.config.harness.value
        provider = self.config.model.provider
        if self.REQUIRED_PROVIDER and provider != self.REQUIRED_PROVIDER:
            raise ValueError(
                f"{label} only supports models with provider '{self.REQUIRED_PROVIDER}'. "
                f"Received '{provider}'."
            )
        model_profile = self.SUPPORTED_MODELS.get(self.config.model.name)
        if model_profile is None:
            supported = ", ".join(sorted(self.SUPPORTED_MODELS))
            raise ValueError(
                f"{label} only supports models: {supported}. Received '{self.config.model.name}'."
            )
        reasoning_effort = self.config.model.reasoning_effort
        if reasoning_effort is None:
            return
        if self.REASONING_UNSUPPORTED_MESSAGE is not None:
            raise ValueError(self.REASONING_UNSUPPORTED_MESSAGE)
        allowed = model_profile.reasoning_levels
        if reasoning_effort not in allowed:
            allowed_rendered = ", ".join(allowed) if allowed else "(none)"
            raise ValueError(
                f"Model '{self.config.model.name}' only supports reasoning levels: "
                f"{allowed_rendered}. Received '{reasoning_effort}'."
            )

    def _validate_credentials(self) -> None:
        """Validate harness-specific credentials after model/CLI validation."""

    def harbor_harness(self) -> str:
        return self.HARBOR_HARNESS_NAME

    def harbor_harness_import_path(self) -> str | None:
        return harbor_agent_import_path(self.config.harness)

    def model_argument(self) -> str:
        return f"{self.config.model.provider}/{self.config.model.name}"

    def runtime_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        env[self.CLI_ENV_VAR] = self._resolve_cli()
        return with_harness_pythonpath(env)

    def prepare_workspace(self, workspace: Path) -> None:
        if not self.WORKSPACE_SESSION_DIR:
            return
        (workspace / self.WORKSPACE_SESSION_DIR).mkdir(exist_ok=True)
