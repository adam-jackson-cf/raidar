"""Base interface for harness adapters.

Adapters encapsulate harness-specific validation, environment preparation,
model compatibility checks, and Harbor argument generation.
"""

from __future__ import annotations

from abc import ABC
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:  # pragma: no cover - import cycles avoided at runtime
    from ..config import HarnessConfig


class HarnessAdapter(ABC):
    """Base adapter contract for all harness integrations."""

    terminal_bench_dataset = "terminal-bench@2.0"

    def __init__(self, config: "HarnessConfig") -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------
    def validate(self) -> None:
        """Validate configuration or environment prior to execution."""

    def prepare_workspace(self, workspace: Path) -> None:
        """Allow adapters to mutate the workspace before Harbor runs."""

    def runtime_env(self) -> dict[str, str]:
        """Extra environment variables required for the harness runtime."""
        return {}

    # ------------------------------------------------------------------
    # Harbor command wiring
    # ------------------------------------------------------------------
    def harbor_agent(self) -> str:
        """Return the Harbor agent identifier."""
        return self.config.agent.value

    def model_argument(self) -> str:
        """Render the model argument passed to Harbor."""
        return self.config.model.litellm_model

    def extra_harbor_args(self) -> Iterable[str]:
        """Adapters can append additional Harbor CLI flags."""
        return []

    def build_harbor_command(self) -> list[str]:
        """Construct the Harbor CLI command for this adapter."""
        return [
            "harbor",
            "run",
            "-d",
            self.terminal_bench_dataset,
            "-a",
            self.harbor_agent(),
            "-m",
            self.model_argument(),
            *self.extra_harbor_args(),
        ]
