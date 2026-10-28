"""Closure image layer: the hash-checked wheels of one lock, installed offline.

The layer is ``FROM <grader image>`` plus one ``RUN`` that installs the lock's
wheels with ``--require-hashes --no-deps --only-binary :all:`` from a local wheel
directory, with the build network disabled (``RUN --network=none``). The wheels
come from the host wheel cache (``raidar.contract.wheel_cache``), so no package
code runs and no sdist is built. The build context holds only ``requirements.txt``
and ``wheels/``; hidden tests never enter it. The tag is the digest of the base
tag and the lock, so a coherent closure is built once per (base, lock) and reused.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from raidar.contract.wheel_cache import fetch_wheels
from raidar.contract.wheels import (
    LOCKED_FLAGS,
    OFFLINE_FLAGS,
    hashed_requirements,
    pylock_wheels,
)
from raidar.grading.submission_deps import closure_dir

CLOSURE_REPOSITORY = "raidar-oracle-closure"
BUILD_TIMEOUT_S = 900.0
CONTEXT_MOUNT = "/raidar-ctx"
REQUIREMENTS_FILE = "requirements.txt"
WHEELS_DIR = "wheels"
_INSTALL = (
    f"uv pip install {OFFLINE_FLAGS} --find-links {{mount}}/{{wheels}} "
    f"{LOCKED_FLAGS} --python {{python}} {{target}} "
    "-r {mount}/{requirements}"
)


@dataclass(frozen=True)
class ClosureImage:
    tag: str
    image_id: str
    built: bool
    seconds: float


async def run_docker(
    docker_bin: str,
    docker_context: str,
    *args: str,
    stdin: bytes | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        docker_bin,
        "--context",
        docker_context,
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, **env} if env else None,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(stdin), timeout=BUILD_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        raise
    return int(proc.returncode or 0), stdout.decode(errors="replace")


def requirements_text(lock_text: str) -> str:
    """The lock as a hashed ``requirements.txt`` (every listed wheel hash is allowed)."""
    return hashed_requirements(pylock_wheels(lock_text))


def build_context(lock_text: str, wheels_dir: Path, dest: Path) -> None:
    """The closure build context: ``requirements.txt`` and ``wheels/`` only; never tests."""
    dest.mkdir(parents=True, exist_ok=True)
    (dest / REQUIREMENTS_FILE).write_text(requirements_text(lock_text))
    target = dest / WHEELS_DIR
    target.mkdir(exist_ok=True)
    for wheel in sorted(wheels_dir.glob("*.whl")):
        shutil.copyfile(wheel, target / wheel.name)


def install_command(*, python: str, target: str | None) -> str:
    """The offline, hash-checked install of the context's wheels (``target`` None: the venv)."""
    return _INSTALL.format(
        mount=CONTEXT_MOUNT,
        wheels=WHEELS_DIR,
        python=python,
        target=f"--target {target}" if target else "",
        requirements=REQUIREMENTS_FILE,
    ).replace("  ", " ")


def run_install(command: str) -> str:
    """One ``RUN`` with no network and the context bound read-only (never stored in a layer)."""
    return f"RUN --network=none --mount=type=bind,target={CONTEXT_MOUNT} {command}"


def closure_tag(base_tag: str, lock_sha: str) -> str:
    digest = hashlib.sha256(f"{base_tag}\n{lock_sha}".encode()).hexdigest()[:16]
    return f"{CLOSURE_REPOSITORY}:{digest}"


def closure_dockerfile(base_tag: str, lock_sha: str, python: str) -> str:
    target = closure_dir(lock_sha)
    install = install_command(python=python, target=target)
    return f"FROM {base_tag}\nUSER root\n{run_install(install + f' && chmod -R a+rX {target}')}\n"


async def image_id(docker_bin: str, docker_context: str, tag: str) -> str | None:
    code, output = await run_docker(
        docker_bin, docker_context, "image", "inspect", "--format", "{{.Id}}", tag
    )
    text = output.strip()
    return text if code == 0 and text else None


async def build_image(
    docker_bin: str, docker_context: str, tag: str, dockerfile: str, context: Path
) -> None:
    code, output = await run_docker(
        docker_bin,
        docker_context,
        "build",
        "-t",
        tag,
        "-f",
        "-",
        str(context),
        stdin=dockerfile.encode(),
        env={"DOCKER_BUILDKIT": "1"},
    )
    if code != 0:
        raise RuntimeError(f"image build failed for {tag}:\n{output[-4000:]}")


async def ensure_closure_image(
    base_tag: str,
    lock_text: str,
    lock_sha: str,
    *,
    docker_context: str,
    python: str,
    docker_bin: str = "docker",
    cache_root: Path | None = None,
) -> ClosureImage:
    """Presence-check the closure image; build it (host fetch, offline build) if absent."""
    tag = closure_tag(base_tag, lock_sha)
    started = time.perf_counter()
    existing = await image_id(docker_bin, docker_context, tag)
    if existing is not None:
        return ClosureImage(tag, existing, False, time.perf_counter() - started)
    with tempfile.TemporaryDirectory(prefix="raidar-closure-") as tmp:
        wheels, context = Path(tmp) / "fetched", Path(tmp) / "context"
        await asyncio.to_thread(fetch_wheels, lock_text, wheels, cache_root=cache_root)
        await asyncio.to_thread(build_context, lock_text, wheels, context)
        await build_image(
            docker_bin, docker_context, tag, closure_dockerfile(base_tag, lock_sha, python), context
        )
    built = await image_id(docker_bin, docker_context, tag)
    if built is None:
        raise RuntimeError(f"closure image {tag} is missing after its build")
    return ClosureImage(tag, built, True, time.perf_counter() - started)


_ARCH_NAMES = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


async def docker_arch(docker_bin: str, docker_context: str) -> str:
    """The daemon's architecture as a pin arch (``amd64`` or ``arm64``)."""
    code, output = await run_docker(
        docker_bin, docker_context, "info", "--format", "{{.Architecture}}"
    )
    arch = _ARCH_NAMES.get(output.strip())
    if code != 0 or arch is None:
        raise RuntimeError(f"unsupported or unreadable docker architecture: {output.strip()!r}")
    return arch
