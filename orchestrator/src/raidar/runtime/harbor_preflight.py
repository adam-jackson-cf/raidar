"""Docker Compose preflight checks for Harbor."""

from __future__ import annotations

import subprocess

from raidar.runtime.harbor import (
    format_version as _format_version,
)
from raidar.runtime.harbor import (
    parse_docker_compose_version as _parse_docker_compose_version,
)
from raidar.runtime.profile import RuntimeProfile, default_runtime_profile


def _version_tuple(version: str) -> tuple[int, int, int]:
    parts = version.split(".")
    if len(parts) != 3:
        raise ValueError(f"Expected semantic version with three parts, got {version!r}")
    return (int(parts[0]), int(parts[1]), int(parts[2]))


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


def _docker_compose_preflight_reason(
    run_env: dict[str, str],
    runtime_profile: RuntimeProfile | None = None,
) -> str | None:
    profile = runtime_profile or default_runtime_profile()
    minimum_version = _version_tuple(profile.compose_minimum_version)
    version = _read_docker_compose_version(run_env)
    if version is None:
        return None
    if version < minimum_version:
        required = _format_version(minimum_version)
        detected = _format_version(version)
        return (
            f"Unsupported docker compose version {detected}. Require >= {required} for Harbor runs."
        )
    return None
