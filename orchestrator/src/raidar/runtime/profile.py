"""Runtime operator policy profile."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RuntimeProfile:
    """Docker, cache, workspace, cleanup, and evidence policy."""

    id: str = "local-default"
    docker_registry_allowlist: tuple[str, ...] = (
        "docker.io",
        "ghcr.io",
        "oven",
        "python",
        "node",
    )
    compose_minimum_version: str = "2.40.1"
    compatibility_env: dict[str, str] = field(default_factory=lambda: {"COMPOSE_BAKE": "false"})
    network_default: str = "none"
    cache_prune_interval_sec: int = 6 * 60 * 60
    prep_cache_max_age_sec: int = 7 * 24 * 60 * 60
    prep_cache_max_bytes: int = 2 * 1024 * 1024 * 1024
    docker_cache_max_age_sec: int = 14 * 24 * 60 * 60
    task_image_build_min_timeout_sec: int = 120
    docker_command_timeout_sec: int = 60
    docker_inspect_timeout_sec: int = 30
    docker_labels: dict[str, str] = field(
        default_factory=lambda: {
            "managed": "io.raidar.cache.managed",
            "key": "io.raidar.cache.key",
            "harness": "io.raidar.cache.harness",
            "repo": "io.raidar.cache.repo",
        }
    )
    cleanup: dict[str, Any] = field(
        default_factory=lambda: {
            "container_name_prefixes": ("harbor-task", "git-multibranch__"),
            "container_name_suffix": "-main-1",
            "build_command_marker_groups": (
                ("docker-compose-build.yaml build",),
                ("docker-buildx bake", "harbor-task-", "/environment"),
            ),
            "run_command_markers": ("harbor run --path", "harbor-task-"),
        }
    )
    workspace_env: dict[str, str] = field(
        default_factory=lambda: {
            "TMPDIR": "{tmp}",
            "TMP": "{tmp}",
            "TEMP": "{tmp}",
            "XDG_CACHE_HOME": "{runtime}/cache",
            "UV_CACHE_DIR": "{runtime}/cache/uv",
            "BUN_INSTALL_CACHE_DIR": "{runtime}/cache/bun",
        }
    )
    copy_excludes: tuple[str, ...] = (
        "node_modules",
        ".next",
        ".cache",
        ".tmp",
        "jobs",
        "harbor-task",
        "harbor-task-*",
    )
    archive_excludes: tuple[str, ...] = ("node_modules", ".next", ".cache", ".tmp", "jobs")
    prune_dirs: tuple[str, ...] = (
        "node_modules",
        ".next",
        ".turbo",
        ".cache",
        "coverage",
        "dist",
        "build",
        "tmp",
    )
    evidence_limits: dict[str, int] = field(
        default_factory=lambda: {
            "file_bytes": 65536,
            "text_chars": 4000,
            "list_items": 50,
            "list_item_chars": 500,
        }
    )

    def cache_payload(self) -> dict[str, Any]:
        """Return stable material that affects runtime execution."""

        return {
            "id": self.id,
            "docker_registry_allowlist": list(self.docker_registry_allowlist),
            "compose_minimum_version": self.compose_minimum_version,
            "compatibility_env": self.compatibility_env,
            "network_default": self.network_default,
            "cache": {
                "prune_interval_sec": self.cache_prune_interval_sec,
                "prep_max_age_sec": self.prep_cache_max_age_sec,
                "prep_max_bytes": self.prep_cache_max_bytes,
                "docker_max_age_sec": self.docker_cache_max_age_sec,
                "task_image_build_min_timeout_sec": self.task_image_build_min_timeout_sec,
            },
            "docker": {
                "command_timeout_sec": self.docker_command_timeout_sec,
                "inspect_timeout_sec": self.docker_inspect_timeout_sec,
                "labels": self.docker_labels,
            },
            "cleanup": self.cleanup,
            "workspace_env": self.workspace_env,
            "copy_excludes": list(self.copy_excludes),
            "archive_excludes": list(self.archive_excludes),
            "prune_dirs": list(self.prune_dirs),
            "evidence_limits": self.evidence_limits,
        }


def default_runtime_profile() -> RuntimeProfile:
    """Return the strict local runtime profile."""

    return RuntimeProfile()
