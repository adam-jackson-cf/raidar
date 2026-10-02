"""Component registry and the lock's ``component`` resolver (plan WS-C C1).

Definitions are registered under the ``raidar.components`` entry-point group,
named by their full reference (``rules-file@1``). Entry points are imported
lazily, only when that reference is looked up. An unknown reference raises; nothing is skipped.

The resolver is exposed to ``raidar lock`` through the ``raidar.spec.resolvers``
entry point named ``component``.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path

from raidar.agents.config import agent_name_for
from raidar.components.manifest import ComponentDefinition
from raidar.spec.canonical import digest_json, sha256_file
from raidar.spec.references import (
    RefKind,
    RegistryRef,
    RegistryReferenceError,
    ResolvedReference,
)

COMPONENT_ENTRY_POINT_GROUP = "raidar.components"


class UnknownComponentError(RegistryReferenceError):
    """No definition is registered for a component reference."""


class ComponentRegistry:
    """Definitions keyed by ``name@version``; explicit ones win over entry points."""

    def __init__(
        self,
        definitions: Iterable[ComponentDefinition] = (),
        *,
        entry_point_group: str | None = COMPONENT_ENTRY_POINT_GROUP,
    ) -> None:
        self._loaded: dict[str, ComponentDefinition] = {}
        for definition in definitions:
            ref = definition.manifest.ref
            if ref in self._loaded:
                raise RegistryReferenceError(f"duplicate component definition {ref!r}")
            self._loaded[ref] = definition
        self._entry_points: dict[str, EntryPoint] = {}
        if entry_point_group is not None:
            for entry in entry_points(group=entry_point_group):
                if entry.name in self._entry_points:
                    raise RegistryReferenceError(
                        f"component {entry.name!r} is registered by more than one entry point"
                    )
                self._entry_points[entry.name] = entry

    def refs(self) -> list[str]:
        return sorted(set(self._loaded) | set(self._entry_points))

    def get(self, ref: str) -> ComponentDefinition:
        loaded = self._loaded.get(ref)
        if loaded is not None:
            return loaded
        entry = self._entry_points.get(ref)
        if entry is None:
            known = ", ".join(self.refs()) or "none"
            raise UnknownComponentError(f"unknown component {ref!r}; registered: {known}")
        definition = entry.load()
        if not isinstance(definition, ComponentDefinition):
            raise RegistryReferenceError(
                f"entry point {entry.value!r} for {ref!r} is not a component definition"
            )
        if definition.manifest.ref != ref:
            raise RegistryReferenceError(
                f"entry point {ref!r} loads a definition for {definition.manifest.ref!r}"
            )
        self._loaded[ref] = definition
        return definition


def _source_root(definition: ComponentDefinition) -> Path:
    """Directory that holds the definition's top-level package (fingerprint base)."""
    top = type(definition).__module__.split(".", 1)[0]
    return Path(sys.modules[top].__file__ or ".").resolve().parent.parent


ANY_RUNTIME = "*"
BUILTIN_COMPONENT_RUNTIMES: dict[str, tuple[str, ...]] = {
    # lint-config@1 delivers ruff/mypy-style Python config files.
    "lint-config@1": ("python",),
}
"""Runtimes of the released built-ins, kept beside (not in) their frozen manifests.

A released component's manifest and fingerprint sources are frozen (editing
them would change its digest), so its runtimes live here; a built-in not
listed works on any runtime. A third-party definition may set ``runtimes``.
"""


def component_runtimes(definition: ComponentDefinition) -> tuple[str, ...]:
    """The runtimes a component works on (``("*",)`` for any)."""
    declared = getattr(definition, "runtimes", None)
    if declared is not None:
        return tuple(declared)
    return BUILTIN_COMPONENT_RUNTIMES.get(definition.manifest.ref, (ANY_RUNTIME,))


def fingerprint(definition: ComponentDefinition) -> str:
    """sha256 over the manifest and the bytes of every fingerprint source file."""
    root = _source_root(definition)
    sources = {}
    for relative in definition.manifest.fingerprint:
        path = root / relative
        if not path.is_file():
            raise RegistryReferenceError(
                f"{definition.manifest.ref}: fingerprint file {relative!r} does not exist"
            )
        sources[relative] = sha256_file(path)
    return digest_json({"manifest": definition.manifest.model_dump(mode="json"), "files": sources})


class ComponentResolver:
    """``RegistryResolver`` for ``component`` references."""

    def __init__(self, registry: ComponentRegistry | None = None) -> None:
        self._registry = registry or ComponentRegistry()

    @property
    def kind(self) -> RefKind:
        return RefKind.COMPONENT

    def resolve(self, ref: RegistryRef, *, base_dir: Path) -> ResolvedReference:
        del base_dir
        definition = self._registry.get(str(ref))
        manifest = definition.manifest
        return ResolvedReference(
            ref=ref,
            digest=fingerprint(definition),
            metadata={
                "kind": manifest.kind.value,
                "supports": [agent_name_for(cli).value for cli in manifest.supports],
                "network": {"egress_hosts": list(manifest.network.egress_hosts)},
                "secrets": list(manifest.secrets),
                "discovery_probe": manifest.discovery_probe.id,
            },
            binding={
                "runtimes": list(component_runtimes(definition)),
                "supports": [cli.value for cli in manifest.supports],
            },
        )


def component_resolver() -> ComponentResolver:
    """Entry-point factory for ``raidar.spec.resolvers`` (kind ``component``)."""
    return ComponentResolver()
