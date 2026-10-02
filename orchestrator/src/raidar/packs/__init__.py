"""Runtime packs: per-runtime agent images, tool locks and lazy provisioners.

A *runtime* is a language toolchain and equals a pack id (``python``,
``node-ts``, ...; F8). Every experiment declares exactly one runtime, and
everything that runs it (``lock``, ``plan``, ``run``) loads that runtime's
pack through ``load_runtime_pack`` rather than assuming Python. Only the
Python pack exists today; other runtimes are refused with a field-level
``RT6`` error at lock time.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from raidar.packs.python.models import PackSpec

__all__ = [
    "PackDriftError",
    "PackSpec",
    "UnknownPackError",
    "available_runtimes",
    "load_runtime_pack",
    "lock_runtime_pack",
    "pack_drift_issues",
    "problem_pack_images",
]


class UnknownPackError(LookupError):
    """No runtime pack is registered for the requested runtime."""


def _python_pack() -> PackSpec:
    from raidar.packs.python.models import load_pack_spec

    return load_pack_spec()  # the Python pack's own loader (pack.yaml beside it)


PACK_LOADERS: dict[str, Callable[[], PackSpec]] = {"python": _python_pack}


def available_runtimes() -> tuple[str, ...]:
    return tuple(sorted(PACK_LOADERS))


def load_runtime_pack(runtime: str) -> PackSpec:
    """The pack implementing ``runtime``; ``UnknownPackError`` when none is registered."""
    loader = PACK_LOADERS.get(runtime)
    if loader is None:
        raise UnknownPackError(
            f"no runtime pack for {runtime!r} (packs: {', '.join(available_runtimes())})"
        )
    pack = loader()
    if pack.id != runtime:
        raise UnknownPackError(f"the pack registered for {runtime!r} declares id {pack.id!r}")
    return pack


def problem_pack_images(lock: Mapping[str, object]) -> dict[str, str]:
    """``{problem ref: locked pack image}`` from a lock's execution references."""
    execution = lock.get("execution")
    references = execution.get("references") if isinstance(execution, Mapping) else None
    problems = references.get("problem") if isinstance(references, Mapping) else None
    images: dict[str, str] = {}
    for ref, record in (problems or {}).items():
        metadata = record.get("metadata") if isinstance(record, Mapping) else None
        image = metadata.get("pack_image") if isinstance(metadata, Mapping) else None
        if isinstance(image, str):
            images[str(ref)] = image
    return images


def _repository(image: str) -> str:
    """The repository of an image reference (``raidar/pack-python`` of ``...:<tag>``)."""
    name = image.split("@", 1)[0]
    head, _, tail = name.rpartition(":")
    return head if head and "/" not in tail else name


def pack_drift_issues(images: Mapping[str, str], built_image: str) -> Sequence[str]:
    """``RT6`` drift: a locked build of this pack that is not the build the pack makes now.

    A locked image from the pack's own repository with another lock-hash tag is
    drift: the pack recipe (base image, agent pins, toolchain) changed after the
    lock, so the trials would run binaries the lock does not describe. An image
    from another repository is not a build of this pack (a fixture task that
    binds its own image) and is left to the problem's own content digest.
    """
    repository = _repository(built_image)
    return [
        f"[RT6] pack drift: {ref} was locked with pack image {image}, but the runtime pack "
        f"now builds {built_image}; relock (and re-import if its contract changed)"
        for ref, image in sorted(images.items())
        if _repository(image) == repository and image != built_image
    ]


class PackDriftError(LookupError):
    """The pack now builds another image than the one the lock recorded (``RT6``)."""


def lock_runtime_pack(lock: Mapping[str, Any]) -> PackSpec:
    """``RT6`` at plan/run: the pack of the lock's runtime, refused on pack drift.

    The pack is loaded from the runtime the lock recorded (never assumed to be
    Python), and every locked problem's pack image must be the image that pack
    builds now; otherwise the trials would run in another image than the one
    the lock (and so the job's identity) names.
    """
    from raidar.packs.python.image import image_tag

    pack = load_runtime_pack(str(lock["spec"]["runtime"]))
    drift = pack_drift_issues(problem_pack_images(lock), image_tag(pack))
    if drift:
        raise PackDriftError("\n".join(drift))
    return pack
