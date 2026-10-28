"""The ``declared-wheels/1`` pin: the manifest's ``dependencies`` block and its lock records.

A problem pin carries the policy, the resolver and index, the interpreter, the platforms, the
cutoff (``exclude_newer``, the import time in UTC) and the dependency *sets* the problem's
graded code is provisioned from: one per reference step, the agent seed (the problem's
``test_dependencies`` only) and the grader's environment (the verifier tools plus
``test_dependencies``). Each set is resolved for both architectures into a hashed
``pylock.toml``; the pin records the lock digests and the lock files sit beside the manifest
(``<version>.deps/pylock.<set12>-<arch>.toml``). Nothing here resolves or installs anything.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from pathlib import Path

from pydantic import Field

from raidar.contract.canonical import digest_json
from raidar.contract.wheels import ARCHES
from raidar.grading.models import VerifierModel

DEPENDENCY_DIR_SUFFIX = ".deps"
REFUSAL_REASON = "dependency_unresolvable"
_SET_CHARS = 12

LockKey = tuple[str, str]
"""``(set_sha256, arch)``."""


class DependencyLockError(ValueError):
    """A recorded lock is missing, unreadable or does not match its recorded digest."""


def set_sha256(requirements: Iterable[str]) -> str:
    """Order-independent digest of a declared requirement set."""
    lines = sorted({line.strip() for line in requirements if line.strip()})
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def lock_sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Unprovided(VerifierModel):
    """A requirement the policy cannot provide, and why."""

    requirement: str
    reason: str


class DependencySet(VerifierModel):
    """One resolved set: its requirements and the digest of its lock for each architecture."""

    requirements: tuple[str, ...]
    amd64: str
    arm64: str

    def lock(self, arch: str) -> str:
        return {"amd64": self.amd64, "arm64": self.arm64}[arch]


class DependencyPins(VerifierModel):
    """The manifest's ``dependencies`` block."""

    policy: str
    resolver: str
    index: str
    python: str
    platforms: tuple[str, ...]
    exclude_newer: str = Field(min_length=1, description="cutoff: import time, RFC 3339 UTC")
    sets: dict[str, DependencySet]
    steps: dict[str, str]
    seed: str | None = Field(
        default=None, description="set key of the problem's test_dependencies (agent seed)"
    )
    grader: str | None = Field(
        default=None, description="set key of the grader tools plus test_dependencies"
    )

    def lock_filename(self, set_key: str, arch: str) -> str:
        return f"pylock.{set_key[:_SET_CHARS]}-{arch}.toml"

    def set_for_step(self, step: str) -> DependencySet | None:
        key = self.steps.get(step)
        return self.sets[key] if key is not None else None

    def digest(self) -> str:
        """``dependencies_sha256``: the experiment lock's per-problem digest of this block."""
        return digest_json(self.model_dump(mode="json"))

    def lock_keys(self) -> list[LockKey]:
        return [(key, arch) for key in sorted(self.sets) for arch in ARCHES]


class DependencyRefusal(VerifierModel):
    """A problem excluded because its reference dependencies cannot be provisioned."""

    reason: str = REFUSAL_REASON
    detail: str
    exclude_newer: str
    unprovided: tuple[Unprovided, ...] = ()


class DependencyLocks:
    """The lock files of one pin directory, addressed by ``(set_sha256, arch)``."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path(self, set_key: str, arch: str) -> Path:
        return self.directory / f"pylock.{set_key[:_SET_CHARS]}-{arch}.toml"

    def has(self, set_key: str, arch: str) -> bool:
        return self.path(set_key, arch).is_file()

    def write(self, set_key: str, arch: str, text: str) -> str:
        path = self.path(set_key, arch)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return lock_sha256(text)

    def read(self, set_key: str, arch: str, expected_sha256: str) -> str:
        path = self.path(set_key, arch)
        try:
            text = path.read_text()
        except OSError as exc:
            raise DependencyLockError(f"missing dependency lock {path}: {exc}") from exc
        if lock_sha256(text) != expected_sha256:
            raise DependencyLockError(
                f"dependency lock {path} differs from its pin ({expected_sha256[:12]})"
            )
        return text

    def read_pins(self, pins: DependencyPins) -> dict[LockKey, str]:
        return {
            (key, arch): self.read(key, arch, pins.sets[key].lock(arch))
            for key, arch in pins.lock_keys()
        }

    def write_all(self, locks: Mapping[LockKey, str]) -> None:
        for (key, arch), text in sorted(locks.items()):
            self.write(key, arch, text)
