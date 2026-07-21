"""Resolved runtime operator policy."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _validate_environment_entries(entries: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(entries, tuple):
        raise TypeError("environment variable entries must be string pairs in a tuple")
    for entry in entries:
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise TypeError("environment variable entries must be string pairs in a tuple")
        if not all(isinstance(value, str) for value in entry):
            raise TypeError("environment variable entries must be string pairs in a tuple")
    return entries


def _validate_unique_environment_names(entries: tuple[tuple[str, str], ...]) -> None:
    names = [name for name, _value in entries]
    if any(not name.strip() for name in names):
        raise ValueError("environment variable names must not be empty")
    if len(names) != len(set(names)):
        raise ValueError("environment variable names must be unique")


def _validate_cleanup_collection_types(policy: CleanupPolicy) -> None:
    tuple_fields = (
        policy.container_name_prefixes,
        policy.build_command_marker_groups,
        policy.run_command_markers,
    )
    if any(not isinstance(value, tuple) for value in tuple_fields):
        raise TypeError("cleanup policy collections must be tuples")
    if any(not isinstance(group, tuple) for group in policy.build_command_marker_groups):
        raise TypeError("cleanup policy collections must be tuples")


def _validate_cleanup_collection_values(policy: CleanupPolicy) -> None:
    if any(not value for value in policy.container_name_prefixes):
        raise ValueError("cleanup container prefixes must not be empty")
    for group in policy.build_command_marker_groups:
        if not group or any(not marker for marker in group):
            raise ValueError("cleanup build marker groups must contain non-empty markers")
    if any(not marker for marker in policy.run_command_markers):
        raise ValueError("cleanup run markers must not be empty")


@dataclass(frozen=True, slots=True)
class EnvironmentVariables(Mapping[str, str]):
    """Immutable environment variable templates."""

    entries: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        entries = _validate_environment_entries(self.entries)
        _validate_unique_environment_names(entries)

    def __getitem__(self, key: str) -> str:
        for name, value in self.entries:
            if name == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (name for name, _value in self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def payload(self) -> dict[str, str]:
        return dict(self.entries)


@dataclass(frozen=True, slots=True)
class DockerLabels:
    """Label keys used for managed task images."""

    managed: str = "io.raidar.cache.managed"
    key: str = "io.raidar.cache.key"
    harness: str = "io.raidar.cache.harness"
    repo: str = "io.raidar.cache.repo"

    def __post_init__(self) -> None:
        values = (self.managed, self.key, self.harness, self.repo)
        if any(not isinstance(value, str) for value in values):
            raise TypeError("docker label keys must be strings")
        if any(not value.strip() for value in values):
            raise ValueError("docker label keys must not be empty")
        if len(values) != len(set(values)):
            raise ValueError("docker label keys must be unique")

    def payload(self) -> dict[str, str]:
        return {
            "managed": self.managed,
            "key": self.key,
            "harness": self.harness,
            "repo": self.repo,
        }


@dataclass(frozen=True, slots=True)
class CleanupPolicy:
    """Stale Harbor resource matching policy."""

    container_name_prefixes: tuple[str, ...] = ("harbor-task", "git-multibranch__")
    container_name_suffix: str = "-main-1"
    build_command_marker_groups: tuple[tuple[str, ...], ...] = (
        ("docker-compose-build.yaml build",),
        ("docker-buildx bake", "harbor-task-", "/environment"),
    )
    run_command_markers: tuple[str, ...] = ("harbor run --path", "harbor-task-")

    def __post_init__(self) -> None:
        _validate_cleanup_collection_types(self)
        _validate_cleanup_collection_values(self)

    def payload(self) -> dict[str, Any]:
        return {
            "container_name_prefixes": list(self.container_name_prefixes),
            "container_name_suffix": self.container_name_suffix,
            "build_command_marker_groups": [
                list(group) for group in self.build_command_marker_groups
            ],
            "run_command_markers": list(self.run_command_markers),
        }


@dataclass(frozen=True, slots=True)
class EvidenceLimits:
    """Limits applied while retaining deterministic evidence."""

    file_bytes: int = 65536
    text_chars: int = 4000
    list_items: int = 50
    list_item_chars: int = 500

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, int) or isinstance(value, bool)
            for value in (self.file_bytes, self.text_chars, self.list_items, self.list_item_chars)
        ):
            raise TypeError("evidence limits must be integers")
        if min(self.file_bytes, self.text_chars, self.list_items, self.list_item_chars) < 1:
            raise ValueError("evidence limits must be positive")

    def payload(self) -> dict[str, int]:
        return {
            "file_bytes": self.file_bytes,
            "text_chars": self.text_chars,
            "list_items": self.list_items,
            "list_item_chars": self.list_item_chars,
        }


def _compatibility_environment() -> EnvironmentVariables:
    return EnvironmentVariables((("COMPOSE_BAKE", "false"),))


def _workspace_environment() -> EnvironmentVariables:
    return EnvironmentVariables(
        (
            ("TMPDIR", "{tmp}"),
            ("TMP", "{tmp}"),
            ("TEMP", "{tmp}"),
            ("XDG_CACHE_HOME", "{runtime}/cache"),
            ("UV_CACHE_DIR", "{runtime}/cache/uv"),
            ("BUN_INSTALL_CACHE_DIR", "{runtime}/cache/bun"),
        )
    )


def _secret_transport_dir() -> Path:
    return Path(__file__).resolve().parents[4] / ".tmp" / "agentic-eval" / "secrets"


def _validate_secret_transport_dir(value: object) -> None:
    if not isinstance(value, Path):
        raise TypeError("runtime profile secret transport directory must be a Path")
    if not value.is_absolute():
        raise ValueError("runtime profile secret transport directory must be absolute")


@dataclass(frozen=True, slots=True)
class RuntimeProfile:
    """Docker, cache, workspace, cleanup, and evidence policy."""

    environment_registry_roots: tuple[str, ...] = ("environments",)
    docker_registry_allowlist: tuple[str, ...] = (
        "docker.io",
        "index.docker.io",
        "registry-1.docker.io",
        "ghcr.io",
        "quay.io",
        "mcr.microsoft.com",
        "public.ecr.aws",
        "gcr.io",
        "us.gcr.io",
        "eu.gcr.io",
        "asia.gcr.io",
        "registry.k8s.io",
    )
    compose_minimum_version: str = "2.40.1"
    compatibility_env: EnvironmentVariables = field(default_factory=_compatibility_environment)
    cache_prune_interval_sec: int = 6 * 60 * 60
    prep_cache_max_age_sec: int = 7 * 24 * 60 * 60
    prep_cache_max_bytes: int = 2 * 1024 * 1024 * 1024
    docker_cache_max_age_sec: int = 14 * 24 * 60 * 60
    task_image_build_min_timeout_sec: int = 120
    docker_command_timeout_sec: int = 60
    docker_inspect_timeout_sec: int = 30
    docker_labels: DockerLabels = field(default_factory=DockerLabels)
    cleanup: CleanupPolicy = field(default_factory=CleanupPolicy)
    secret_transport_dir: Path = field(default_factory=_secret_transport_dir)
    workspace_env: EnvironmentVariables = field(default_factory=_workspace_environment)
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
    evidence_limits: EvidenceLimits = field(default_factory=EvidenceLimits)

    def __post_init__(self) -> None:
        self._validate_policy_types()
        self._validate_registry_policy()
        self._validate_compose_version()
        self._validate_limits_and_paths()

    def _validate_policy_types(self) -> None:
        typed_policies = (
            ("compatibility_env", self.compatibility_env, EnvironmentVariables),
            ("docker_labels", self.docker_labels, DockerLabels),
            ("cleanup", self.cleanup, CleanupPolicy),
            ("workspace_env", self.workspace_env, EnvironmentVariables),
            ("evidence_limits", self.evidence_limits, EvidenceLimits),
        )
        for name, value, expected_type in typed_policies:
            if not isinstance(value, expected_type):
                raise TypeError(f"runtime profile {name} must be {expected_type.__name__}")
        tuple_policies = (
            ("environment registry roots", self.environment_registry_roots),
            ("docker registry allowlist", self.docker_registry_allowlist),
            ("copy excludes", self.copy_excludes),
            ("archive excludes", self.archive_excludes),
            ("prune directories", self.prune_dirs),
        )
        for name, values in tuple_policies:
            if not isinstance(values, tuple) or any(not isinstance(value, str) for value in values):
                raise TypeError(f"runtime profile {name} must be a tuple of strings")

    def _validate_registry_policy(self) -> None:
        if not self.environment_registry_roots or any(
            not root.strip() for root in self.environment_registry_roots
        ):
            raise ValueError("runtime profile must declare non-empty environment registry roots")
        if len(self.environment_registry_roots) != len(set(self.environment_registry_roots)):
            raise ValueError("environment registry roots must be unique")
        if not self.docker_registry_allowlist or any(
            not host.strip() or host != host.lower() for host in self.docker_registry_allowlist
        ):
            raise ValueError("docker registry allowlist hosts must be non-empty lowercase values")
        if len(self.docker_registry_allowlist) != len(set(self.docker_registry_allowlist)):
            raise ValueError("docker registry allowlist hosts must be unique")

    def _validate_compose_version(self) -> None:
        if not isinstance(self.compose_minimum_version, str):
            raise TypeError("compose minimum version must be a string")
        try:
            compose_version = tuple(int(part) for part in self.compose_minimum_version.split("."))
        except ValueError as exc:
            raise ValueError("compose minimum version must be major.minor.patch") from exc
        if len(compose_version) != 3 or min(compose_version) < 0:
            raise ValueError("compose minimum version must be major.minor.patch")

    def _validate_limits_and_paths(self) -> None:
        _validate_secret_transport_dir(self.secret_transport_dir)
        positive_values = (
            self.cache_prune_interval_sec,
            self.prep_cache_max_age_sec,
            self.prep_cache_max_bytes,
            self.docker_cache_max_age_sec,
            self.task_image_build_min_timeout_sec,
            self.docker_command_timeout_sec,
            self.docker_inspect_timeout_sec,
        )
        if any(not isinstance(value, int) or isinstance(value, bool) for value in positive_values):
            raise TypeError("runtime profile time and size limits must be integers")
        if min(positive_values) < 1:
            raise ValueError("runtime profile time and size limits must be positive")
        for name, values in (
            ("copy excludes", self.copy_excludes),
            ("archive excludes", self.archive_excludes),
            ("prune directories", self.prune_dirs),
        ):
            if any(not value for value in values) or len(values) != len(set(values)):
                raise ValueError(f"runtime profile {name} must be non-empty unique values")

    def cache_payload(self) -> dict[str, Any]:
        """Return stable material that affects runtime execution."""

        return {
            "environment_registry_roots": list(self.environment_registry_roots),
            "docker_registry_allowlist": list(self.docker_registry_allowlist),
            "compose_minimum_version": self.compose_minimum_version,
            "compatibility_env": self.compatibility_env.payload(),
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
                "labels": self.docker_labels.payload(),
            },
            "cleanup": self.cleanup.payload(),
            "workspace_env": self.workspace_env.payload(),
            "copy_excludes": list(self.copy_excludes),
            "archive_excludes": list(self.archive_excludes),
            "prune_dirs": list(self.prune_dirs),
            "evidence_limits": self.evidence_limits.payload(),
        }


def default_runtime_profile() -> RuntimeProfile:
    """Resolve the strict profile at an application composition root."""

    return RuntimeProfile()
