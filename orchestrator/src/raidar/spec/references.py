"""Registry references (``name@version``) and the resolver protocol.

An ``experiment.yaml`` names problems, components, scorers and score policies by
registry reference. The lock resolves each reference through a
``RegistryResolver`` registered for its kind. The spec package ships the
``metric`` resolver (the headline metric registry); other workstreams plug in their
registries either by passing a resolver to ``ResolverSet`` directly or by
exposing a zero-argument factory under the ``raidar.spec.resolvers`` entry-point
group. Entry points are loaded lazily, only when a reference of that kind must
be resolved.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from importlib.metadata import entry_points
from pathlib import Path
from typing import Protocol, runtime_checkable

RESOLVER_ENTRY_POINT_GROUP = "raidar.spec.resolvers"

_NAME_PATTERN = r"[a-z0-9][a-z0-9._/-]*"
_VERSION_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._+-]*"
REFERENCE_PATTERN = rf"^{_NAME_PATTERN}@{_VERSION_PATTERN}$"
_REFERENCE_RE = re.compile(REFERENCE_PATTERN)


class RefKind(StrEnum):
    """Registry kinds an experiment can reference."""

    PROBLEM = "problem"
    COMPONENT = "component"
    SCORER = "scorer"
    METRIC = "metric"


class RegistryReferenceError(ValueError):
    """A reference is malformed or cannot be resolved."""


@dataclass(frozen=True, order=True)
class RegistryRef:
    """A parsed ``name@version`` registry reference of one kind."""

    kind: RefKind
    name: str
    version: str

    @classmethod
    def parse(cls, kind: RefKind, text: str) -> RegistryRef:
        if not _REFERENCE_RE.fullmatch(text):
            raise RegistryReferenceError(
                f"invalid {kind.value} reference {text!r}: expected name@version "
                f"(name matches {_NAME_PATTERN}, version matches {_VERSION_PATTERN})"
            )
        name, version = text.split("@", 1)
        return cls(kind=kind, name=name, version=version)

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True)
class ResolvedReference:
    """What the lock records for one resolved reference.

    ``digest`` is a sha256 over the resolved content (files, definition or
    upstream digest). ``metadata`` is JSON-serialisable and must be
    deterministic. Problem resolvers must provide an integer ``checkpoints``
    entry. ``tool_versions`` feeds the lock's recorded tool versions.

    ``binding`` holds the facts the runtime-compatibility gate reads
    (``raidar.spec.compat``) and is never recorded in the lock, so adding the
    gate left every execution digest unchanged:

    - problem: ``runtime`` (the binding's pack id), ``pack_version``,
      ``validated`` and, when not validated, ``validation_issue``;
    - component: ``runtimes`` (``["*"]`` for any) and ``supports`` (agent CLI ids);
    - scorer and metric: ``runtimes``.
    """

    ref: RegistryRef
    digest: str
    metadata: Mapping[str, object] = field(default_factory=dict)
    tool_versions: Mapping[str, str] = field(default_factory=dict)
    binding: Mapping[str, object] = field(default_factory=dict)


@runtime_checkable
class RegistryResolver(Protocol):
    """Resolves references of a single kind to content-addressed records."""

    @property
    def kind(self) -> RefKind: ...

    def resolve(self, ref: RegistryRef, *, base_dir: Path) -> ResolvedReference:
        """Resolve ``ref``; raise ``RegistryReferenceError`` when it does not exist."""
        ...


ResolverFactory = Callable[[], RegistryResolver]


def _entry_point_factories(group: str) -> dict[RefKind, list[ResolverFactory]]:
    """Map each kind to the entry-point factories named after it (lazy import)."""
    factories: dict[RefKind, list[ResolverFactory]] = {}
    for entry in entry_points(group=group):
        try:
            kind = RefKind(entry.name)
        except ValueError as exc:
            raise RegistryReferenceError(
                f"entry point {entry.value!r} in group {group!r} is named {entry.name!r}; "
                f"its name must be a reference kind ({', '.join(k.value for k in RefKind)})"
            ) from exc

        def load(entry_point: object = entry) -> RegistryResolver:
            factory = entry_point.load()  # type: ignore[attr-defined]
            return factory()  # type: ignore[no-any-return]

        factories.setdefault(kind, []).append(load)
    return factories


class ResolverSet:
    """Resolvers keyed by kind; explicit resolvers win over entry points."""

    def __init__(
        self,
        resolvers: Iterable[RegistryResolver] = (),
        *,
        entry_point_group: str | None = RESOLVER_ENTRY_POINT_GROUP,
    ) -> None:
        self._resolvers: dict[RefKind, RegistryResolver] = {}
        for resolver in resolvers:
            if resolver.kind in self._resolvers:
                raise RegistryReferenceError(f"duplicate resolver for kind {resolver.kind.value!r}")
            self._resolvers[resolver.kind] = resolver
        self._factories = (
            _entry_point_factories(entry_point_group) if entry_point_group is not None else {}
        )

    def available_kinds(self) -> frozenset[RefKind]:
        return frozenset(self._resolvers) | frozenset(self._factories)

    def resolver_for(self, kind: RefKind) -> RegistryResolver | None:
        resolver = self._resolvers.get(kind)
        if resolver is not None:
            return resolver
        factories = self._factories.get(kind, [])
        if not factories:
            return None
        if len(factories) > 1:
            raise RegistryReferenceError(
                f"multiple entry-point resolvers registered for kind {kind.value!r}"
            )
        loaded = factories[0]()
        if loaded.kind is not kind:
            raise RegistryReferenceError(
                f"entry-point resolver for {kind.value!r} reports kind {loaded.kind.value!r}"
            )
        self._resolvers[kind] = loaded
        return loaded
