"""Resolved immutable runtime contract for one run."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.harness import HarnessDefinition, harness_definition
from raidar.runtime.environments import (
    ResolvedEnvironment,
    combined_capability_requirements,
    merge_capability_requirements,
    resolve_scenario_environment,
    scorer_requirements_payload,
)
from raidar.runtime.profile import RuntimeProfile, default_runtime_profile
from raidar.runtime.tool_catalog import tool_catalog_payload
from raidar.runtime.verifier_runners import VerifierRunnerDefinition, verifier_runner_definition
from raidar.schemas.environment import CapabilityRequirements


@dataclass(frozen=True, slots=True)
class EffectiveRunContract:
    """One resolved contract consumed by runtime phases."""

    environment: ResolvedEnvironment
    verifier_runner: VerifierRunnerDefinition
    harness: HarnessDefinition | None
    runtime_profile: RuntimeProfile
    provided_capabilities: CapabilityRequirements
    required_capabilities: CapabilityRequirements
    cache_payload: dict[str, Any]
    contract_hash: str

    @property
    def id(self) -> str:
        return self.contract_hash[:16]


def resolve_effective_run_contract(
    *,
    scenario,
    scenario_path: Path,
    repo_root: Path,
    harness_id: str | None = None,
    runtime_profile: RuntimeProfile | None = None,
) -> EffectiveRunContract:
    """Resolve scenario, environment, verifier, catalog, and profile inputs."""

    profile = runtime_profile or default_runtime_profile()
    environment = resolve_scenario_environment(
        scenario=scenario,
        scenario_path=scenario_path,
        repo_root=repo_root,
    )
    verifier = verifier_runner_definition(environment.library.verifier.runner)
    harness = harness_definition(harness_id) if harness_id else None
    required = combined_capability_requirements(scenario=scenario, environment=environment)
    if harness is not None:
        required = merge_capability_requirements(required, harness.execution_requirements)
    payload = {
        "schema_version": "1",
        "environment": environment.cache_payload(),
        "verifier_runner": verifier.cache_payload(),
        "harness": harness.cache_payload() if harness else None,
        "runtime_profile": profile.cache_payload(),
        "tool_catalog": tool_catalog_payload(),
        "scorer_requirements": scorer_requirements_payload(scenario),
        "provided_capabilities": environment.library.capabilities.model_dump(mode="json"),
        "required_capabilities": required.model_dump(mode="json"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return EffectiveRunContract(
        environment=environment,
        verifier_runner=verifier,
        harness=harness,
        runtime_profile=profile,
        provided_capabilities=environment.library.capabilities,
        required_capabilities=required,
        cache_payload=payload,
        contract_hash=hashlib.sha256(encoded).hexdigest(),
    )
