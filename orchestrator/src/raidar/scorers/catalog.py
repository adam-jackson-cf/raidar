"""Scorer registry and the ``scorer`` registry resolver (plan F1, A2).

A registration pairs a manifest with an optional implementation import path
(``module:attribute``, imported lazily). Its implementation digest covers the
module and every module it imports, transitively, from ``raidar`` or its own
top-level package (shared kernels, the SDK, the oracle records), read
statically without importing, so a code change to any helper changes the
Assessment key. Each module is hashed as its normalised syntax tree
(``raidar.spec.source_digest``): docstring, comment and layout edits leave
the digest, and so every stored Assessment, unchanged. The lock resolves ``name@version``
scorer references here through the ``raidar.spec.resolvers`` entry point
``scorer`` and records the manifest, the implementation's source digest, the
pinned versions of the pack tools the scorer declares and, for a scorer that
decides under Jev policies, those policies' effective mode and digest.

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

from raidar.jev.policies import POLICY_DIR, PolicyRegistry
from raidar.packs.python.models import ToolLock, load_tool_lock
from raidar.scorers.builtin_manifests import BUILTIN_MANIFESTS
from raidar.scorers.sdk.context import Scorer
from raidar.scorers.sdk.manifest import ANY_RUNTIME, ScorerManifest
from raidar.scorers.versioned_manifests import VERSIONED_MANIFESTS
from raidar.spec.canonical import digest_json
from raidar.spec.references import (
    RefKind,
    RegistryRef,
    RegistryReferenceError,
    ResolvedReference,
)
from raidar.spec.source_digest import SOURCE_DIGEST_SCHEME, python_source_digest

LockedTools = dict[str, dict[str, dict[str, Any]]]
RUNTIME_TOOL_LOCKS: dict[str, Callable[[], ToolLock]] = {"python": load_tool_lock}
# The repo-versioned Jev policies a store-bound runtime decides under
# (``raidar.jev.session.open_runtime`` reads the same directory).
JEV_POLICY_DIR = POLICY_DIR


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
    """sha256 over the module's transitive in-scope code, without importing it."""
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
                digests[owner] = python_source_digest(path)
                pending.extend(_imported(owner, path))
    return digest_json({"scheme": SOURCE_DIGEST_SCHEME, "modules": dict(sorted(digests.items()))})


StoreFactory = Callable[..., Scorer]
"""``factory(store_root, *, replay_only, trial) -> Scorer`` (a ``module:attribute`` target)."""


def _split_target(target: str, field: str) -> tuple[str, str]:
    if target.count(":") != 1:
        raise ValueError(f"{field} must be 'module:attribute', got {target!r}")
    module, attribute = target.split(":", 1)
    return module, attribute


@dataclass(frozen=True)
class ScorerRegistration:
    """A manifest, its implementation, and what it needs from the assessment run.

    - ``store_implementation``: a factory that binds the scorer to an assessment
      store (for example a Jev runtime whose audit and cache live in it).
      ``raidar assess --rescore`` loads through it; without one it uses
      ``implementation``.
    - ``jev_policies``: the Jev policy ids the store-bound scorer decides under.
      Their effective definitions (mode included) join the scorer identity, so a
      mode change never reuses an Assessment made under another mode.
    - ``problem_config``: config keys whose default comes from the problem
      (``AssessContext.problem_config``); explicit scorer config wins.
    - ``protocol_config``: config keys the experiment protocol sets (for
      example ``progression``), written into every run's assessment config.
    - ``errata_config``: the scorer takes the problem errata the lock pins
      (config key ``errata``: ``{problem ref: ErrataRef}``); set only from a
      lock, never by the experiment.
    """

    manifest: ScorerManifest
    implementation: str | None = None
    store_implementation: str | None = None
    jev_policies: tuple[str, ...] = ()
    problem_config: tuple[str, ...] = ()
    protocol_config: tuple[str, ...] = ()
    errata_config: bool = False

    def __post_init__(self) -> None:
        if self.implementation is not None:
            _split_target(self.implementation, "implementation")
        if self.store_implementation is not None:
            if self.implementation is None:
                raise ValueError("store_implementation needs an implementation")
            _split_target(self.store_implementation, "store_implementation")

    def implementation_digest(self) -> str | None:
        if self.implementation is None:
            return None
        modules = {_split_target(self.implementation, "implementation")[0]}
        if self.store_implementation is not None:
            modules.add(_split_target(self.store_implementation, "store_implementation")[0])
        if len(modules) == 1:
            return _source_digest(next(iter(modules)))
        return digest_json({module: _source_digest(module) for module in sorted(modules)})

    def _checked(self, scorer: object, target: str) -> Scorer:
        if not isinstance(scorer, Scorer) or scorer.manifest != self.manifest:
            raise ScorerUnavailableError(
                f"{target} does not implement the registered {self.manifest.ref}"
            )
        return scorer

    def load(self) -> Scorer:
        if self.implementation is None:
            raise ScorerUnavailableError(
                f"scorer {self.manifest.ref} has a manifest but no implementation yet"
            )
        module_name, attribute = _split_target(self.implementation, "implementation")
        factory = getattr(importlib.import_module(module_name), attribute)
        return self._checked(factory(), self.implementation)

    def load_for_store(
        self, store_root: Path, *, replay_only: bool = False, trial: str | None = None
    ) -> Scorer:
        """The scorer bound to an assessment store; ``load()`` when it needs no store."""
        if self.store_implementation is None:
            return self.load()
        module_name, attribute = _split_target(self.store_implementation, "store_implementation")
        factory: StoreFactory = getattr(importlib.import_module(module_name), attribute)
        scorer = factory(store_root, replay_only=replay_only, trial=trial)
        return self._checked(scorer, self.store_implementation)

    def jev_policy_bindings(self) -> dict[str, dict[str, str]] | None:
        """``{policy id: {mode, digest}}`` of the repo-versioned policies; None without Jev."""
        if not self.jev_policies:
            return None
        registry = PolicyRegistry.from_dir(JEV_POLICY_DIR)
        return {
            policy_id: {
                "mode": registry.get(policy_id).mode.value,
                "digest": registry.get(policy_id).digest(),
            }
            for policy_id in sorted(self.jev_policies)
        }


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


# ``process-compliance@2`` and ``gate-effort@1``/``@2`` keep their manifests beside their
# implementations so the released scorers' digests (which cover
# ``builtin_manifests``) stay unchanged.
REGISTERED_MANIFESTS = (*BUILTIN_MANIFESTS, *VERSIONED_MANIFESTS)

BUILTIN_IMPLEMENTATIONS: dict[str, str] = {
    "correctness@1": "raidar.scorers.correctness:correctness_scorer",
    "code-quality@1": "raidar.scorers.code_quality:code_quality_scorer",
    "resource-usage@1": "raidar.scorers.resource_usage:resource_usage_scorer",
    "process-compliance@1": "raidar.scorers.process_compliance:process_compliance_scorer",
    "process-compliance@2": (
        "raidar.scorers.process_checks:deterministic_process_compliance_scorer"
    ),
    "gate-effort@1": "raidar.scorers.gate_effort:gate_effort_scorer",
    "gate-effort@2": "raidar.scorers.gate_effort_v2:gate_effort_v2_scorer",
}
# Scorers bound to the assessment store at rescore time, with the Jev policies
# that store-bound runtime decides under. Only the dormant process-compliance@1
# (Jev parked 2026-10-04; its residue policy is off); @2 needs no store binding.
BUILTIN_STORE_IMPLEMENTATIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "process-compliance@1": (
        "raidar.scorers.process_compliance:process_compliance_scorer_for_store",
        ("process-compliance.residue",),
    ),
}
# Config keys a scorer takes from the problem unless the experiment sets them.
BUILTIN_PROBLEM_CONFIG: dict[str, tuple[str, ...]] = {
    "code-quality@1": ("entry_file",),
}
# Config keys the experiment's protocol sets (``ProtocolSpec`` field names).
BUILTIN_PROTOCOL_CONFIG: dict[str, tuple[str, ...]] = {
    "correctness@1": ("progression",),
}
# Scorers that take the lock's pinned problem errata as config key ``errata``.
ERRATA_CONFIG_KEY = "errata"
BUILTIN_ERRATA_CONFIG: frozenset[str] = frozenset({"correctness@1"})


def _builtin(manifest: ScorerManifest) -> ScorerRegistration:
    store_implementation, jev_policies = BUILTIN_STORE_IMPLEMENTATIONS.get(manifest.ref, (None, ()))
    return ScorerRegistration(
        manifest,
        BUILTIN_IMPLEMENTATIONS.get(manifest.ref),
        store_implementation=store_implementation,
        jev_policies=jev_policies,
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
        jev_policies = registration.jev_policy_bindings()
        if jev_policies is not None:
            metadata["jev_policies"] = jev_policies
        return ResolvedReference(
            ref=ref,
            digest=digest_json(metadata),
            metadata=metadata,
            tool_versions=_tool_versions(tool_lock),
            binding={"runtimes": list(registration.manifest.runtimes)},
        )


def scorer_resolver() -> ScorerResolver:
    """Entry-point factory (group ``raidar.spec.resolvers``, name ``scorer``)."""
    return ScorerResolver(default_scorer_registry())
