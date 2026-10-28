"""Provision a snapshot's declared packages for grading (policy ``declared-wheels/1``).

1. Digest the snapshot's declared set (``set_sha256``).
2. Reuse the pin's lock when the set is one the pin resolved (``source="pin"``);
   otherwise resolve it on the host under the pin's cutoff and caps
   (``source="resolved"``).
3. Build (or reuse) the closure image layer for that lock.

A regrade passes the step's recorded evidence: its lock is reinstalled as
recorded and nothing is resolved. A resolver or index outage raises
``DependencyInfraError`` (an infrastructure failure, never ``unprovided``).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from raidar.contract.wheels import ARCHES, DependencyInfraError
from raidar.grading.bundle import BundleError, VerifierBundle
from raidar.grading.dependency_pins import lock_sha256, set_sha256
from raidar.grading.resolver import (
    Resolution,
    Resolver,
    UvResolver,
    provided_by_lock,
    pylock_packages,
)
from raidar.grading.sandbox import GradingSandbox
from raidar.grading.submission_deps import SubmissionDependencies, declared_requirements


async def _with_image(
    sandbox: GradingSandbox, dependencies: SubmissionDependencies
) -> SubmissionDependencies:
    """``dependencies`` plus the id of the image the graded program runs in."""
    if not dependencies.lock_text:
        return dependencies.model_copy(update={"image_id": await sandbox.environment_id()})
    image = await sandbox.closure_image(dependencies.lock_text)
    return dependencies.model_copy(
        update={"image_id": image.image_id, "build_s": image.seconds if image.built else 0.0}
    )


def _recorded(recorded: SubmissionDependencies) -> SubmissionDependencies:
    if recorded.lock_text and lock_sha256(recorded.lock_text) != recorded.lock_sha256:
        raise BundleError("the recorded dependency lock does not match its recorded sha256")
    return recorded


def _from_pin(
    bundle: VerifierBundle, key: str, requirements: tuple[str, ...], arch: str
) -> SubmissionDependencies | None:
    records = bundle.dependencies
    if records is None or key not in records.pins.sets:
        return None
    lock = records.locks.read(key, arch, records.pins.sets[key].lock(arch))
    return SubmissionDependencies(
        set_sha256=key,
        source="pin",
        lock_text=lock,
        lock_sha256=lock_sha256(lock),
        arch=arch,
        provided=provided_by_lock(requirements, lock),
    )


def _divergent(first: Resolution, second: Resolution) -> bool:
    """The two architectures resolved to different packages or refusals (never pooled)."""
    if first.provided != second.provided or set(first.unprovided) != set(second.unprovided):
        return True
    versions = [
        sorted(pylock_packages(r.lock_text)) if r.lock_text else [] for r in (first, second)
    ]
    return versions[0] != versions[1]


async def _resolved(
    bundle: VerifierBundle,
    key: str,
    requirements: tuple[str, ...],
    arch: str,
    resolver: Resolver | None,
) -> SubmissionDependencies:
    records = bundle.dependencies
    if records is None:
        raise BundleError(
            f"bundle {bundle.manifest.problem!r} has no dependency pin; re-import the problem"
        )
    chosen = resolver or UvResolver()
    identity = await asyncio.to_thread(lambda: chosen.identity)
    if identity != records.pins.resolver:
        raise DependencyInfraError(
            f"dependency resolver {identity} differs from pinned {records.pins.resolver}"
        )
    other = next(name for name in ARCHES if name != arch)
    cutoff = records.pins.exclude_newer
    resolution, counterpart = await asyncio.gather(
        asyncio.to_thread(chosen, requirements, arch=arch, exclude_newer=cutoff),
        asyncio.to_thread(chosen, requirements, arch=other, exclude_newer=cutoff),
    )
    return SubmissionDependencies(
        set_sha256=key,
        source="resolved",
        lock_text=resolution.lock_text,
        lock_sha256=lock_sha256(resolution.lock_text) if resolution.lock_text else "",
        arch=arch,
        provided=resolution.provided,
        unprovided=resolution.unprovided,
        resolve_s=resolution.resolve_s,
        arch_divergent=_divergent(resolution, counterpart),
    )


async def prepare_dependencies(
    bundle: VerifierBundle,
    snapshot: Path,
    sandbox: GradingSandbox,
    *,
    resolver: Resolver | None = None,
    recorded: SubmissionDependencies | None = None,
) -> SubmissionDependencies:
    """The declared set's lock and the image holding its closure (``image_id``)."""
    declared = declared_requirements(snapshot)
    arch = await sandbox.target_arch()
    requirements = tuple(sorted({text.strip() for text in declared if text.strip()}))
    key = set_sha256(requirements)
    if recorded is not None:
        return await _with_image(sandbox, _recorded(recorded))
    if not requirements:
        empty = SubmissionDependencies(declared=declared, set_sha256=key, source="pin", arch=arch)
        return await _with_image(sandbox, empty)
    started = time.perf_counter()
    found = _from_pin(bundle, key, requirements, arch) or await _resolved(
        bundle, key, requirements, arch, resolver
    )
    found = found.model_copy(
        update={"declared": declared, "resolve_s": found.resolve_s or time.perf_counter() - started}
    )
    return await _with_image(sandbox, found)
