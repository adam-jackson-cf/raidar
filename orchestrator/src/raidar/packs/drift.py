"""``RT6`` pack drift: a lock's pack image against the image its pack builds now."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from raidar.packs import PackSpec, UnknownPackError, load_runtime_pack


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


class LockedPackError(LookupError):
    """``RT6`` at plan/run: the lock's runtime has no pack, or its pack now builds another image."""


def lock_runtime_pack(lock: Mapping[str, Any]) -> PackSpec:
    """``RT6`` at plan/run: the pack of the lock's runtime, refused when missing or drifted.

    The pack is loaded from the runtime the lock recorded (never assumed to be
    Python), and every locked problem's pack image must be the image that pack
    builds now; otherwise the trials would run in another image than the one
    the lock (and so the job's identity) names.
    """
    from raidar.packs.python.image import image_tag

    try:
        pack = load_runtime_pack(str(lock["spec"]["runtime"]))
    except UnknownPackError as exc:
        raise LockedPackError(f"[RT6] {exc}") from exc
    drift = pack_drift_issues(problem_pack_images(lock), image_tag(pack))
    if drift:
        raise LockedPackError("\n".join(drift))
    return pack
