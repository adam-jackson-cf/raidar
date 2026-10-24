from pathlib import Path

import pytest

from raidar.scoring.judge.redaction import redact
from raidar.subject.agent.config import Harness
from raidar.subject.agent.harness import (
    CredentialSourceType,
    CredentialTransport,
    HarnessDefinitionError,
    harness_definition,
    harness_rule_filenames,
    registered_harness_definitions,
    validate_harness_registry,
)


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
            assert set(credential.accepted_names) <= set(credential.redaction.assignment_names)


def test_openai_key_shape_is_redacted_before_leaving_the_host() -> None:
    assert "sk-abcdefghijklmnop" not in redact("failed sk-abcdefghijklmnopqrstuv")


def test_file_credentials_declare_file_sources_and_mounts() -> None:
    codex = harness_definition("codex-cli")
    auth_file = next(
        credential for credential in codex.credentials if credential.id == "codex-auth-json"
    )

    assert auth_file.source_type is CredentialSourceType.FILE
    assert auth_file.transport is CredentialTransport.SECRET_FILE
    assert Path(auth_file.mount_path or "").is_absolute()


def test_rule_filenames_cover_every_harness_and_unknown_harnesses_are_refused() -> None:
    assert set(harness_rule_filenames()) == {
        "AGENTS.md",
        "CLAUDE.md",
        "GEMINI.md",
        "copilot-instructions.md",
        "user-rules-setting.md",
    }
    with pytest.raises(HarnessDefinitionError, match="Unknown harness 'nope'"):
        harness_definition("nope")
