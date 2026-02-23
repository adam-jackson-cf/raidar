"""Base interface for harness adapters.

Adapters encapsulate harness-specific validation, environment preparation,
model compatibility checks, and Harbor argument generation.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycles avoided at runtime
    from ..config import HarnessConfig


class HarnessAdapter:
    """Base adapter contract for all harness integrations."""

    terminal_bench_dataset = "terminal-bench@2.0"

    def __init__(self, config: HarnessConfig) -> None:
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

    def harbor_agent_import_path(self) -> str | None:
        """Optional Harbor import path for custom repository-local agents."""
        return None

    def model_argument(self) -> str:
        """Render the model argument passed to Harbor."""
        return self.config.model.qualified_name

    def extra_harbor_args(self) -> Iterable[str]:
        """Adapters can append additional Harbor CLI flags."""
        return []

    def build_harbor_command(
        self,
        task_path: Path | None = None,
        job_name: str | None = None,
        jobs_dir: Path | None = None,
    ) -> list[str]:
        """Construct the Harbor CLI command for this adapter."""
        cmd: list[str] = [
            "harbor",
            "run",
        ]

        if task_path is not None:
            cmd.extend(["--path", str(task_path)])
        else:
            cmd.extend(["-d", self.terminal_bench_dataset, "-l", "1"])

        if job_name:
            cmd.extend(["--job-name", job_name])
        if jobs_dir is not None:
            cmd.extend(["--jobs-dir", str(jobs_dir)])

        import_path = self.harbor_agent_import_path()
        if import_path:
            cmd.extend(["--agent-import-path", import_path])
        else:
            cmd.extend(["-a", self.harbor_agent()])

        cmd.extend(
            [
                "--n-concurrent",
                "1",
                "-m",
                self.model_argument(),
                *self.extra_harbor_args(),
            ]
        )
        return cmd
