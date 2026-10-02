"""Grader image: the agent environment's image plus Raidar's pinned verifier tools.

The image is the bundle's ``environment_dockerfile`` (the agent's own
Dockerfile) with one extra layer, so the submission sees the same system
packages and ``ENV`` it had in the agent environment.

Verifier tools (pytest, pytest-timeout, coverage and the problem's test
dependencies) live in a separate venv inside this image only. They never enter
the agent image (the Python pack asserts that side). The submission keeps
running on the base interpreter, as it did in the agent environment.

Pinned copies of the third-party packages the problem's SCB configuration
names (``raidar.verifier.submission_deps``) are installed into per-package
directories outside both interpreters; the graded program sees one only when
its snapshot declares it.

The tag is content-addressed over the Dockerfile text, so the warm path is a
presence check (N1 budget: grader image warm 1 s; cold build is record-only).
The tag is not a reproducibility proof (a base tag or an unpinned test
dependency can resolve differently on a rebuild); the image ID recorded per
step is, and ``raidar assess --regrade`` compares it.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass

from raidar.verifier.bundle import GraderSpec
from raidar.verifier.sandbox import CONTAINER_LAYOUT, docker_cli_env
from raidar.verifier.submission_deps import dockerfile_layer, provisioned_pins

GRADER_TOOLS: tuple[str, ...] = (
    "pytest==9.0.3",
    "pytest-timeout==2.4.0",
    "coverage==7.13.4",
)
GRADER_VENV = CONTAINER_LAYOUT.python.removesuffix("/bin/python")
GRADER_REPOSITORY = "raidar-oracle-grader"
SUBMISSION_PYTHON = "/usr/local/bin/python3"
BUILD_TIMEOUT_S = 900.0


def grader_dockerfile(spec: GraderSpec) -> str:
    packages = " ".join(f'"{p}"' for p in (*GRADER_TOOLS, *spec.test_dependencies))
    environment = spec.environment_dockerfile or f"FROM {spec.base_image}\n"
    return (
        f"{environment.rstrip()}\n"
        "USER root\n"
        f"RUN uv venv --python {SUBMISSION_PYTHON} {GRADER_VENV} \\\n"
        f" && uv pip install --no-cache --python {GRADER_VENV}/bin/python {packages} \\\n"
        f" && chmod -R a+rX {GRADER_VENV}\n"
        f"{dockerfile_layer(provisioned_pins(spec.test_dependencies), SUBMISSION_PYTHON)}"
    )


def grader_image_tag(spec: GraderSpec) -> str:
    digest = hashlib.sha256(grader_dockerfile(spec).encode()).hexdigest()[:16]
    return f"{GRADER_REPOSITORY}:{digest}"


@dataclass(frozen=True)
class GraderImage:
    tag: str
    built: bool
    seconds: float


async def _docker(
    docker_bin: str, docker_context: str, *args: str, stdin: bytes | None = None
) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        docker_bin,
        "--context",
        docker_context,
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=docker_cli_env(),
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(stdin), timeout=BUILD_TIMEOUT_S)
    return int(proc.returncode or 0), stdout.decode(errors="replace")


async def ensure_grader_image(
    spec: GraderSpec, *, docker_context: str, docker_bin: str = "docker"
) -> GraderImage:
    """Presence-check the grader image; build it (network at build time only) if absent."""
    tag = grader_image_tag(spec)
    started = time.perf_counter()
    code, _ = await _docker(docker_bin, docker_context, "image", "inspect", tag)
    if code == 0:
        return GraderImage(tag=tag, built=False, seconds=time.perf_counter() - started)
    code, output = await _docker(
        docker_bin, docker_context, "build", "-t", tag, "-", stdin=grader_dockerfile(spec).encode()
    )
    if code != 0:
        raise RuntimeError(f"grader image build failed for {tag}:\n{output[-4000:]}")
    return GraderImage(tag=tag, built=True, seconds=time.perf_counter() - started)
