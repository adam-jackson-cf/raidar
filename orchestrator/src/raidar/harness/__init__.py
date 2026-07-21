"""Canonical harness definitions."""

from raidar.harness.definitions import (
    SECRET_FILE_ENV_PREFIX,
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
from raidar.harness.result_manifest import HarborArtifactRole, HarborResultManifest

__all__ = [
    "CredentialRedaction",
    "CredentialSourceType",
    "CredentialSpec",
    "CredentialTransport",
    "HarborArtifactRole",
    "HarborResultManifest",
    "HarnessDefinition",
    "HarnessDefinitionError",
    "SECRET_FILE_ENV_PREFIX",
    "harness_definition",
    "harness_rule_filenames",
    "registered_harness_definitions",
    "validate_harness_registry",
]
