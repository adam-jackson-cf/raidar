"""Import-time dependency pins for a problem: reuse, resolution, refusal and lock records."""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from raidar.design.problem.models import ProblemId, ProblemManifest
from raidar.design.problem.store import ProblemStore
from raidar.grading.dependency_pins import (
    DependencyLocks,
    DependencyPins,
    DependencyRefusal,
    LockKey,
    set_sha256,
)
from raidar.grading.resolver import (
    GRADER_TOOLS,
    DependencyUnresolvable,
    Resolver,
    UvResolver,
    resolve_pins,
)
from raidar.grading.submission_deps import declared_requirements


@dataclass(frozen=True)
class ProblemDependencies:
    """The outcome of resolving a problem's dependency sets: a pin with its locks, or a refusal."""

    pins: DependencyPins | None = None
    locks: Mapping[LockKey, str] | None = None
    refusal: DependencyRefusal | None = None


def cutoff_now() -> str:
    """The import time as the dependency cutoff: RFC 3339, UTC, whole seconds."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _retained_cutoff(recorded: ProblemManifest | None) -> str | None:
    """A re-import keeps the recorded cutoff so the resolved versions stay reproducible."""
    if recorded is None:
        return None
    if recorded.dependencies is not None:
        return recorded.dependencies.exclude_newer
    if recorded.dependency_refusal is not None:
        return recorded.dependency_refusal.exclude_newer
    return None


def _unchanged_pins(
    recorded: ProblemManifest | None,
    reference_sets: Mapping[str, tuple[str, ...]],
    test_dependencies: tuple[str, ...],
) -> DependencyPins | None:
    """The recorded pin when the declared sets are the ones it was resolved for."""
    pins = None if recorded is None else recorded.dependencies
    if pins is None:
        return None
    steps = {step: set_sha256(reqs) for step, reqs in reference_sets.items() if reqs}
    seed = set_sha256(test_dependencies) if test_dependencies else None
    grader = set_sha256((*GRADER_TOOLS, *test_dependencies))
    same = pins.steps == steps and pins.seed == seed and pins.grader == grader
    return pins if same else None


def resolve_dependencies(
    store: ProblemStore,
    problem: ProblemId,
    recorded: ProblemManifest | None,
    references: Path,
    steps: list[str],
    test_dependencies: tuple[str, ...],
    resolver: Resolver | None,
) -> ProblemDependencies:
    """Resolve every reference step's declared set and the grader's sets, both architectures.

    Sets the recorded pin was resolved for, by the same resolver, are reused from its committed
    locks; anything else (including a pin from another uv version) resolves under the retained
    cutoff (or the import time). A set that does not resolve, or
    resolves to different versions per architecture, is a refusal (``dependency_unresolvable``).
    A resolver or index outage raises (infrastructure), it never becomes a refusal.
    """
    reference_sets = {step: declared_requirements(references / step) for step in steps}
    chosen = resolver or UvResolver()
    reused = _unchanged_pins(recorded, reference_sets, test_dependencies)
    if reused is not None and reused.resolver == chosen.identity:
        locks = DependencyLocks(store.deps_dir(problem)).read_pins(reused)
        return ProblemDependencies(pins=reused, locks=locks)
    cutoff = _retained_cutoff(recorded) or cutoff_now()
    try:
        pins, locks = resolve_pins(
            reference_sets, test_dependencies, exclude_newer=cutoff, resolver=chosen
        )
    except DependencyUnresolvable as exc:
        refusal = DependencyRefusal(
            detail=str(exc), exclude_newer=cutoff, unprovided=exc.unprovided
        )
        return ProblemDependencies(refusal=refusal)
    return ProblemDependencies(pins=pins, locks=locks)


def write_locks(store: ProblemStore, problem: ProblemId, dependencies: ProblemDependencies) -> None:
    """Replace the committed lock records beside the manifest (none for a refused problem)."""
    target = store.deps_dir(problem)
    shutil.rmtree(target, ignore_errors=True)
    if dependencies.locks is not None:
        DependencyLocks(target).write_all(dependencies.locks)
