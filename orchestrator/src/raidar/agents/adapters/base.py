"""Base interface for harness adapters.

Adapters encapsulate harness-specific validation, workspace preparation,
model compatibility checks, and Harbor argument generation.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import cycles avoided at runtime
    from ..config import AgentSpec


def resolve_cli_executable(
    *,
    cli_env_var: str,
    default_binary: str,
    harness_label: str,
) -> str:
    """Resolve a harness CLI executable from env override or PATH."""
    candidate = os.environ.get(cli_env_var)
    if not candidate and default_binary:
        candidate = shutil.which(default_binary)
    if not candidate:
        raise FileNotFoundError(
            f"{harness_label} CLI not found. Set {cli_env_var} or add '{default_binary}' to PATH."
        )
    return candidate


class HarnessAdapter:
    """Base adapter contract for all harness integrations."""

    terminal_bench_dataset = "terminal-bench@2.0"
    CLI_ENV_VAR: str = ""
    DEFAULT_BINARY: str = ""

    @classmethod
    def supported_model_summary(cls) -> str:
        """Describe the model variants the adapter accepts for CLI discovery output."""
        return "(model coverage not declared)"

    def __init__(self, config: AgentSpec) -> None:
        self.config = config
        self._cli_path: str | None = None

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

    def excluded_run_env_keys(self) -> set[str]:
        """Environment keys that should not be forwarded into Harbor runs."""
        return set()

    def local_secret_files(self) -> dict[str, Path]:
        """Local file artifacts that should be copied into Harbor as secret files."""
        return {}

    def execution_metadata(self) -> dict[str, Any]:
        """Adapter metadata that should be surfaced in validation and run artifacts."""
        return {}

    def _resolve_cli(self) -> str:
        """Resolve and cache this adapter's CLI executable."""
        if self._cli_path:
            return self._cli_path
        self._cli_path = resolve_cli_executable(
            cli_env_var=self.CLI_ENV_VAR,
            default_binary=self.DEFAULT_BINARY,
            harness_label=self.config.harness.value,
        )
        return self._cli_path

    # ------------------------------------------------------------------
    # Harbor command wiring
    # ------------------------------------------------------------------
    def harbor_harness(self) -> str:
        """Return the Harbor harness identifier passed through Harbor's agent flag."""
        return self.config.harness.value

    def harbor_harness_import_path(self) -> str | None:
        """Optional Harbor import path for custom repository-local harnesses."""
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

        import_path = self.harbor_harness_import_path()
        if import_path:
            cmd.extend(["--agent-import-path", import_path])
        else:
            cmd.extend(["-a", self.harbor_harness()])

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
