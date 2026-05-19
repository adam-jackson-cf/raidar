"""Runtime maintenance and environment preflight services."""

from __future__ import annotations


def cleanup_stale_harbor_resources(
    *, include_containers: bool = True, include_build_processes: bool = True
) -> None:
    """Cleanup stale Harbor containers and orphan build processes."""

    from raidar.runtime.harbor_cleanup import cleanup_stale_harbor_resources

    cleanup_stale_harbor_resources(
        include_containers=include_containers,
        include_build_processes=include_build_processes,
    )


def docker_compose_preflight_reason(env: dict[str, str]) -> str | None:
    """Return a Docker Compose preflight failure reason, when one exists."""

    from raidar.runtime.harbor_preflight import _docker_compose_preflight_reason

    return _docker_compose_preflight_reason(env)
