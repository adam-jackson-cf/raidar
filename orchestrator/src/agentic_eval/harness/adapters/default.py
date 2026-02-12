"""Default Harbor-backed harness adapters."""

from __future__ import annotations

from ..config import Agent
from .base import HarnessAdapter


class HarborHarnessAdapter(HarnessAdapter):
    """Adapter that simply proxies to Harbor with minimal validation."""

    provider_constraints: dict[Agent, set[str]] = {}

    def validate(self) -> None:  # noqa: D401
        allowed = self.provider_constraints.get(self.config.agent)
        if allowed and self.config.model.provider not in allowed:
            allowed_str = ", ".join(sorted(allowed))
            raise ValueError(
                f"{self.config.agent.value} harness requires model providers: {allowed_str}. "
                f"Received '{self.config.model.provider}'."
            )

    def extra_harbor_args(self) -> list[str]:
        return []
