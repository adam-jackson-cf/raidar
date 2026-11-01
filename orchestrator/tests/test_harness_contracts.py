from pathlib import Path
from typing import Any

import pytest

from raidar.scoring.judge.redaction import redact
from raidar.subject.agent.config import Harness, agent_cli, agent_name_for
from raidar.subject.agent.harness import (
    CredentialRedaction,
    CredentialSourceType,
    CredentialSpec,
    CredentialTransport,
    HarnessDefinition,
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
    assert set(harness_rule_filenames()) == {"AGENTS.md", "CLAUDE.md"}
    with pytest.raises(HarnessDefinitionError, match="Unknown harness 'nope'"):
        harness_definition("nope")


def _credential(**overrides: Any) -> CredentialSpec:
    fields: dict[str, Any] = {
        "id": "token",
        "accepted_names": ("TOKEN",),
        "source_type": CredentialSourceType.ENVIRONMENT,
        "required": True,
        "transport": CredentialTransport.SECRET_FILE,
        "transport_name": "TOKEN",
        "mount_path": "/tmp/secrets/token",
        "redaction": CredentialRedaction(assignment_names=("TOKEN",)),
    }
    return CredentialSpec(**(fields | overrides))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"id": ""}, "require id, accepted names"),
        ({"accepted_names": ()}, "require id, accepted names"),
        ({"transport_name": ""}, "require id, accepted names"),
        ({"export_names": ("OTHER",)}, "redaction misses names: OTHER"),
        ({"mount_path": None}, "requires mount_path"),
    ],
    ids=["no-id", "no-names", "no-transport-name", "unredacted-export", "no-mount"],
)
def test_a_credential_that_could_leak_or_not_be_transported_is_refused_at_definition(
    overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _credential(**overrides)


def test_a_credential_that_declares_all_it_needs_is_accepted() -> None:
    assert _credential().redaction.assignment_names == ("TOKEN",)


def test_the_registry_refuses_inconsistent_definitions(monkeypatch: pytest.MonkeyPatch) -> None:
    good = _credential()
    cases = {
        "key mismatch": ({"a": HarnessDefinition("b", None, (good,))}, "does not match"),
        "duplicate credentials": (
            {"a": HarnessDefinition("a", None, (good, good))},
            "duplicate credential identifiers",
        ),
        "untyped transport": (
            {"a": HarnessDefinition("a", None, (_credential(transport="env"),))},
            "has no transport",
        ),
    }
    for definitions, message in cases.values():
        monkeypatch.setattr("raidar.subject.agent.harness._DEFINITIONS", definitions)
        with pytest.raises(HarnessDefinitionError, match=message):
            validate_harness_registry()


def test_every_harness_maps_to_a_distinct_agent_and_back() -> None:
    names = [agent_name_for(harness) for harness in Harness]

    assert len(set(names)) == len(names)
    assert [agent_cli(name) for name in names] == list(Harness)
