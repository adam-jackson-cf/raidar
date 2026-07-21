"""Resolved immutable runtime contract for one run."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.config import settings
from raidar.harness import HarnessDefinition, harness_definition
from raidar.runtime.environments import (
    ResolvedEnvironment,
    ResolvedEnvironmentRegistry,
    combined_capability_requirements,
    merge_capability_requirements,
    resolve_environment_registry,
    resolve_scenario_environment,
    scorer_requirements_payload,
)
from raidar.runtime.profile import RuntimeProfile, default_runtime_profile
from raidar.runtime.tool_catalog import tool_catalog_payload
from raidar.schemas.environment import CapabilityRequirements
from raidar.scorers.base import scorer_class
from raidar.scorers.verification import (
    EVIDENCE_RUNNER_CONTRACT_VERSION,
    EvidencePlanInput,
    NormalizedVerificationPlan,
    VerificationPlanContext,
    VisualComparisonToolPolicy,
    normalize_verification_plans,
)


@dataclass(frozen=True, slots=True)
class EffectiveRunContract:
    """One resolved contract consumed by runtime phases."""

    environment: ResolvedEnvironment
    environment_registry: ResolvedEnvironmentRegistry
    verification_plan: NormalizedVerificationPlan
    harness: HarnessDefinition | None
    runtime_profile: RuntimeProfile
    visual_comparison: VisualComparisonToolPolicy
    provided_capabilities: CapabilityRequirements
    required_capabilities: CapabilityRequirements
    cache_payload: dict[str, Any]
    contract_hash: str

    @property
    def id(self) -> str:
        return self.contract_hash[:16]

    def evidence_runner_payload(self, *, workspace: str, output_dir: str) -> dict[str, Any]:
        """Return fully resolved, validated input for the bundled evidence runner."""

        payload = self.verification_plan.payload()
        payload.update(
            {
                "workspace": workspace,
                "output_dir": output_dir,
                "limits": self.runtime_profile.evidence_limits.payload(),
                "archive_excludes": list(self.runtime_profile.archive_excludes),
            }
        )
        return EvidencePlanInput.model_validate(payload).model_dump(mode="json")


def resolve_effective_run_contract(
    *,
    scenario,
    scenario_path: Path,
    repo_root: Path,
    harness_id: str | None = None,
    runtime_profile: RuntimeProfile | None = None,
    visual_comparison_policy: VisualComparisonToolPolicy | None = None,
) -> EffectiveRunContract:
    """Resolve scenario, environment, evidence runner, catalog, and profile inputs."""

    profile = runtime_profile or default_runtime_profile()
    visual_comparison = visual_comparison_policy or VisualComparisonToolPolicy(
        executable=settings.visual.comparison_executable,
        arguments_template=settings.visual.comparison_arguments_template,
        threshold=settings.visual.odiff_threshold,
        timeout_sec=settings.timeouts.image_compare,
    )
    environment_registry = resolve_environment_registry(
        repo_root=repo_root,
        runtime_profile=profile,
    )
    environment = resolve_scenario_environment(
        scenario=scenario,
        scenario_path=scenario_path,
        repo_root=repo_root,
        registry=environment_registry,
        docker_registry_allowlist=profile.docker_registry_allowlist,
    )
    verification_plan = _resolve_verification_plan(
        scenario=scenario,
        scenario_path=scenario_path,
        environment=environment,
        visual_comparison=visual_comparison,
    )
    harness = harness_definition(harness_id) if harness_id else None
    required = combined_capability_requirements(scenario=scenario, environment=environment)
    if harness is not None:
        required = merge_capability_requirements(required, harness.execution_requirements)
    payload = {
        "schema_version": "1",
        "environment_registry": environment_registry.cache_payload(),
        "environment": environment.cache_payload(),
        "evidence_runner_contract": EVIDENCE_RUNNER_CONTRACT_VERSION,
        "verification_plan": verification_plan.payload(),
        "verification_plan_hash": verification_plan.hash(),
        "harness": harness.cache_payload() if harness else None,
        "runtime_profile": profile.cache_payload(),
        "visual_comparison": visual_comparison.cache_payload(),
        "tool_catalog": tool_catalog_payload(),
        "scorer_requirements": scorer_requirements_payload(scenario),
        "provided_capabilities": environment.library.capabilities.model_dump(mode="json"),
        "required_capabilities": required.model_dump(mode="json"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return EffectiveRunContract(
        environment=environment,
        environment_registry=environment_registry,
        verification_plan=verification_plan,
        harness=harness,
        runtime_profile=profile,
        visual_comparison=visual_comparison,
        provided_capabilities=environment.library.capabilities,
        required_capabilities=required,
        cache_payload=payload,
        contract_hash=hashlib.sha256(encoded).hexdigest(),
    )


def _resolve_verification_plan(
    *,
    scenario,
    scenario_path: Path,
    environment: ResolvedEnvironment,
    visual_comparison: VisualComparisonToolPolicy,
) -> NormalizedVerificationPlan:
    plans = []
    scenario_dir = scenario_path.parent
    for resolved_scorer in scenario.resolved_scorers():
        scorer = scorer_class(resolved_scorer.id, resolved_scorer.version)()
        plans.append(
            scorer.verification_plan(
                VerificationPlanContext(
                    scenario=scenario,
                    resolved_scorer=resolved_scorer,
                    scenario_dir=scenario_dir,
                    environment_capabilities=environment.library.capabilities,
                    visual_comparison=visual_comparison,
                )
            )
        )
    dependencies = {
        resolved_scorer.id: resolved_scorer.evidence_dependencies
        for resolved_scorer in scenario.resolved_scorers()
    }
    return normalize_verification_plans(tuple(plans), dependencies)
