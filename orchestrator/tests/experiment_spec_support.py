"""Builders and in-memory registry resolvers for experiment spec tests."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml
from raidar_contract.canonical import digest_json

from raidar.contract.references import (
    RefKind,
    RegistryRef,
    RegistryReferenceError,
    RegistryResolver,
    ResolvedReference,
    ResolverSet,
)
from raidar.design.experiment.loader import CliOverrides, load_experiment
from raidar.design.experiment.lock import build_lock, render_lock
from raidar.scoring.framework.metrics import MetricResolver
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
        {"id": "baseline", "agent": "luna", "workflow": "raidar/one-shot@2"},
        {"id": "guided", "agent": "luna", "workflow": "raidar/one-shot@2"},
    ],
    "assessment": {"scorers": ["correctness@10"]},
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
            RefKind.SCORER, {"correctness@10": {"exact": True}}, tool_versions={"pytest": "8.4.1"}
        ),
    ]


def pin_spec_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the whole-package source digest a lock records with a constant.

    Hashing the spec and contract sources on every lock dominates a fast test; the digest's own
    rules (cosmetic edits do not relock) are tested in
    ``test_source_digest_ignores_cosmetic_edits``.
    """
    monkeypatch.setattr("raidar.design.experiment.lock.spec_source_digest", lambda: "0" * 64)


def lock_bytes(path: Path, *, checkpoints: int = 8, overrides: CliOverrides | None = None) -> bytes:
    """The rendered lock of ``path`` over in-memory problem/scorer resolvers."""
    resolvers = ResolverSet(
        [*fixture_resolvers(checkpoints=checkpoints), MetricResolver()], entry_point_group=None
    )
    loaded = load_experiment(path, overrides)
    return render_lock(
        build_lock(loaded, lock_path=path.with_name("experiment.lock.json"), resolvers=resolvers)
    )


def lock_document(path: Path, **kwargs: Any) -> dict[str, Any]:
    return json.loads(lock_bytes(path, **kwargs))
