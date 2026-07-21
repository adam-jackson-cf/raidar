from pathlib import Path

from raidar.agents.config import Harness
from raidar.harness import (
    CredentialSourceType,
    CredentialTransport,
    registered_harness_definitions,
    validate_harness_registry,
)
from raidar.runtime.harbor_env import _redact_sensitive_text


def test_registry_credentials_are_transportable_and_redacted() -> None:
    validate_harness_registry()
    definitions = registered_harness_definitions()

    assert {definition.id for definition in definitions} == {harness.value for harness in Harness}
    for definition in definitions:
        assert definition.credentials
        for credential in definition.credentials:
            assert credential.required is True
            assert credential.source_type in CredentialSourceType
            assert credential.transport in CredentialTransport
            if credential.transport is CredentialTransport.SECRET_FILE:
                assert credential.mount_path
            for name in credential.redaction.assignment_names:
                assert "secret-value" not in _redact_sensitive_text(
                    f'{name}=secret-value {{"{name}": "secret-value"}}'
                )


def test_pi_token_and_openai_value_shapes_are_redacted() -> None:
    assert _redact_sensitive_text("PI_API_TOKEN=pi-secret") == "PI_API_TOKEN=[REDACTED]"
    assert _redact_sensitive_text("failed sk-abcdefghijklmnop") == "failed [REDACTED]"


def test_file_credentials_declare_file_sources_and_mounts() -> None:
    codex = next(
        definition
        for definition in registered_harness_definitions()
        if definition.id == "codex-cli"
    )
    auth_file = next(
        credential for credential in codex.credentials if credential.id == "codex-auth-json"
    )

    assert auth_file.source_type is CredentialSourceType.FILE
    assert auth_file.transport is CredentialTransport.SECRET_FILE
    assert Path(auth_file.mount_path or "").is_absolute()
