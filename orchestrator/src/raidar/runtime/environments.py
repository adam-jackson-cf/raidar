"""Scenario environment library resolution and inventory compatibility checks."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from raidar.runtime.harbor import validate_public_base_images
from raidar.runtime.profile import RuntimeProfile
from raidar.scenario_paths import resolve_relative_file
from raidar.schemas.environment import (
    CapabilityRequirements,
    EnvironmentConfig,
    EnvironmentResourcesConfig,
)

TASK_IMAGE_PROBE_SCHEMA_VERSION = "1"


class EnvironmentResolutionError(ValueError):
    """Raised when a scenario environment cannot be resolved or validated."""


class EnvironmentBuildMetadata(BaseModel):
    """Library-owned Docker build metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dockerfile: str

    @field_validator("dockerfile")
    @classmethod
    def _validate_dockerfile_path(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("environment build dockerfile must not be empty")
        return value


class EnvironmentLibraryEntry(BaseModel):
    """Repo-local environment metadata entry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    version: int = Field(ge=1)
    image: str
    build: EnvironmentBuildMetadata
    capabilities: CapabilityRequirements = Field(default_factory=CapabilityRequirements)


@dataclass(frozen=True, slots=True)
class ResolvedEnvironmentLibraryEntry:
    """One fingerprinted entry discovered in a configured registry root."""

    library: EnvironmentLibraryEntry
    registry_root: Path
    metadata_path: Path
    dockerfile_path: Path
    metadata_fingerprint: str
    dockerfile_fingerprint: str

    def cache_payload(self) -> dict[str, Any]:
        return {
            "id": self.library.id,
            "version": self.library.version,
            "image": self.library.image,
            "registry_root": str(self.registry_root),
            "metadata": str(self.metadata_path),
            "dockerfile": str(self.dockerfile_path),
            "metadata_fingerprint": self.metadata_fingerprint,
            "dockerfile_fingerprint": self.dockerfile_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ResolvedEnvironmentRegistry:
    """Ordered environment roots and their immutable discovered entries."""

    roots: tuple[Path, ...]
    entries: tuple[ResolvedEnvironmentLibraryEntry, ...]

    def index(self) -> dict[str, ResolvedEnvironmentLibraryEntry]:
        return {entry.library.id: entry for entry in self.entries}

    def cache_payload(self) -> dict[str, Any]:
        return {
            "roots": [str(root) for root in self.roots],
            "entries": [entry.cache_payload() for entry in self.entries],
        }


@dataclass(frozen=True, slots=True)
class ResolvedEnvironment:
    """Path-aware scenario environment selection."""

    config: EnvironmentConfig
    library: EnvironmentLibraryEntry
    dockerfile_path: Path
    metadata_path: Path
    dockerfile_fingerprint: str
    metadata_fingerprint: str

    @property
    def id(self) -> str:
        return self.library.id

    @property
    def image(self) -> str:
        return self.library.image

    @property
    def version(self) -> int:
        return self.library.version

    @property
    def resources(self) -> EnvironmentResourcesConfig:
        return self.config.resources

    @property
    def workdir(self) -> str:
        return self.config.workdir

    @property
    def requirements(self) -> CapabilityRequirements:
        return self.config.requirements

    def cache_payload(self) -> dict[str, Any]:
        """Return stable metadata that should invalidate task image caches."""

        return {
            "id": self.id,
            "image": self.image,
            "version": self.version,
            "workdir": self.workdir,
            "allow_internet": self.config.allow_internet,
            "resources": self.resources.model_dump(mode="json"),
            "requirements": self.requirements.model_dump(mode="json"),
            "capabilities": self.library.capabilities.model_dump(mode="json"),
            "dockerfile": str(self.dockerfile_path),
            "metadata": str(self.metadata_path),
            "dockerfile_fingerprint": self.dockerfile_fingerprint,
            "metadata_fingerprint": self.metadata_fingerprint,
        }

    def report_payload(self) -> dict[str, Any]:
        """Return human-readable resolved environment metadata."""

        return {
            "id": self.id,
            "image": self.image,
            "version": self.version,
            "workdir": self.workdir,
            "dockerfile": str(self.dockerfile_path),
            "metadata": str(self.metadata_path),
            "requirements": self.requirements.model_dump(mode="json"),
            "capabilities": self.library.capabilities.model_dump(mode="json"),
        }


def resolve_scenario_environment(
    *,
    scenario,
    scenario_path: Path,
    repo_root: Path,
    registry: ResolvedEnvironmentRegistry,
    docker_registry_allowlist: tuple[str, ...],
) -> ResolvedEnvironment:
    """Resolve the scenario-owned environment against a resolved library."""

    if scenario.environment.kind == "stack_preset":
        resolved_entry = _environment_library_entry_by_id(
            registry,
            scenario.environment.id,
        )
        library = resolved_entry.library
        metadata_path = resolved_entry.metadata_path
        dockerfile_path = resolved_entry.dockerfile_path
    elif scenario.environment.kind == "custom_docker":
        library, metadata_path, dockerfile_path = _custom_environment_entry(
            scenario.environment,
            scenario_path=scenario_path,
            repo_root=repo_root,
            docker_registry_allowlist=docker_registry_allowlist,
        )
    else:
        raise EnvironmentResolutionError(
            f"Unsupported environment kind {scenario.environment.kind!r}"
        )
    requirement_errors = [
        *_capability_requirement_errors(
            owner="scenario environment",
            provided=library.capabilities,
            required=scenario.environment.requirements,
        ),
    ]
    for scorer in scenario.resolved_scorers():
        requirement_errors.extend(
            _capability_requirement_errors(
                owner=f"scorer {scorer.ref}",
                provided=library.capabilities,
                required=scorer.requirements,
            )
        )
    if requirement_errors:
        raise EnvironmentResolutionError(
            f"Scenario {scenario.name} environment {library.id} does not satisfy inventory "
            "requirements: " + "; ".join(requirement_errors)
        )
    return ResolvedEnvironment(
        config=scenario.environment,
        library=library,
        dockerfile_path=dockerfile_path,
        metadata_path=metadata_path,
        dockerfile_fingerprint=_file_fingerprint(dockerfile_path),
        metadata_fingerprint=_file_fingerprint(metadata_path),
    )


def resolve_environment_registry(
    *,
    repo_root: Path,
    runtime_profile: RuntimeProfile,
) -> ResolvedEnvironmentRegistry:
    """Discover and fingerprint every configured environment registry entry."""

    resolved_repo_root = repo_root.resolve()
    roots = tuple(
        _resolve_environment_registry_root(resolved_repo_root, configured_root)
        for configured_root in runtime_profile.environment_registry_roots
    )
    if len(roots) != len(set(roots)):
        raise EnvironmentResolutionError("Environment registry roots resolve to duplicate paths")
    entries: list[ResolvedEnvironmentLibraryEntry] = []
    seen: dict[str, Path] = {}
    for registry_root in roots:
        if not registry_root.is_dir():
            raise EnvironmentResolutionError(f"Environment library not found: {registry_root}")
        for metadata_path in sorted(registry_root.glob("**/environment.yaml")):
            library = _load_environment_library_entry(metadata_path)
            dockerfile_path = resolve_relative_file(
                metadata_path.parent,
                library.build.dockerfile,
                field_name=f"{metadata_path}:build.dockerfile",
                root_name="environment library entry",
            )
            try:
                validate_public_base_images(
                    dockerfile_path.read_text(encoding="utf-8"),
                    allowed_registry_hosts=runtime_profile.docker_registry_allowlist,
                )
            except ValueError as exc:
                raise EnvironmentResolutionError(
                    f"Invalid environment Dockerfile {dockerfile_path}: {exc}"
                ) from exc
            if library.id in seen:
                raise EnvironmentResolutionError(
                    f"Duplicate environment id {library.id!r}: {seen[library.id]} and "
                    f"{metadata_path}"
                )
            seen[library.id] = metadata_path
            entries.append(
                ResolvedEnvironmentLibraryEntry(
                    library=library,
                    registry_root=registry_root,
                    metadata_path=metadata_path,
                    dockerfile_path=dockerfile_path,
                    metadata_fingerprint=_file_fingerprint(metadata_path),
                    dockerfile_fingerprint=_file_fingerprint(dockerfile_path),
                )
            )
    return ResolvedEnvironmentRegistry(roots=roots, entries=tuple(entries))


def environment_library_index(
    registry: ResolvedEnvironmentRegistry,
) -> dict[str, ResolvedEnvironmentLibraryEntry]:
    """Return resolved environment registry entries keyed by id."""

    return registry.index()


def _resolve_environment_registry_root(repo_root: Path, configured_root: str) -> Path:
    path = Path(configured_root).expanduser()
    return path.resolve() if path.is_absolute() else (repo_root / path).resolve()


def scorer_requirements_payload(scenario) -> list[dict[str, Any]]:
    """Return stable resolved scorer inventory requirements for cache keys and reports."""

    payload = [
        {
            "scorer": scorer.ref,
            "requirements": scorer.requirements.model_dump(mode="json"),
        }
        for scorer in scenario.resolved_scorers()
    ]
    return sorted(payload, key=lambda item: json.dumps(item, sort_keys=True))


def combined_capability_requirements(
    *, scenario, environment: ResolvedEnvironment
) -> CapabilityRequirements:
    """Return scenario and scorer inventory requirements."""

    combined = CapabilityRequirements.model_validate(
        environment.requirements.model_dump(mode="json")
    )
    for scorer in scenario.resolved_scorers():
        combined = merge_capability_requirements(combined, scorer.requirements)
    return combined


def merge_capability_requirements(
    left: CapabilityRequirements,
    right: CapabilityRequirements,
) -> CapabilityRequirements:
    """Merge capability requirement maps, keeping the stricter duplicate requirement."""

    return CapabilityRequirements(
        runtimes=_merge_requirement_map(left.runtimes, right.runtimes),
        package_managers=_merge_requirement_map(left.package_managers, right.package_managers),
        tools=_merge_requirement_map(left.tools, right.tools),
        browsers=_merge_requirement_map(left.browsers, right.browsers),
    )


def _environment_library_entry_by_id(
    registry: ResolvedEnvironmentRegistry,
    environment_id: str,
) -> ResolvedEnvironmentLibraryEntry:
    index = environment_library_index(registry)
    try:
        return index[environment_id]
    except KeyError as exc:
        available = ", ".join(sorted(index)) or "(none)"
        raise EnvironmentResolutionError(
            f"Unknown environment id {environment_id!r}; available environments: {available}"
        ) from exc


def _custom_environment_entry(
    config: EnvironmentConfig,
    *,
    scenario_path: Path,
    repo_root: Path,
    docker_registry_allowlist: tuple[str, ...],
) -> tuple[EnvironmentLibraryEntry, Path, Path]:
    if config.image is None or config.build is None:
        raise EnvironmentResolutionError("custom_docker environment metadata is incomplete")
    dockerfile_path = resolve_relative_file(
        repo_root,
        config.build.dockerfile,
        field_name=f"{scenario_path}:environment.build.dockerfile",
        root_name="repository",
    )
    try:
        validate_public_base_images(
            dockerfile_path.read_text(encoding="utf-8"),
            allowed_registry_hosts=docker_registry_allowlist,
        )
    except ValueError as exc:
        raise EnvironmentResolutionError(
            f"Invalid custom_docker environment {config.id!r}: {exc}"
        ) from exc
    try:
        library = EnvironmentLibraryEntry.model_validate(
            {
                "id": config.id,
                "version": 1,
                "image": config.image,
                "build": config.build.model_dump(mode="json"),
                "capabilities": config.capabilities.model_dump(mode="json"),
            }
        )
    except ValueError as exc:
        raise EnvironmentResolutionError(
            f"Invalid custom_docker environment metadata {scenario_path}: {exc}"
        ) from exc
    metadata_path = scenario_path
    return library, metadata_path, dockerfile_path


def _load_environment_library_entry(path: Path) -> EnvironmentLibraryEntry:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise EnvironmentResolutionError(f"Invalid environment metadata YAML: {path}") from exc
    if not isinstance(payload, dict):
        raise EnvironmentResolutionError(f"Environment metadata must be a mapping: {path}")
    try:
        return EnvironmentLibraryEntry.model_validate(payload)
    except ValueError as exc:
        raise EnvironmentResolutionError(f"Invalid environment metadata {path}: {exc}") from exc


def _capability_requirement_errors(
    *,
    owner: str,
    provided: CapabilityRequirements,
    required: CapabilityRequirements,
) -> list[str]:
    errors: list[str] = []
    for category in ("runtimes", "package_managers", "tools", "browsers"):
        provided_items = getattr(provided, category)
        required_items = getattr(required, category)
        for name, required_spec in sorted(required_items.items()):
            provided_spec = provided_items.get(name)
            if provided_spec is None:
                errors.append(f"{owner} requires {category}.{name}")
                continue
            if not capability_spec_satisfies(provided_spec, required_spec):
                errors.append(
                    f"{owner} requires {category}.{name} {required_spec}, "
                    f"environment provides {provided_spec}"
                )
    return errors


def capability_spec_satisfies(provided_spec: str, required_spec: str) -> bool:
    """Return whether a provided inventory spec satisfies a required spec."""

    provided = _parse_version_spec(provided_spec)
    required = _parse_version_spec(required_spec)
    if provided is None or required is None:
        return provided_spec == required_spec

    provided_operator, provided_version = provided
    required_operator, required_version = required
    if required_operator in {">=", ">"}:
        return _provided_lower_bound_satisfies(
            provided_operator,
            provided_version,
            required_operator,
            required_version,
        )
    if required_operator in {"", "=", "=="}:
        return _compare_versions(provided_version, required_version) == 0
    return False


def normalize_probe_version(value: str) -> str:
    """Normalize common command version output to a bare numeric version."""

    match = re.search(r"v?([0-9]+(?:\.[0-9]+)*)", value)
    return match.group(1) if match else value.strip()


def _provided_lower_bound_satisfies(
    provided_operator: str,
    provided_version: tuple[int, ...],
    required_operator: str,
    required_version: tuple[int, ...],
) -> bool:
    comparison = _compare_versions(provided_version, required_version)
    if required_operator == ">=":
        if provided_operator == ">":
            return comparison >= 0
        return comparison >= 0
    if required_operator == ">":
        if provided_operator == ">":
            return comparison >= 0
        return comparison > 0
    return False


def _merge_requirement_map(left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
    merged = dict(left)
    for name, spec in right.items():
        existing = merged.get(name)
        if existing is None or capability_spec_satisfies(spec, existing):
            merged[name] = spec
    return merged


def _parse_version_spec(spec: str) -> tuple[str, tuple[int, ...]] | None:
    match = re.fullmatch(r"\s*(>=|>|==|=)?\s*v?([0-9]+(?:\.[0-9]+)*)\s*", spec)
    if match is None:
        return None
    version = _parse_version(match.group(2))
    if version is None:
        return None
    return match.group(1) or "", version


def _parse_version(version: str) -> tuple[int, ...] | None:
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version):
        return None
    return tuple(int(part) for part in version.split("."))


def _compare_versions(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    length = max(len(left), len(right))
    padded_left = left + (0,) * (length - len(left))
    padded_right = right + (0,) * (length - len(right))
    return (padded_left > padded_right) - (padded_left < padded_right)


def _file_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
