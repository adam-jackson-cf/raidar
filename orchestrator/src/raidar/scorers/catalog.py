"""Scorer registry and the ``scorer`` registry resolver (plan F1, A2).

A registration pairs a manifest with an optional implementation import path
(``module:attribute``, imported lazily). Its implementation digest covers the
module and every module it imports, transitively, from ``raidar`` or its own
top-level package (shared kernels, the SDK, the oracle records), read
statically without importing, so a change to any helper changes the
Assessment key. The lock resolves ``name@version``
scorer references here through the ``raidar.spec.resolvers`` entry point
``scorer`` and records the manifest, the implementation's source digest and
the pinned versions of the pack tools the scorer declares.

A scorer whose implementation has not landed still resolves (its manifest is
the contract), but it cannot assess: loading it raises
``ScorerUnavailableError`` rather than producing any value.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.packs.python.models import ToolLock, load_tool_lock
from raidar.scorers.builtin_manifests import BUILTIN_MANIFESTS
from raidar.scorers.sdk.context import Scorer
from raidar.scorers.sdk.manifest import ANY_RUNTIME, ScorerManifest
from raidar.spec.canonical import digest_json, sha256_file
from raidar.spec.references import (
    RefKind,
    RegistryRef,
    RegistryReferenceError,
    ResolvedReference,
)

LockedTools = dict[str, dict[str, dict[str, Any]]]
RUNTIME_TOOL_LOCKS: dict[str, Callable[[], ToolLock]] = {"python": load_tool_lock}


class ScorerUnavailableError(RuntimeError):
    """The scorer is registered but cannot run (no implementation, or it failed to load)."""


_DIGEST_ROOTS = frozenset({"raidar"})


def _descend(directory: Path, part: str) -> tuple[Path | None, Path | None] | None:
    """(source, next package directory) for one dotted part, or None if absent."""
    package = directory / part
    if (package / "__init__.py").is_file():
        return package / "__init__.py", package
    if (directory / f"{part}.py").is_file():
        return directory / f"{part}.py", None
    if package.is_dir():
        return None, package
    return None


def _module_files(name: str) -> tuple[tuple[str, Path], ...] | None:
    """(module, source) pairs an import of ``name`` executes: parent packages, then itself.

    Namespace packages have no source and contribute nothing. None when ``name``
    is not a module (for example an attribute named in ``from x import y``).
    """
    parts = name.split(".")
    spec = importlib.util.find_spec(parts[0])
    if spec is None:
        return None
    locations = list(spec.submodule_search_locations or ())
    files = [(parts[0], Path(spec.origin))] if spec.origin and spec.has_location else []
    directory = Path(locations[0]) if locations else None
    for depth, part in enumerate(parts[1:], start=2):
        found = _descend(directory, part) if directory is not None else None
        if found is None:
            return None
        source, directory = found
        if source is not None:
            files.append((".".join(parts[:depth]), source))
    return tuple(files)


def _imported(name: str, path: Path) -> Iterator[str]:
    """Absolute names of modules (or their attributes) imported by ``path``."""
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    for node in ast.walk(ast.parse(path.read_bytes(), filename=str(path))):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*anchor, base] if base else anchor)
            yield base
            yield from (f"{base}.{alias.name}" for alias in node.names)


def _package_modules(module: str, files: tuple[tuple[str, Path], ...]) -> list[str]:
    """Every module of a package implementation (it may load some by name)."""
    if not files or files[-1][0] != module or files[-1][1].name != "__init__.py":
        return []
    root = files[-1][1].parent
    names = []
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            names.append(".".join([module, *parts]))
    return names


def _source_digest(module: str) -> str:
    """sha256 over the module's transitive in-scope source, without importing it."""
    importlib.invalidate_caches()
    roots = _DIGEST_ROOTS | {module.split(".")[0]}
    files = _module_files(module)
    if files is None or not files or files[-1][0] != module:
        raise ScorerUnavailableError(f"implementation module {module!r} not found")
    digests: dict[str, str] = {}
    pending = [module, *_package_modules(module, files)]
    while pending:
        name = pending.pop()
        if name in digests or name.split(".")[0] not in roots:
            continue
        for owner, path in _module_files(name) or ():
            if owner not in digests:
                digests[owner] = sha256_file(path)
                pending.extend(_imported(owner, path))
    return digest_json(dict(sorted(digests.items())))


@dataclass(frozen=True)
class ScorerRegistration:
    manifest: ScorerManifest
    implementation: str | None = None

    def __post_init__(self) -> None:
        if self.implementation is not None and self.implementation.count(":") != 1:
            raise ValueError(
                f"implementation must be 'module:attribute', got {self.implementation!r}"
            )

    def implementation_digest(self) -> str | None:
        if self.implementation is None:
            return None
        return _source_digest(self.implementation.split(":", 1)[0])

    def load(self) -> Scorer:
        if self.implementation is None:
            raise ScorerUnavailableError(
                f"scorer {self.manifest.ref} has a manifest but no implementation yet"
            )
        module_name, attribute = self.implementation.split(":", 1)
        factory = getattr(importlib.import_module(module_name), attribute)
        scorer = factory()
        if not isinstance(scorer, Scorer) or scorer.manifest != self.manifest:
            raise ScorerUnavailableError(
                f"{self.implementation} does not implement the registered {self.manifest.ref}"
            )
        return scorer


def tool_lock_for(manifest: ScorerManifest) -> LockedTools:
    """The pinned pack-tool entries this scorer declares, per runtime (feeds the key)."""
    if not manifest.tools:
        return {}
    locked: LockedTools = {}
    for runtime in manifest.runtimes:
        if runtime == ANY_RUNTIME or runtime not in RUNTIME_TOOL_LOCKS:
            raise RegistryReferenceError(
                f"scorer {manifest.ref} pins tools but runtime {runtime!r} has no tool lock"
            )
        lock = RUNTIME_TOOL_LOCKS[runtime]()
        try:
            locked[runtime] = {
                name: lock.tool(name).model_dump(mode="json") for name in manifest.tools
            }
        except KeyError as exc:
            raise RegistryReferenceError(f"scorer {manifest.ref}: {exc.args[0]}") from exc
    return locked


def _tool_versions(tool_lock: LockedTools) -> dict[str, str]:
    versions: dict[str, str] = {}
    for tools in tool_lock.values():
        for spec in tools.values():
            for package in spec["packages"]:
                name, version = package.split("==", 1)
                versions[name] = version
    return versions


class ScorerRegistry:
    """Registrations addressed by ``id@version``."""

    def __init__(self, registrations: Iterable[ScorerRegistration]) -> None:
        self._registrations: dict[str, ScorerRegistration] = {}
        for registration in registrations:
            ref = registration.manifest.ref
            if ref in self._registrations:
                raise ValueError(f"duplicate scorer registration {ref}")
            self._registrations[ref] = registration

    def refs(self) -> tuple[str, ...]:
        return tuple(sorted(self._registrations))

    def get(self, ref: str) -> ScorerRegistration:
        try:
            return self._registrations[ref]
        except KeyError as exc:
            raise RegistryReferenceError(
                f"unknown scorer {ref!r}; known: {', '.join(self.refs())}"
            ) from exc


def default_scorer_registry() -> ScorerRegistry:
    """Built-in scorers. Implementations are bound here as WS-G lands them."""
    return ScorerRegistry(ScorerRegistration(manifest) for manifest in BUILTIN_MANIFESTS)


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
            tool_versions=_tool_versions(tool_lock),
        )


def scorer_resolver() -> ScorerResolver:
    """Entry-point factory (group ``raidar.spec.resolvers``, name ``scorer``)."""
    return ScorerResolver(default_scorer_registry())
