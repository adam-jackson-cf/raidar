"""Typed contracts for the Python pack definition and its measurement tool lock."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

PACK_DIR = Path(__file__).resolve().parent
PACK_SPEC_PATH = PACK_DIR / "pack.yaml"
TOOL_LOCK_PATH = PACK_DIR / "tools.lock.yaml"

# ``name==version`` and nothing else: no ranges, wildcards, extras or markers.
_EXACT_PIN = re.compile(
    r"(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)==(?P<version>[0-9][0-9A-Za-z.+!]*)"
)


def pinned_distribution(package: str) -> str:
    """Normalised distribution name of an exact ``name==version`` pin (PEP 503)."""
    match = _EXACT_PIN.fullmatch(package)
    if match is None:
        raise ValueError(f"not an exact 'name==version' pin: {package!r}")
    return re.sub(r"[-_.]+", "-", match["name"]).lower()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HarnessPin(_Strict):
    npm_package: str
    version: str
    executable: str
    # Harbor ``AgentName`` whose install() version check must accept the baked-in
    # binary; the image audit replays that check (``harness.harbor_reported_version``).
    harbor_agent: str


# Codex refuses to create its helper binaries under a temporary directory and
# prints a warning line first on ``codex --version``; Harbor parses that first
# line as the version, misses the pin and reinstalls codex in every trial.
_TEMP_ROOTS = ("/tmp", "/var/tmp", "/dev/shm")


class ImageSpec(_Strict):
    repository: str
    context: str
    base_image: str
    agent_home: str
    harnesses: dict[str, HarnessPin]
    forbidden_executables: tuple[str, ...]

    @model_validator(mode="after")
    def _base_image_pinned_by_digest(self) -> ImageSpec:
        if "@sha256:" not in self.base_image:
            raise ValueError("image.base_image must be pinned by digest (@sha256:...)")
        return self

    @model_validator(mode="after")
    def _agent_home_outside_temp_dirs(self) -> ImageSpec:
        if not PurePosixPath(self.agent_home).is_absolute():
            raise ValueError(f"image.agent_home must be absolute, got {self.agent_home!r}")
        # Lexically normalised, so '/home/../tmp/x' counts as under /tmp.
        home = PurePosixPath(posixpath.normpath(self.agent_home))
        if any(
            home == PurePosixPath(root) or PurePosixPath(root) in home.parents
            for root in _TEMP_ROOTS
        ):
            raise ValueError(
                f"image.agent_home {self.agent_home!r} is under a temporary directory; "
                "codex then warns on --version and Harbor reinstalls it every trial"
            )
        return self


class TaskEnvironmentSpec(_Strict):
    compose_main_service: dict[str, Any]


class PackSpec(_Strict):
    id: str
    version: int
    python: str
    # How a problem's ``entry_file`` names its snapshot file (SCB fork
    # environment ``commands.entry_file``, e.g. ``{entry_file}.py``).
    entry_file_template: str = Field(pattern=r"\{entry_file\}")
    image: ImageSpec
    task_environment: TaskEnvironmentSpec

    def entry_file(self, name: str) -> str:
        """The snapshot-relative entrypoint for a problem's configured ``entry_file``."""
        formatted = self.entry_file_template.format(entry_file=name.strip())
        path = PurePosixPath(formatted)
        if not name.strip() or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"entry_file {name!r} does not name a snapshot-relative file")
        return formatted

    @property
    def context_dir(self) -> Path:
        return PACK_DIR / self.image.context


class ToolExecution(StrEnum):
    """Where a measurement tool runs (design memo §4.1)."""

    HOST_PARSE = "host-parse"
    EVAL_SANDBOX = "eval-sandbox"


class ToolInstaller(StrEnum):
    UV_TOOL = "uv-tool"
    UV_ENV = "uv-env"


class ToolSpec(_Strict):
    execution: ToolExecution
    installer: ToolInstaller
    packages: tuple[str, ...] = Field(min_length=1)
    executables: tuple[str, ...]
    modules: tuple[str, ...]

    @model_validator(mode="after")
    def _packages_exact_pinned(self) -> ToolSpec:
        unpinned = [p for p in self.packages if _EXACT_PIN.fullmatch(p) is None]
        if unpinned:
            raise ValueError(f"tool packages must be exact-pinned as 'name==version': {unpinned}")
        return self


class ToolLock(_Strict):
    version: int
    runtime: str
    python: str
    exclude_newer: str
    tools: dict[str, ToolSpec]

    def lock_hash(self) -> str:
        """Content hash of the whole lock; the host cache is keyed by it."""
        return _canonical_hash(self.model_dump(mode="json"))

    def tool(self, name: str) -> ToolSpec:
        try:
            return self.tools[name]
        except KeyError as error:
            known = ", ".join(sorted(self.tools))
            raise KeyError(f"unknown {self.runtime} tool {name!r}; known: {known}") from error

    def scorer_executables(self) -> tuple[str, ...]:
        return tuple(sorted({exe for spec in self.tools.values() for exe in spec.executables}))

    def scorer_modules(self) -> tuple[str, ...]:
        return tuple(sorted({mod for spec in self.tools.values() for mod in spec.modules}))

    def scorer_distributions(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    pinned_distribution(package)
                    for spec in self.tools.values()
                    for package in spec.packages
                }
            )
        )


def _canonical_hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping")
    return data


def load_pack_spec(path: Path = PACK_SPEC_PATH) -> PackSpec:
    return PackSpec.model_validate(_load_yaml(path))


def load_tool_lock(path: Path = TOOL_LOCK_PATH) -> ToolLock:
    return ToolLock.model_validate(_load_yaml(path))
