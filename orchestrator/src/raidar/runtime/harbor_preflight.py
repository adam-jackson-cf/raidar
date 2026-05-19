"""Docker Compose preflight checks for Harbor."""

from __future__ import annotations

import subprocess

from raidar.runtime.harbor import (
    format_version as _format_version,
)
from raidar.runtime.harbor import (
    parse_docker_compose_version as _parse_docker_compose_version,
)

MIN_DOCKER_COMPOSE_VERSION = (2, 40, 1)


def _read_docker_compose_version(run_env: dict[str, str]) -> tuple[int, int, int] | None:
    for cmd in (["docker", "compose", "version", "--short"], ["docker", "compose", "version"]):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
                env=run_env,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            continue
        version = _parse_docker_compose_version(result.stdout or "")
        if version:
            return version
    return None


def _docker_compose_preflight_reason(run_env: dict[str, str]) -> str | None:
    version = _read_docker_compose_version(run_env)
    if version is None:
        return None
    if version < MIN_DOCKER_COMPOSE_VERSION:
        required = _format_version(MIN_DOCKER_COMPOSE_VERSION)
        detected = _format_version(version)
        return (
            f"Unsupported docker compose version {detected}. Require >= {required} for Harbor runs."
        )
    return None
