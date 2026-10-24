"""The built-in scorer registrations: which scorers exist and how they are bound.

The registry machinery is ``raidar.scoring.framework.registry``; this module is the top of the
scoring group, where the built-in manifests, their implementation paths, their store bindings and
their problem-, protocol- and errata-derived config keys are put together. The lock resolves
``scorer`` references here through the ``raidar.contract.resolvers`` entry point.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from raidar.contract.canonical import digest_json
from raidar.contract.references import RefKind, RegistryRef, ResolvedReference
from raidar.scoring.framework.builtin_manifests import BUILTIN_MANIFESTS
from raidar.scoring.framework.manifest import ScorerManifest
from raidar.scoring.framework.registry import (
    ScorerRegistration,
    ScorerRegistry,
    tool_lock_for,
    tool_versions,
)
from raidar.scoring.gate_effort.manifest import GATE_EFFORT
from raidar.scoring.process_checks.manifest import PROCESS_COMPLIANCE

REGISTERED_MANIFESTS = (*BUILTIN_MANIFESTS, PROCESS_COMPLIANCE, GATE_EFFORT)

BUILTIN_IMPLEMENTATIONS: dict[str, str] = {
    "correctness@1": "raidar.scoring.correctness:correctness_scorer",
    "code-quality@3": "raidar.scoring.code_quality:code_quality_scorer",
    "resource-usage@1": "raidar.scoring.resource_usage:resource_usage_scorer",
    "process-compliance@6": (
        "raidar.scoring.process_checks:deterministic_process_compliance_scorer"
    ),
    "gate-effort@10": "raidar.scoring.gate_effort:gate_effort_scorer",
}
# Scorers bound to the assessment store at rescore time.
BUILTIN_STORE_IMPLEMENTATIONS: dict[str, str] = {
    # gate-effort reads the offline grades of guardrail-gate's pre-fix snapshots,
    # which ``raidar assess --rescore`` writes to the store (raidar.grading.prefix.grade).
    "gate-effort@10": "raidar.scoring.gate_effort:gate_effort_scorer_for_store",
    # code-quality reads the offline coverage runs of steps whose trial deferred the
    # coverage pass, which ``raidar assess --rescore`` makes into the store
    # (raidar.grading.coverage.offline).
    "code-quality@3": "raidar.scoring.code_quality:code_quality_scorer_for_store",
}
# Config keys a scorer takes from the problem unless the experiment sets them.
BUILTIN_PROBLEM_CONFIG: dict[str, tuple[str, ...]] = {
    "code-quality@3": ("entry_file",),
}
# Config keys the experiment's protocol sets (``ProtocolSpec`` field names).
BUILTIN_PROTOCOL_CONFIG: dict[str, tuple[str, ...]] = {
    "correctness@1": ("progression",),
}
# Scorers that take the lock's pinned problem errata as config key ``errata``.
ERRATA_CONFIG_KEY = "errata"
BUILTIN_ERRATA_CONFIG: frozenset[str] = frozenset({"correctness@1"})


def _builtin(manifest: ScorerManifest) -> ScorerRegistration:
    return ScorerRegistration(
        manifest,
        BUILTIN_IMPLEMENTATIONS.get(manifest.ref),
        store_implementation=BUILTIN_STORE_IMPLEMENTATIONS.get(manifest.ref),
        problem_config=BUILTIN_PROBLEM_CONFIG.get(manifest.ref, ()),
        protocol_config=BUILTIN_PROTOCOL_CONFIG.get(manifest.ref, ()),
        errata_config=manifest.ref in BUILTIN_ERRATA_CONFIG,
    )


def default_scorer_registry() -> ScorerRegistry:
    """Built-in scorers, with their store bindings and problem-derived config keys."""
    return ScorerRegistry(_builtin(manifest) for manifest in REGISTERED_MANIFESTS)


@dataclass(frozen=True)
class ScorerResolver:
    """``RegistryResolver`` for kind ``scorer``."""

    registry: ScorerRegistry

    @property
    def kind(self) -> RefKind:
        return RefKind.SCORER

    def resolve(self, ref: RegistryRef, *, base_dir: Path) -> ResolvedReference:
        del base_dir
        registration = self.registry.get(str(ref))
        tool_lock = tool_lock_for(registration.manifest)
        metadata: dict[str, object] = {
            "manifest": registration.manifest.definition(),
            "implementation": registration.implementation,
            "implementation_digest": registration.implementation_digest(),
            "tool_lock": tool_lock,
        }
        return ResolvedReference(
            ref=ref,
            digest=digest_json(metadata),
            metadata=metadata,
            tool_versions=tool_versions(tool_lock),
            binding={"runtimes": list(registration.manifest.runtimes)},
        )


def scorer_resolver() -> ScorerResolver:
    """Entry-point factory (group ``raidar.contract.resolvers``, name ``scorer``)."""
    return ScorerResolver(default_scorer_registry())
