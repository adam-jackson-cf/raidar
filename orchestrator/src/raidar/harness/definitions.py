"""Harness definitions: the rules filename each harness reads and its host credentials."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


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
class HarnessDefinition:
    """Canonical harness metadata: its rules filename and host credential contract."""

    id: str
    rule_filename: str | None
    credentials: tuple[CredentialSpec, ...] = ()


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
        rule_filename="AGENTS.md",
        credentials=(_OPENAI_API_KEY, _CODEX_AUTH_JSON),
    ),
    "claude-code": HarnessDefinition(
        id="claude-code",
        rule_filename="CLAUDE.md",
        credentials=(_CLAUDE_API_KEY, _CLAUDE_OAUTH_TOKEN),
    ),
    "gemini": HarnessDefinition(
        id="gemini",
        rule_filename="GEMINI.md",
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
    ),
    "cursor": HarnessDefinition(
        id="cursor",
        rule_filename="user-rules-setting.md",
        credentials=(_environment_credential("CURSOR_API_KEY"),),
    ),
    "copilot": HarnessDefinition(
        id="copilot",
        rule_filename="copilot-instructions.md",
        credentials=(_environment_credential("COPILOT_API_KEY"),),
    ),
    "pi": HarnessDefinition(
        id="pi",
        rule_filename="AGENTS.md",
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
