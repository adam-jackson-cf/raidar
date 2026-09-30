"""Component SDK (plan WS-C): manifests, registry, compiler, per-step reset, probes."""

from raidar.components.compiled import (
    HARBOR_AGENT_NAMES,
    CompiledCondition,
    ProbeReport,
    ProbeResult,
)
from raidar.components.compiler import ComponentRejectedError, compile_arm, compile_condition
from raidar.components.contribution import AgentWrapper, Contribution, SupportFile
from raidar.components.evidence import StepEvidence
from raidar.components.manifest import (
    ComponentConfigError,
    ComponentDefinition,
    ComponentKind,
    ComponentManifest,
    ComponentUseContext,
    DiscoveryProbe,
    NetworkNeeds,
    ProbeOutcome,
)
from raidar.components.registry import (
    COMPONENT_ENTRY_POINT_GROUP,
    ComponentRegistry,
    ComponentResolver,
    UnknownComponentError,
    component_resolver,
    fingerprint,
)

__all__ = [
    "COMPONENT_ENTRY_POINT_GROUP",
    "HARBOR_AGENT_NAMES",
    "AgentWrapper",
    "CompiledCondition",
    "ComponentConfigError",
    "ComponentDefinition",
    "ComponentKind",
    "ComponentManifest",
    "ComponentRegistry",
    "ComponentRejectedError",
    "ComponentResolver",
    "ComponentUseContext",
    "Contribution",
    "DiscoveryProbe",
    "NetworkNeeds",
    "ProbeOutcome",
    "ProbeReport",
    "ProbeResult",
    "StepEvidence",
    "SupportFile",
    "UnknownComponentError",
    "compile_arm",
    "compile_condition",
    "component_resolver",
    "fingerprint",
]
