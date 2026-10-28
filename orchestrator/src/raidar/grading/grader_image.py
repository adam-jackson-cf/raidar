"""Grader image: the agent environment's image plus Raidar's pinned verifier tools.

The image is the bundle's ``environment_dockerfile`` (the agent's own
Dockerfile) with one extra layer, so the submission sees the same system
packages and ``ENV`` it had in the agent environment.

Verifier tools (pytest, pytest-timeout, coverage and the problem's test
dependencies) live in a separate venv inside this image only. They never enter
the agent image (the Python pack asserts that side). The submission keeps
running on the base interpreter, as it did in the agent environment.

The venv is installed from the problem pin's ``grader`` lock (``declared-wheels/1``:
tools plus test dependencies and every transitive package, hash-checked, wheels
only) from the host wheel cache, with the build network off, so the same pin
always yields the same venv. The submission's own declared packages are a
separate closure layer (``raidar.grading.closure``).

The tag is content-addressed over the Dockerfile text and the pinned lock digests,
so the warm path is a presence check (N1 budget: grader image warm 1 s; cold build
is record-only). The image ID recorded per step is the reproducibility proof;
``raidar assess --regrade`` compares it.
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from raidar.grading.bundle import BundleError, VerifierBundle
from raidar.grading.closure import (
    build_context,
    build_image,
    docker_arch,
    image_id,
    install_command,
    run_install,
)
from raidar.grading.sandbox import CONTAINER_LAYOUT, SUBMISSION_PYTHON
from raidar.grading.wheel_cache import fetch_wheels

GRADER_VENV = CONTAINER_LAYOUT.python.removesuffix("/bin/python")
GRADER_REPOSITORY = "raidar-oracle-grader"
__all__ = [
    "GraderImage",
    "ensure_grader_image",
    "grader_dockerfile",
    "grader_image_tag",
]


def _grader_set(bundle: VerifierBundle) -> str:
    records = bundle.dependencies
    if records is None or records.pins.grader is None:
        raise BundleError(
            f"bundle {bundle.manifest.problem!r} has no dependency pin for its grader "
            "environment; re-import the problem"
        )
    return records.pins.grader


def grader_dockerfile(bundle: VerifierBundle) -> str:
    key = _grader_set(bundle)
    assert bundle.dependencies is not None
    locked = bundle.dependencies.pins.sets[key]
    spec = bundle.manifest.grader
    environment = spec.environment_dockerfile or f"FROM {spec.base_image}\n"
    install = install_command(python=f"{GRADER_VENV}/bin/python", target=None)
    return (
        f"{environment.rstrip()}\n"
        "USER root\n"
        f"# raidar-grader-lock {key} {locked.amd64} {locked.arm64}\n"
        + run_install(
            f"uv venv --python {SUBMISSION_PYTHON} {GRADER_VENV} && {install}"
            f" && chmod -R a+rX {GRADER_VENV}"
        )
        + "\n"
    )


def grader_image_tag(bundle: VerifierBundle) -> str:
    digest = hashlib.sha256(grader_dockerfile(bundle).encode()).hexdigest()[:16]
    return f"{GRADER_REPOSITORY}:{digest}"


@dataclass(frozen=True)
class GraderImage:
    tag: str
    built: bool
    seconds: float


async def ensure_grader_image(
    bundle: VerifierBundle,
    *,
    docker_context: str,
    docker_bin: str = "docker",
    cache_root: Path | None = None,
) -> GraderImage:
    """Presence-check the grader image; build it (host wheel fetch, offline build) if absent."""
    tag = grader_image_tag(bundle)
    started = time.perf_counter()
    if await image_id(docker_bin, docker_context, tag) is not None:
        return GraderImage(tag=tag, built=False, seconds=time.perf_counter() - started)
    records = bundle.dependencies
    assert records is not None  # grader_image_tag raised otherwise
    key = _grader_set(bundle)
    arch = await docker_arch(docker_bin, docker_context)
    lock_text = records.locks.read(key, arch, records.pins.sets[key].lock(arch))
    with tempfile.TemporaryDirectory(prefix="raidar-grader-") as tmp:
        wheels, context = Path(tmp) / "fetched", Path(tmp) / "context"
        await asyncio.to_thread(fetch_wheels, lock_text, wheels, cache_root=cache_root)
        await asyncio.to_thread(build_context, lock_text, wheels, context)
        await build_image(docker_bin, docker_context, tag, grader_dockerfile(bundle), context)
    return GraderImage(tag=tag, built=True, seconds=time.perf_counter() - started)
