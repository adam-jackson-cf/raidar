"""Builders and in-memory registry resolvers for experiment spec tests."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from raidar.spec.canonical import digest_json
from raidar.spec.references import (
    RefKind,
    RegistryRef,
    RegistryReferenceError,
    ResolvedReference,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENTS = REPO_ROOT / "experiments"
PILOT_EXPERIMENT = EXPERIMENTS / "ab-test" / "python__forge__guided-pilot" / "experiment.yaml"
PILOT_ONESHOT = (
    EXPERIMENTS / "ab-test" / "python__circuit_eval-cp1__guided-pilot" / "experiment.yaml"
)
SMOKE_EXPERIMENT = (
    EXPERIMENTS / "smoke" / "python__circuit_eval-cp1__agent-health" / "experiment.yaml"
)
AGENTS_BENCHMARK = (
    EXPERIMENTS / "benchmark" / "python__circuit_eval-cp1__agents" / "experiment.yaml"
)
GUIDED_R3_EXPERIMENT = EXPERIMENTS / "ab-test" / "python__forge__guided-r3" / "experiment.yaml"
GUARDRAILS_EXPERIMENT = EXPERIMENTS / "ab-test" / "python__forge__guardrails" / "experiment.yaml"
LEGACY_V1 = Path(__file__).resolve().parent / "fixtures" / "legacy_v1"

BASE_EXPERIMENT: dict[str, Any] = {
    "schema_version": 2,
    "kind": "ab-test",
    "id": "python__fixture__test",
    "problems": [{"ref": "scb/forge@abc123"}],
    "runtime": "python",
    "agents": [
        {
            "id": "luna",
            "name": "codex",
            "provider": "openai",
            "model": "gpt-6-luna",
            "reasoning_effort": "low",
            "version": "0.156.1",
        }
    ],
    "factors": ["condition"],
    "conditions": [
        {"id": "baseline", "components": []},
        {
            "id": "guided",
            "components": [{"use": "rules-file@1", "files": ["components/AGENTS.md"]}],
        },
    ],
    "arms": [
        {"id": "baseline", "condition": "baseline", "agent": "luna"},
        {"id": "guided", "condition": "guided", "agent": "luna"},
    ],
    "assessment": {"scorers": ["correctness@1"]},
    "analysis": {"baseline_arm": "baseline", "attempts": 5, "seed": 7},
}


def experiment_document(**changes: Any) -> dict[str, Any]:
    document = copy.deepcopy(BASE_EXPERIMENT)
    document.update(changes)
    return document


SHARED_FRAMING = {"use": "prompt-wrapper@1", "files": ["components/framing.md"]}
"""A shared component use; ``write_experiment`` writes its template."""


def write_experiment(root: Path, document: Mapping[str, Any]) -> Path:
    """Write the experiment plus the component files it references."""
    components = root / "components"
    components.mkdir(parents=True, exist_ok=True)
    rules = components / "AGENTS.md"
    if not rules.exists():
        rules.write_text("Run the tests before finishing.\n", encoding="utf-8")
    framing = components / "framing.md"
    if document.get("shared_components") and not framing.exists():
        framing.write_text("Work in /app.\n\n{{ instruction }}\n", encoding="utf-8")
    path = root / "experiment.yaml"
    path.write_text(yaml.safe_dump(dict(document), sort_keys=False), encoding="utf-8")
    return path


DEFAULT_BINDINGS: dict[RefKind, dict[str, object]] = {
    RefKind.PROBLEM: {"runtime": "python", "pack_version": 1, "validated": True},
    RefKind.COMPONENT: {"runtimes": ["*"], "supports": ["codex-cli", "claude-code"]},
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


def fixture_resolvers(*, checkpoints: int = 8) -> list[InMemoryResolver]:
    return [
        InMemoryResolver(RefKind.PROBLEM, {"scb/forge@abc123": {"checkpoints": checkpoints}}),
        InMemoryResolver(
            RefKind.COMPONENT,
            {"rules-file@1": {"kind": "workspace"}, "prompt-wrapper@1": {"kind": "prompt"}},
        ),
        InMemoryResolver(
            RefKind.SCORER, {"correctness@1": {"exact": True}}, tool_versions={"pytest": "8.4.1"}
        ),
    ]
