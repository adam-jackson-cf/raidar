"""Harness execution, artifact, parser, and usage definitions."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from raidar.schemas.environment import CapabilityRequirements

SECRET_FILE_ENV_PREFIX = "AGENTIC_EVAL_SECRET_FILE_"


class HarnessDefinitionError(ValueError):
    """Raised when a harness is not registered."""


class CredentialSourceType(StrEnum):
    """Supported host-side credential source types."""

    ENVIRONMENT = "environment"
    FILE = "file"


class CredentialTransport(StrEnum):
    """Supported transports from the host into Harbor."""

    ENVIRONMENT = "environment"
    SECRET_FILE = "secret-file"


@dataclass(frozen=True, slots=True)
class CredentialRedaction:
    """Text shapes that must be redacted for one credential."""

    assignment_names: tuple[str, ...]
    value_patterns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CredentialSpec:
    """Harness-owned credential source, transport, and redaction contract."""

    id: str
    accepted_names: tuple[str, ...]
    source_type: CredentialSourceType
    required: bool
    transport: CredentialTransport
    transport_name: str
    redaction: CredentialRedaction
    requirement_group: str | None = None
    mount_path: str | None = None
    export_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id or not self.accepted_names or not self.transport_name:
            raise ValueError("credential specs require id, accepted names, and transport name")
        names_to_redact = {*self.accepted_names, *self.export_names, self.transport_name}
        if not names_to_redact.issubset(self.redaction.assignment_names):
            missing = ", ".join(sorted(names_to_redact - set(self.redaction.assignment_names)))
            raise ValueError(f"credential {self.id!r} redaction misses names: {missing}")
        if self.transport is CredentialTransport.SECRET_FILE and not self.mount_path:
            raise ValueError(f"secret-file credential {self.id!r} requires mount_path")


@dataclass(frozen=True, slots=True)
class UsagePolicy:
    """Token usage support for a harness."""

    supported: bool
    required: bool
    parser: str | None = None


@dataclass(frozen=True, slots=True)
class HarnessDefinition:
    """Canonical harness metadata consumed by runtime phases."""

    id: str
    npm_package: str | None
    npm_version_probe: tuple[str, ...] | None
    execution_requirements: CapabilityRequirements
    artifact_files: tuple[str, ...]
    final_workspace_archive: str
    rule_filename: str | None
    event_stream_pointer: str
    command_parser: str
    usage_policy: UsagePolicy
    emits_structured_trace_events: bool
    trace_parser: str | None
    credentials: tuple[CredentialSpec, ...] = ()
    additional_stdout_files: tuple[str, ...] = ()

    def npm_install_spec(self) -> str | None:
        """Return a concrete npm install spec for in-image harness execution."""

        if self.npm_package is None:
            return None
        if self.npm_version_probe is None:
            return self.npm_package
        version = _local_cli_version(self.npm_version_probe)
        return f"{self.npm_package}@{version}" if version else self.npm_package

    def dockerfile_install_fragment(self) -> str:
        """Return Dockerfile instructions needed for in-image harness execution."""

        install_spec = self.npm_install_spec()
        if install_spec is None:
            return ""
        return f"""RUN apt-get update && apt-get install -y --no-install-recommends \\
  npm \\
  && rm -rf /var/lib/apt/lists/*
RUN npm install -g {install_spec}
"""

    def cache_payload(self) -> dict[str, Any]:
        """Return stable harness material for runtime cache keys."""

        return {
            "id": self.id,
            "npm_package": self.npm_package,
            "npm_install_spec": self.npm_install_spec(),
            "dockerfile_install_fragment": self.dockerfile_install_fragment(),
            "execution_requirements": self.execution_requirements.model_dump(mode="json"),
            "artifact_files": list(self.artifact_files),
            "final_workspace_archive": self.final_workspace_archive,
            "rule_filename": self.rule_filename,
            "event_stream_pointer": self.event_stream_pointer,
            "command_parser": self.command_parser,
            "usage_policy": {
                "supported": self.usage_policy.supported,
                "required": self.usage_policy.required,
                "parser": self.usage_policy.parser,
            },
            "emits_structured_trace_events": self.emits_structured_trace_events,
            "trace_parser": self.trace_parser,
            "credentials": [
                {
                    "id": credential.id,
                    "accepted_names": list(credential.accepted_names),
                    "source_type": credential.source_type.value,
                    "required": credential.required,
                    "requirement_group": credential.requirement_group,
                    "transport": credential.transport.value,
                    "transport_name": credential.transport_name,
                    "mount_path": credential.mount_path,
                    "export_names": list(credential.export_names),
                    "redaction": {
                        "assignment_names": list(credential.redaction.assignment_names),
                        "value_patterns": list(credential.redaction.value_patterns),
                    },
                }
                for credential in self.credentials
            ],
            "additional_stdout_files": list(self.additional_stdout_files),
        }


def _codex_version_probe() -> tuple[str, ...]:
    executable = os.environ.get("CODEX_CLI_PATH") or shutil.which("codex") or "codex"
    return (executable, "--version")


def _local_cli_version(command: tuple[str, ...]) -> str | None:
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    match = re.search(r"(\d+\.\d+\.\d+)", completed.stdout or "")
    return match.group(1) if match else None


_COMMON_COMMAND_ARTIFACTS = (
    "install.sh",
    "final-app.tar.gz",
)

_OPENAI_API_KEY = CredentialSpec(
    id="openai-api-key",
    accepted_names=("OPENAI_API_KEY",),
    source_type=CredentialSourceType.ENVIRONMENT,
    required=True,
    requirement_group="authentication",
    transport=CredentialTransport.SECRET_FILE,
    transport_name="OPENAI_API_KEY",
    mount_path="/tmp/agentic-eval-secrets/openai_api_key",
    redaction=CredentialRedaction(
        assignment_names=("OPENAI_API_KEY",),
        value_patterns=(r"\bsk-[A-Za-z0-9_-]{16,}\b",),
    ),
)

_CODEX_AUTH_JSON = CredentialSpec(
    id="codex-auth-json",
    accepted_names=("CODEX_AUTH_JSON",),
    source_type=CredentialSourceType.FILE,
    required=True,
    requirement_group="authentication",
    transport=CredentialTransport.SECRET_FILE,
    transport_name="CODEX_AUTH_JSON",
    mount_path="/tmp/agentic-eval-secrets/codex_auth_json",
    redaction=CredentialRedaction(assignment_names=("CODEX_AUTH_JSON",)),
)

_CLAUDE_API_KEY = CredentialSpec(
    id="claude-api-key",
    accepted_names=("ANTHROPIC_API_KEY", "CLAUDE_CODE_API_KEY"),
    source_type=CredentialSourceType.ENVIRONMENT,
    required=True,
    requirement_group="authentication",
    transport=CredentialTransport.SECRET_FILE,
    transport_name="ANTHROPIC_API_KEY",
    mount_path="/tmp/agentic-eval-secrets/anthropic_api_key",
    export_names=("ANTHROPIC_API_KEY", "CLAUDE_CODE_API_KEY"),
    redaction=CredentialRedaction(
        assignment_names=("ANTHROPIC_API_KEY", "CLAUDE_CODE_API_KEY"),
    ),
)

_CLAUDE_OAUTH_TOKEN = CredentialSpec(
    id="claude-oauth-token",
    accepted_names=("CLAUDE_CODE_OAUTH_TOKEN",),
    source_type=CredentialSourceType.ENVIRONMENT,
    required=True,
    requirement_group="authentication",
    transport=CredentialTransport.SECRET_FILE,
    transport_name="CLAUDE_CODE_OAUTH_TOKEN",
    mount_path="/tmp/agentic-eval-secrets/claude_code_oauth_token",
    export_names=("CLAUDE_CODE_OAUTH_TOKEN",),
    redaction=CredentialRedaction(assignment_names=("CLAUDE_CODE_OAUTH_TOKEN",)),
)


def _environment_credential(name: str) -> CredentialSpec:
    return CredentialSpec(
        id=name.lower().replace("_", "-"),
        accepted_names=(name,),
        source_type=CredentialSourceType.ENVIRONMENT,
        required=True,
        transport=CredentialTransport.ENVIRONMENT,
        transport_name=name,
        export_names=(name,),
        redaction=CredentialRedaction(assignment_names=(name,)),
    )


_DEFINITIONS: dict[str, HarnessDefinition] = {
    "codex-cli": HarnessDefinition(
        id="codex-cli",
        npm_package="@openai/codex",
        npm_version_probe=_codex_version_probe(),
        execution_requirements=CapabilityRequirements(
            runtimes={"node": ">=20"},
            tools={"git": ">=2"},
        ),
        artifact_files=("trajectory.json", "codex.txt", *_COMMON_COMMAND_ARTIFACTS),
        final_workspace_archive="final-app.tar.gz",
        rule_filename="AGENTS.md",
        event_stream_pointer="codex.txt",
        command_parser="codex-jsonl",
        usage_policy=UsagePolicy(supported=True, required=True, parser="codex-jsonl"),
        emits_structured_trace_events=True,
        trace_parser="codex-jsonl",
        credentials=(_OPENAI_API_KEY, _CODEX_AUTH_JSON),
    ),
    "claude-code": HarnessDefinition(
        id="claude-code",
        npm_package="@anthropic-ai/claude-code",
        npm_version_probe=None,
        execution_requirements=CapabilityRequirements(
            runtimes={"node": ">=20"},
            tools={"git": ">=2"},
        ),
        artifact_files=("claude-code.txt", *_COMMON_COMMAND_ARTIFACTS),
        final_workspace_archive="final-app.tar.gz",
        rule_filename="CLAUDE.md",
        event_stream_pointer="commands",
        command_parser="claude-stdout",
        usage_policy=UsagePolicy(supported=True, required=True, parser="claude-jsonl"),
        emits_structured_trace_events=False,
        trace_parser="claude",
        credentials=(_CLAUDE_API_KEY, _CLAUDE_OAUTH_TOKEN),
    ),
    "gemini": HarnessDefinition(
        id="gemini",
        npm_package="@google/gemini-cli",
        npm_version_probe=None,
        execution_requirements=CapabilityRequirements(
            runtimes={"node": ">=20"},
            tools={"git": ">=2"},
        ),
        artifact_files=("gemini-cli.txt", "gemini-cli.trajectory.json", *_COMMON_COMMAND_ARTIFACTS),
        final_workspace_archive="final-app.tar.gz",
        rule_filename="GEMINI.md",
        event_stream_pointer="commands",
        command_parser="gemini",
        usage_policy=UsagePolicy(supported=True, required=True, parser="gemini-trajectory"),
        emits_structured_trace_events=False,
        trace_parser="gemini",
        credentials=(
            CredentialSpec(
                id="gemini-api-key",
                accepted_names=("GEMINI_API_KEY",),
                source_type=CredentialSourceType.ENVIRONMENT,
                required=True,
                transport=CredentialTransport.SECRET_FILE,
                transport_name="GEMINI_API_KEY",
                mount_path="/tmp/agentic-eval-secrets/gemini_api_key",
                export_names=("GEMINI_API_KEY",),
                redaction=CredentialRedaction(assignment_names=("GEMINI_API_KEY",)),
            ),
        ),
        additional_stdout_files=("gemini-cli.txt",),
    ),
    "cursor": HarnessDefinition(
        id="cursor",
        npm_package=None,
        npm_version_probe=None,
        execution_requirements=CapabilityRequirements(tools={"git": ">=2"}),
        artifact_files=_COMMON_COMMAND_ARTIFACTS,
        final_workspace_archive="final-app.tar.gz",
        rule_filename="user-rules-setting.md",
        event_stream_pointer="commands",
        command_parser="command-stdout",
        usage_policy=UsagePolicy(supported=False, required=False),
        emits_structured_trace_events=False,
        trace_parser="cursor",
        credentials=(_environment_credential("CURSOR_API_KEY"),),
    ),
    "copilot": HarnessDefinition(
        id="copilot",
        npm_package=None,
        npm_version_probe=None,
        execution_requirements=CapabilityRequirements(tools={"git": ">=2"}),
        artifact_files=_COMMON_COMMAND_ARTIFACTS,
        final_workspace_archive="final-app.tar.gz",
        rule_filename="copilot-instructions.md",
        event_stream_pointer="commands",
        command_parser="command-stdout",
        usage_policy=UsagePolicy(supported=False, required=False),
        emits_structured_trace_events=False,
        trace_parser="copilot",
        credentials=(_environment_credential("COPILOT_API_KEY"),),
    ),
    "pi": HarnessDefinition(
        id="pi",
        npm_package=None,
        npm_version_probe=None,
        execution_requirements=CapabilityRequirements(tools={"git": ">=2"}),
        artifact_files=_COMMON_COMMAND_ARTIFACTS,
        final_workspace_archive="final-app.tar.gz",
        rule_filename="AGENTS.md",
        event_stream_pointer="commands",
        command_parser="command-stdout",
        usage_policy=UsagePolicy(supported=False, required=False),
        emits_structured_trace_events=False,
        trace_parser="pi",
        credentials=(_environment_credential("PI_API_TOKEN"),),
    ),
}


def validate_harness_registry() -> None:
    """Validate cross-harness definition and credential invariants."""

    for harness_id, definition in _DEFINITIONS.items():
        if definition.id != harness_id:
            raise HarnessDefinitionError(
                f"Harness registry key {harness_id!r} does not match definition {definition.id!r}"
            )
        credential_ids = [credential.id for credential in definition.credentials]
        if len(credential_ids) != len(set(credential_ids)):
            raise HarnessDefinitionError(
                f"Harness {harness_id!r} defines duplicate credential identifiers"
            )
        for credential in definition.credentials:
            if credential.required and not isinstance(
                credential.transport,
                CredentialTransport,
            ):
                raise HarnessDefinitionError(
                    f"Harness {harness_id!r} credential {credential.id!r} has no transport"
                )
            redacted_names = set(credential.redaction.assignment_names)
            if not set(credential.accepted_names).issubset(redacted_names):
                raise HarnessDefinitionError(
                    f"Harness {harness_id!r} credential {credential.id!r} is not redacted"
                )


validate_harness_registry()


def harness_definition(harness_id: str) -> HarnessDefinition:
    """Return a registered harness definition."""

    try:
        return _DEFINITIONS[harness_id]
    except KeyError as exc:
        available = ", ".join(sorted(_DEFINITIONS))
        raise HarnessDefinitionError(
            f"Unknown harness {harness_id!r}; available harnesses: {available}"
        ) from exc


def harness_rule_filenames() -> tuple[str, ...]:
    """Return unique rule filenames declared by registered harnesses."""

    return tuple(
        sorted(
            {
                definition.rule_filename
                for definition in _DEFINITIONS.values()
                if definition.rule_filename is not None
            }
        )
    )


def registered_harness_definitions() -> tuple[HarnessDefinition, ...]:
    """Return every registered definition in stable identifier order."""

    return tuple(_DEFINITIONS[harness_id] for harness_id in sorted(_DEFINITIONS))
