"""Harbor stale resource cleanup services."""

from __future__ import annotations

import os
import re
import signal
import subprocess

HARNESS_STALE_CONTAINER_PATTERN = re.compile(r"^harbor-task.*-main-1$")

HARBOR_GIT_MULTIBRANCH_PATTERN = re.compile(r"^git-multibranch__.+-main-1$")

HARNESS_STALE_BUILD_PATTERN = re.compile(
    r"(?:docker compose|docker-compose compose).+docker-compose-build\.yaml build"
)

HARNESS_STALE_BUILDX_PATTERN = re.compile(
    r"docker-buildx bake .*--allow fs\.read=.*harbor-task-[^/]+/environment"
)

HARNESS_STALE_RUN_PATTERN = re.compile(r"\bharbor run --path .*harbor-task-")


def cleanup_stale_harbor_resources(
    *, include_containers: bool = True, include_build_processes: bool = False
) -> None:
    """Remove stale Harbor containers and/or orphaned build processes."""
    if include_containers:
        cleanup_stale_harbor_containers()
    if include_build_processes:
        cleanup_stale_harbor_build_processes()


def cleanup_stale_harbor_containers() -> None:
    """Remove stale Harbor scenario-run containers that can block future runs."""
    try:
        listing = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.ID}}\t{{.Names}}\t{{.Status}}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return
    if listing.returncode != 0:
        return

    stale_ids: list[str] = []
    for line in listing.stdout.splitlines():
        parsed = _parse_container_listing_line(line)
        if not parsed:
            continue
        container_id, name, status = parsed
        if not _is_stale_harbor_container(name=name, status=status):
            continue
        stale_ids.append(container_id)
    for container_id in stale_ids:
        subprocess.run(
            ["docker", "rm", "-f", container_id],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )


def _parse_container_listing_line(line: str) -> tuple[str, str, str] | None:
    line = line.strip()
    if not line:
        return None
    parts = line.split("\t", maxsplit=2)
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


def _is_stale_harbor_container(*, name: str, status: str) -> bool:
    if not (
        HARNESS_STALE_CONTAINER_PATTERN.match(name) or HARBOR_GIT_MULTIBRANCH_PATTERN.match(name)
    ):
        return False
    # Do not kill active containers; parallel runs may be in-flight.
    return not status.startswith("Up ")


def cleanup_stale_harbor_build_processes() -> None:
    """Kill orphaned Harbor docker-compose/buildx build processes."""
    parsed = _collect_harbor_process_candidates()
    if parsed is None:
        return

    process_table, candidate_pids, orphan_harbor_run_pids = parsed
    orphan_harbor_run_set = set(orphan_harbor_run_pids)
    stale_build_pids = _stale_harbor_build_pids(
        process_table=process_table,
        candidate_pids=candidate_pids,
        orphan_harbor_run_set=orphan_harbor_run_set,
    )
    stale_pids = sorted(set(orphan_harbor_run_pids).union(stale_build_pids))
    for pid in stale_pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue


def _collect_harbor_process_candidates() -> tuple[dict[int, int], list[int], list[int]] | None:
    try:
        listing = subprocess.run(
            ["ps", "-ax", "-o", "pid=,ppid=,command="],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listing.returncode != 0:
        return None

    process_table: dict[int, int] = {}
    candidate_pids: list[int] = []
    orphan_harbor_run_pids: list[int] = []
    for line in listing.stdout.splitlines():
        parsed = _parse_process_listing_line(line)
        if parsed is None:
            continue
        pid, ppid, command = parsed
        process_table[pid] = ppid
        if _is_orphan_harbor_run_command(command=command, ppid=ppid):
            orphan_harbor_run_pids.append(pid)
        if _is_harbor_build_command(command):
            candidate_pids.append(pid)

    return process_table, candidate_pids, orphan_harbor_run_pids


def _stale_harbor_build_pids(
    *,
    process_table: dict[int, int],
    candidate_pids: list[int],
    orphan_harbor_run_set: set[int],
) -> list[int]:
    return [
        pid
        for pid in candidate_pids
        if process_table.get(pid, 0) <= 1
        or _has_ancestor_in_set(
            pid=pid,
            process_table=process_table,
            ancestor_set=orphan_harbor_run_set,
        )
    ]


def _parse_process_listing_line(line: str) -> tuple[int, int, str] | None:
    line = line.strip()
    if not line:
        return None
    parts = line.split(maxsplit=2)
    if len(parts) != 3:
        return None
    pid_text, ppid_text, command = parts
    if not pid_text.isdigit() or not ppid_text.isdigit():
        return None
    return int(pid_text), int(ppid_text), command


def _is_harbor_build_command(command: str) -> bool:
    return bool(
        HARNESS_STALE_BUILD_PATTERN.search(command) or HARNESS_STALE_BUILDX_PATTERN.search(command)
    )


def _is_orphan_harbor_run_command(*, command: str, ppid: int) -> bool:
    return ppid <= 1 and bool(HARNESS_STALE_RUN_PATTERN.search(command))


def _has_ancestor_in_set(
    *,
    pid: int,
    process_table: dict[int, int],
    ancestor_set: set[int],
) -> bool:
    current = process_table.get(pid, 0)
    seen: set[int] = set()
    while current > 1 and current not in seen:
        if current in ancestor_set:
            return True
        seen.add(current)
        current = process_table.get(current, 0)
    return current in ancestor_set
