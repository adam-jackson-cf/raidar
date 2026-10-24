"""Shared adapter behaviors for harnesses that Harbor runs as installed agents."""

from __future__ import annotations

from dataclasses import dataclass

from .base import HarnessAdapter


@dataclass(frozen=True, slots=True)
class SupportedModelProfile:
    """Model support metadata for adapter validation and reporting."""

    reasoning_levels: tuple[str, ...] = ()


class HarborCliAdapter(HarnessAdapter):
    """Reusable base for harnesses executed as Harbor installed agents."""

    CLI_ENV_VAR: str = ""
    DEFAULT_BINARY: str = ""
    HARBOR_HARNESS_NAME: str = ""
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

    def harbor_harness(self) -> str:
        return self.HARBOR_HARNESS_NAME

    def model_argument(self) -> str:
        return f"{self.config.model.provider}/{self.config.model.name}"

    def runtime_env(self) -> dict[str, str]:
        return {self.CLI_ENV_VAR: self._resolve_cli()}
