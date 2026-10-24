"""Base interface for harness adapters.

Adapters own each harness's model support table (which ``raidar validate``
checks agent entries against) and its host-side credential and CLI checks
(``raidar agent validate``). Trials themselves run Harbor's installed agents
(``raidar.engine.trial.agent_config``).
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from raidar.subject.agent.harness import (
    CredentialSourceType,
    CredentialSpec,
    HarnessDefinition,
    harness_definition,
)

if TYPE_CHECKING:  # pragma: no cover - import cycles avoided at runtime
    from ..config import AgentSpec


def harbor_executable() -> str:
    """Return the project-pinned Harbor CLI, installed next to this interpreter.

    Falls back to ``harbor`` on PATH only when the pinned entrypoint is absent
    (for example when raidar is imported outside its uv-managed environment).
    """
    candidate = Path(sys.executable).parent / "harbor"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return "harbor"


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


@dataclass(frozen=True, slots=True)
class ResolvedCredential:
    """One available credential paired with its owning harness contract."""

    spec: CredentialSpec
    source_name: str
    value: str | Path


def _missing_required_names(
    credentials: tuple[CredentialSpec, ...],
    resolved_ids: set[str],
) -> tuple[str, ...]:
    missing: list[str] = []
    for credential in credentials:
        if (
            credential.required
            and credential.requirement_group is None
            and credential.id not in resolved_ids
        ):
            missing.extend(credential.accepted_names)
    return tuple(dict.fromkeys(missing))


def _missing_required_group(
    credentials: tuple[CredentialSpec, ...],
    resolved_ids: set[str],
) -> tuple[str, tuple[str, ...]] | None:
    grouped: dict[str, list[CredentialSpec]] = {}
    for credential in credentials:
        if credential.required and credential.requirement_group is not None:
            grouped.setdefault(credential.requirement_group, []).append(credential)
    for group, alternatives in sorted(grouped.items()):
        if any(credential.id in resolved_ids for credential in alternatives):
            continue
        names = tuple(
            dict.fromkeys(name for credential in alternatives for name in credential.accepted_names)
        )
        return group, names
    return None


class HarnessAdapter:
    """Base adapter contract for all harness integrations."""

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

    def runtime_env(self) -> dict[str, str]:
        """Extra environment variables required for the harness runtime."""
        return {}

    @property
    def definition(self) -> HarnessDefinition:
        """Return the canonical definition for this adapter's harness."""

        return harness_definition(self.config.harness.value)

    def credential_environment_sources(self) -> dict[str, str]:
        """Return available environment-backed sources declared by the harness."""

        names = {
            name
            for credential in self.definition.credentials
            if credential.source_type is CredentialSourceType.ENVIRONMENT
            for name in credential.accepted_names
        }
        return {name: value for name in names if (value := os.environ.get(name))}

    def credential_file_sources(self) -> dict[str, Path]:
        """Return available file-backed sources declared by the harness."""

        return {}

    def resolved_credentials(self) -> tuple[ResolvedCredential, ...]:
        """Resolve declared credential alternatives without duplicating policy."""

        environment_sources = self.credential_environment_sources()
        file_sources = self.credential_file_sources()
        resolved: list[ResolvedCredential] = []
        for credential in self.definition.credentials:
            sources: Mapping[str, str | Path]
            if credential.source_type is CredentialSourceType.ENVIRONMENT:
                sources = environment_sources
            else:
                sources = file_sources
            source_name = next(
                (name for name in credential.accepted_names if name in sources),
                None,
            )
            if source_name is not None:
                resolved.append(
                    ResolvedCredential(
                        spec=credential,
                        source_name=source_name,
                        value=sources[source_name],
                    )
                )
        return tuple(resolved)

    def _validate_credentials(self) -> None:
        """Validate required credential groups from the harness definition."""

        resolved_ids = {credential.spec.id for credential in self.resolved_credentials()}
        missing_names = _missing_required_names(self.definition.credentials, resolved_ids)
        if missing_names:
            raise OSError(
                f"{self.config.harness.value} runs require credentials; "
                f"set: {', '.join(missing_names)}."
            )

        missing_group = _missing_required_group(self.definition.credentials, resolved_ids)
        if missing_group is None:
            return
        group, names = missing_group
        raise OSError(
            f"{self.config.harness.value} runs require credentials for {group}; "
            f"set one of: {', '.join(names)}."
        )

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
    # Harbor identity
    # ------------------------------------------------------------------
    def harbor_harness(self) -> str:
        """Return the Harbor harness identifier passed through Harbor's agent flag."""
        return self.config.harness.value

    def model_argument(self) -> str:
        """Render the model argument passed to Harbor."""
        return self.config.model.qualified_name
