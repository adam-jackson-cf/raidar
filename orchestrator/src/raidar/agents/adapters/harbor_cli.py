"""Shared adapter behaviors for repository-local Harbor CLI harnesses."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..harbor_routing import harbor_agent_import_path, with_harness_pythonpath
from .base import HarnessAdapter

if TYPE_CHECKING:
    from ..config import AgentSpec


@dataclass(frozen=True, slots=True)
class SupportedModelProfile:
    """Model support metadata for adapter validation and reporting."""

    display_label: str
    reasoning_levels: tuple[str, ...] = ()
    default_reasoning: str | None = None


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


class HarborCliAdapter(HarnessAdapter):
    """Reusable base for harnesses executed via repository-local Harbor agents."""

    CLI_ENV_VAR: str = ""
    DEFAULT_BINARY: str = ""
    HARBOR_HARNESS_NAME: str = ""
    WORKSPACE_SESSION_DIR: str = ""
    SUPPORTED_MODELS: dict[str, SupportedModelProfile] = {}

    def __init__(self, config: AgentSpec) -> None:
        super().__init__(config)
        self._cli_path: str | None = None

    def _resolve_cli(self) -> str:
        if self._cli_path:
            return self._cli_path
        self._cli_path = resolve_cli_executable(
            cli_env_var=self.CLI_ENV_VAR,
            default_binary=self.DEFAULT_BINARY,
            harness_label=self.config.harness.value,
        )
        return self._cli_path

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
