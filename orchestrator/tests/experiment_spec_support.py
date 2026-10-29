"""Builders and in-memory registry resolvers for experiment spec tests."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from raidar.contract.canonical import digest_json
from raidar.contract.references import (
    RefKind,
    RegistryRef,
    RegistryReferenceError,
    RegistryResolver,
    ResolvedReference,
)
from raidar.subject.releases import AgentResolver, WorkflowResolver

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENTS = REPO_ROOT / "lab" / "experiments"
SMOKE_EXPERIMENT = (
    EXPERIMENTS / "smoke" / "python__circuit_eval-cp1__agent-health" / "experiment.yaml"
)
AGENTS_BENCHMARK = (
    EXPERIMENTS / "benchmark" / "python__circuit_eval-cp1__agents" / "experiment.yaml"
)
MECHANICS_SMOKE = EXPERIMENTS / "smoke" / "python__forge__mechanics" / "experiment.yaml"

BASE_EXPERIMENT: dict[str, Any] = {
    "schema_version": 3,
    "kind": "ab-test",
    "id": "python__fixture__test",
    "problems": [{"ref": "scb/forge@abc123"}],
    "runtime": "python",
    "agents": [{"id": "luna", "release": "codex@3", "model": "gpt-6-luna", "reasoning": "low"}],
    "arms": [
        {"id": "baseline", "agent": "luna", "workflow": "one-shot@1"},
        {"id": "guided", "agent": "luna", "workflow": "one-shot@1"},
    ],
    "assessment": {"scorers": ["correctness@9"]},
    "analysis": {"baseline_arm": "baseline", "attempts": 5, "seed": 7},
}


def experiment_document(**changes: Any) -> dict[str, Any]:
    document = copy.deepcopy(BASE_EXPERIMENT)
    document.update(changes)
    return document


def write_experiment(root: Path, document: Mapping[str, Any]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "experiment.yaml"
    path.write_text(yaml.safe_dump(dict(document), sort_keys=False), encoding="utf-8")
    return path


DEFAULT_BINDINGS: dict[RefKind, dict[str, object]] = {
    RefKind.PROBLEM: {"runtime": "python", "pack_version": 1, "validated": True},
    RefKind.SCORER: {"runtimes": ["*"]},
    RefKind.METRIC: {"runtimes": ["python"]},
}
"""Compatibility-gate facts an in-memory reference reports unless ``bindings`` overrides them."""


@dataclass
class InMemoryResolver:
    """Registry resolver over a fixed ``ref -> metadata`` table."""

    resolver_kind: RefKind
    entries: dict[str, dict[str, object]] = field(default_factory=dict)
    tool_versions: dict[str, str] = field(default_factory=dict)
    bindings: dict[str, dict[str, object]] = field(default_factory=dict)

    @property
    def kind(self) -> RefKind:
        return self.resolver_kind

    def resolve(self, ref: RegistryRef, *, base_dir: Path) -> ResolvedReference:
        del base_dir
        try:
            metadata = self.entries[str(ref)]
        except KeyError as exc:
            raise RegistryReferenceError(f"unknown {self.kind.value} {ref}") from exc
        return ResolvedReference(
            ref=ref,
            digest=digest_json({"ref": str(ref), **metadata}),
            metadata=metadata,
            tool_versions=self.tool_versions,
            binding=self.bindings.get(str(ref), DEFAULT_BINDINGS.get(self.kind, {})),
        )


def fixture_resolvers(*, checkpoints: int = 8) -> list[RegistryResolver]:
    """In-memory problem and scorer resolvers, plus the tree's real agent/workflow releases."""
    return [
        InMemoryResolver(RefKind.PROBLEM, {"scb/forge@abc123": {"checkpoints": checkpoints}}),
        AgentResolver(),
        WorkflowResolver(),
        InMemoryResolver(
            RefKind.SCORER, {"correctness@9": {"exact": True}}, tool_versions={"pytest": "8.4.1"}
        ),
    ]
