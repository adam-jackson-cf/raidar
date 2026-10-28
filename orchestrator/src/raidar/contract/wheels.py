"""The ``declared-wheels/1`` wheel policy below every tier that installs or resolves wheels.

Wheels only (CPython 3.12, Linux, both pack architectures), hash-pinned, from the index's
own file host. This module holds the shared, dependency-free pieces: the policy constants, the
wheel/lock readers (``pylock.toml`` from the host resolver and ``uv.lock`` from stage packages),
the architecture fit rule and the hashed offline install requirements. Nothing here fetches,
resolves, unpacks or imports a package.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass

from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename

POLICY = "declared-wheels/1"
INDEX = "https://pypi.org/simple"
PYTHON = "3.12"
ARCHES = ("amd64", "arm64")
PLATFORMS = {"amd64": "x86_64-manylinux_2_28", "arm64": "aarch64-manylinux_2_28"}
MAX_WHEEL_BYTES = 1 << 30
WHEEL_URL_PREFIXES = ("https://files.pythonhosted.org/",)

OFFLINE_FLAGS = "--no-cache --offline --no-index"
"""``uv pip install`` flags that forbid every cache and network use."""
LOCKED_FLAGS = "--require-hashes --no-deps --only-binary :all:"
"""``uv pip install`` flags that require hash-pinned, prebuilt, non-resolved wheels only."""

_GLIBC = re.compile(r"manylinux_(\d+)_(\d+)_(.+)")
_LEGACY_MANYLINUX = {"manylinux1": (2, 5), "manylinux2010": (2, 12), "manylinux2014": (2, 17)}
_MACHINES = {"amd64": "x86_64", "arm64": "aarch64"}
_SHA256 = re.compile(r"[0-9a-f]{64}")


class DependencyInfraError(RuntimeError):
    """The index or uv is unavailable: an infrastructure failure, not a verdict about packages."""


class WheelPolicyError(ValueError):
    """A lock lists something ``declared-wheels/1`` refuses (source build, direct reference...)."""


@dataclass(frozen=True)
class LockedWheel:
    """One hash-pinned wheel of a lock."""

    name: str
    version: str
    url: str
    sha256: str
    size: int

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1]


def _python_fits(interpreter: str, abi: str) -> bool:
    if interpreter.startswith("py3") or interpreter == "cp312":
        return True
    return abi == "abi3" and interpreter.startswith("cp3") and _cp_minor(interpreter) <= 12


def _cp_minor(interpreter: str) -> int:
    digits = interpreter.removeprefix("cp3")
    return int(digits) if digits.isdigit() else 99


def _platform_fits(platform: str, machine: str) -> bool:
    if platform in {"any", f"linux_{machine}"}:
        return True
    modern = _GLIBC.fullmatch(platform)
    if modern is not None:
        return (int(modern[1]), int(modern[2])) <= (2, 28) and modern[3] == machine
    return any(
        platform == f"{legacy}_{machine}" and glibc <= (2, 28)
        for legacy, glibc in _LEGACY_MANYLINUX.items()
    )


def wheel_fits(filename: str, arch: str) -> bool:
    """Whether the wheel could install on CPython 3.12 / manylinux_2_28 for ``arch``."""
    try:
        tags = parse_wheel_filename(filename)[3]
    except InvalidWheelFilename:
        return False
    machine = _MACHINES[arch]
    return any(
        _python_fits(tag.interpreter, tag.abi) and _platform_fits(tag.platform, machine)
        for tag in tags
    )


def _locked(package: dict[str, object], entry: dict[str, object], digest: object) -> LockedWheel:
    url = str(entry.get("url", ""))
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ValueError(f"lock lists wheel {url} without a sha256")
    return LockedWheel(
        name=canonicalize_name(str(package["name"])),
        version=str(package["version"]),
        url=url,
        sha256=digest,
        size=int(str(entry.get("size", 0))),
    )


def pylock_wheels(lock_text: str) -> list[LockedWheel]:
    """Every wheel a ``pylock.toml`` lists, in lock order."""
    found: list[LockedWheel] = []
    for package in tomllib.loads(lock_text).get("packages", []):
        for entry in package.get("wheels", []):
            hashes = entry.get("hashes")
            digest = hashes.get("sha256") if isinstance(hashes, dict) else None
            found.append(_locked(package, entry, digest))
    return found


def _uv_package_wheels(package: dict[str, object], arch: str) -> list[LockedWheel]:
    name = package.get("name")
    entries = package.get("wheels")
    if not isinstance(entries, list) or not entries:
        raise WheelPolicyError(f"{name} has no wheel (an sdist would need a source build)")
    chosen: list[LockedWheel] = []
    for entry in entries:
        url = str(entry.get("url", ""))
        if not url:
            raise WheelPolicyError(f"{name} lists a wheel without a url")
        if wheel_fits(url.rsplit("/", 1)[-1], arch):
            digest = str(entry.get("hash", "")).removeprefix("sha256:")
            try:
                chosen.append(_locked(package, entry, digest))
            except ValueError as error:
                raise WheelPolicyError(str(error)) from error
    if not chosen:
        raise WheelPolicyError(f"{name} has no {arch} CPython {PYTHON} Linux wheel")
    return chosen


def _uv_installable(package: dict[str, object]) -> bool:
    """Whether a ``uv.lock`` package is an index dependency (not the dependency-only project)."""
    source = package.get("source")
    if not isinstance(source, dict):
        raise WheelPolicyError(f"{package.get('name')} has no recorded source")
    if source.get("registry") == INDEX:
        return True
    if source.get("virtual") == "." and not package.get("wheels"):
        return False
    raise WheelPolicyError(f"{package.get('name')} is not an index package: {sorted(source)}")


def uv_lock_wheels(lock_text: str, arch: str) -> tuple[LockedWheel, ...]:
    """The wheels a stage's ``uv.lock`` installs on ``arch``; anything else is refused.

    Each dependency needs at least one hash-pinned wheel fitting ``arch``; sdist-only packages,
    direct, local, VCS and non-index sources and one package at two versions are refused.
    """
    document = tomllib.loads(lock_text)
    wheels: list[LockedWheel] = []
    seen: dict[str, str] = {}
    for package in document.get("package", []):
        if "sdist" in package:
            raise WheelPolicyError(f"{package.get('name')} lists an sdist (source builds refused)")
        if not _uv_installable(package):
            continue
        name = canonicalize_name(str(package["name"]))
        if seen.setdefault(name, str(package["version"])) != str(package["version"]):
            raise WheelPolicyError(f"{name} is locked at more than one version")
        wheels.extend(_uv_package_wheels(package, arch))
    return tuple(wheels)


def generated_wheels_only_lock(lock_text: str) -> str:
    """Remove source metadata from newly generated uv output, never from an admitted lock.

    The semantic check permits only that removal; unknown uv formatting fails closed.
    Callers must also prove ``uv lock --check --offline`` accepts the generated result.
    ``uv_lock_wheels`` continues to refuse every incoming lock containing an sdist.
    """
    expected = tomllib.loads(lock_text)
    for package in expected.get("package", []):
        package.pop("sdist", None)
    generated = "".join(
        line for line in lock_text.splitlines(keepends=True) if not line.startswith("sdist = ")
    )
    if tomllib.loads(generated) != expected:
        raise WheelPolicyError("uv source metadata is not in the supported generated format")
    refusals = uv_lock_refusals(generated)
    if refusals:
        raise WheelPolicyError("; ".join(refusals))
    return generated


def uv_lock_refusals(lock_text: str) -> list[str]:
    """Why a stage ``uv.lock`` breaks the policy on any pack architecture (empty: accepted)."""
    refusals: list[str] = []
    for arch in ARCHES:
        try:
            uv_lock_wheels(lock_text, arch)
        except (WheelPolicyError, ValueError) as error:
            refusals.append(f"{arch}: {error}")
    return refusals


def hashed_requirements(wheels: Iterable[LockedWheel]) -> str:
    """``name==version`` blocks with every listed wheel hash allowed (``--require-hashes``)."""
    grouped: dict[tuple[str, str], set[str]] = {}
    for wheel in wheels:
        grouped.setdefault((wheel.name, wheel.version), set()).add(wheel.sha256)
    blocks = [
        " \\\n".join([f"{name}=={version}", *(f"    --hash=sha256:{d}" for d in sorted(digests))])
        for (name, version), digests in grouped.items()
    ]
    return "\n".join(blocks) + "\n"
