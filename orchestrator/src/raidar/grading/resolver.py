"""Host-side dependency resolution under policy ``declared-wheels/1``.

``uv pip compile`` reads index metadata only: wheels only (``--only-binary :all:``,
``--no-build``), Python 3.12 for one Linux architecture, ``pypi.org`` only, and an
``exclude-newer`` cutoff. No package code runs and no sdist is built. The result
is a hashed ``pylock.toml`` whose text is byte-identical for identical input.

Failure is classified, never guessed. A failed resolution first runs a *canary*
(a fixed, always-resolvable requirement under the same policy): if the canary
fails too, the index or uv is unreachable and ``DependencyInfraError`` is raised
(an infrastructure failure, never ``unprovided`` and never a score). Otherwise
each requirement is probed alone and classified from the index's file listing as
``no-wheel``, ``after-cutoff`` or ``unresolvable``. Direct references and
unparseable lines are refused up front. A closure over the caps (200 packages,
1 GiB of wheels) is ``cap-exceeded``. A resolution that runs past its time cap (120 s) is
retried once, identically (``TIMEOUT_RETRIES``); at most two attempts are made per declared
set and architecture, and the count is recorded. When both time out it is
``DependencyInfraError`` (never ``cap-exceeded`` and never a score).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import InvalidVersion, Version

from raidar.contract.uv import locked_uv
from raidar.contract.wheels import (
    ARCHES,
    INDEX,
    MAX_WHEEL_BYTES,
    PLATFORMS,
    POLICY,
    PYTHON,
    WHEEL_URL_PREFIXES,
    DependencyInfraError,
    wheel_fits,
)
from raidar.grading.dependency_pins import (
    DependencyPins,
    DependencySet,
    LockKey,
    Unprovided,
    lock_sha256,
    set_sha256,
)

MAX_PACKAGES = 200
TIMEOUT_S = 120.0
TIMEOUT_RETRIES = 1
CANARY_TIMEOUT_S = 30.0
CANARY_REQUIREMENT = "pip==25.0"
GRADER_TOOLS: tuple[str, ...] = (
    "pytest==9.0.3",
    "pytest-timeout==2.4.0",
    "coverage==7.13.4",
)
HTTP_TIMEOUT_S = 30.0
_PASSED_ENV = (
    "PATH",
    "HOME",
    "TMPDIR",
    "LANG",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)
_NETWORK_SIGNS = (
    "error sending request",
    "dns error",
    "failed to fetch",
    "connection refused",
    "connection reset",
    "timed out",
    "network unreachable",
    "tcp connect error",
    "failed to lookup",
    "temporary failure",
    "tls handshake",
    "certificate",
)


class _Exhausted(Exception):  # noqa: N818 - internal control flow: the resolution budget ran out
    """The cumulative resolution deadline passed."""


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _Exhausted
    return remaining


_DIRECT_PREFIXES = (
    "-e ",
    "--editable",
    "./",
    "../",
    "/",
    "~",
    "file:",
    "git+",
    "hg+",
    "svn+",
    "bzr+",
)


def _refusal_reason(text: str) -> str:
    """Why an unparseable requirement line is refused: a direct/local/editable source, or junk."""
    direct = (
        text.startswith(_DIRECT_PREFIXES) or "://" in text or text.endswith((".whl", ".tar.gz"))
    )
    return "direct-reference" if direct else "unparseable"


class DependencyUnresolvable(ValueError):
    """A reference set has unprovided requirements or diverging architectures."""

    def __init__(self, detail: str, unprovided: tuple[Unprovided, ...] = ()) -> None:
        super().__init__(detail)
        self.unprovided = unprovided


@dataclass(frozen=True)
class Resolution:
    lock_text: str
    provided: tuple[str, ...]
    unprovided: tuple[Unprovided, ...]
    resolve_s: float
    attempts: int = 1
    """Resolution attempts made: more than one means earlier attempts timed out."""
    packages: int = 0
    wheel_bytes: int = 0


class Resolver(Protocol):
    """Resolves declared requirements to a hashed lock for one architecture."""

    @property
    def identity(self) -> str: ...

    def __call__(
        self, requirements: Sequence[str], *, arch: str, exclude_newer: str
    ) -> Resolution: ...


# ------------------------------------------------------------------ lock text
def pylock_packages(lock_text: str) -> list[tuple[str, str]]:
    """``(canonical name, version)`` of every package in a lock, in lock order."""
    document = tomllib.loads(lock_text)
    return [
        (canonicalize_name(str(package["name"])), str(package["version"]))
        for package in document.get("packages", [])
    ]


_SOURCE_KEYS = ("vcs", "directory", "archive", "sdist")


def lock_sources_ok(lock_text: str, prefixes: Sequence[str]) -> bool:
    """Require every package to have only index wheels from allowed hosts."""
    for package in tomllib.loads(lock_text).get("packages", []):
        wheels = package.get("wheels")
        if not wheels or any(key in package for key in _SOURCE_KEYS):
            return False
        if any(not str(wheel.get("url", "")).startswith(tuple(prefixes)) for wheel in wheels):
            return False
    return True


def _closure_bytes(lock_text: str) -> int:
    """Wheel bytes a fetch of the lock downloads: every wheel it lists."""
    return sum(
        int(wheel.get("size", 0))
        for package in tomllib.loads(lock_text).get("packages", [])
        for wheel in package.get("wheels", [])
    )


def provided_by_lock(requirements: Sequence[str], lock_text: str) -> tuple[str, ...]:
    """``name==version`` of each declared requirement the lock resolves, sorted."""
    versions = dict(pylock_packages(lock_text))
    found: set[str] = set()
    for text in requirements:
        try:
            name = canonicalize_name(Requirement(text).name)
        except InvalidRequirement:
            continue
        if name in versions:
            found.add(f"{name}=={versions[name]}")
    return tuple(sorted(found))


# ---------------------------------------------------------------------- index
@dataclass(frozen=True)
class IndexFile:
    filename: str
    uploaded: datetime | None = None


class IndexClient(Protocol):
    def files(self, name: str) -> list[IndexFile] | None:
        """The project's files; ``None`` when the index has no such project."""
        ...


@dataclass(frozen=True)
class SimpleIndex:
    """PEP 691 JSON view of a simple index (``upload-time`` per file, PEP 700)."""

    url: str = INDEX
    deadline: float | None = None

    def files(self, name: str) -> list[IndexFile] | None:
        request = urllib.request.Request(  # noqa: S310 - fixed https index
            f"{self.url.rstrip('/')}/{canonicalize_name(name)}/",
            headers={"Accept": "application/vnd.pypi.simple.v1+json"},
        )
        try:
            timeout = HTTP_TIMEOUT_S
            if self.deadline is not None:
                timeout = min(timeout, _remaining(self.deadline))
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                document = json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise DependencyInfraError(f"index answered {error.code} for {name}") from error
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            raise DependencyInfraError(f"index unreachable: {error}") from error
        found: list[IndexFile] = []
        for entry in document.get("files", []):
            stamp = entry.get("upload-time")
            uploaded = datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None
            found.append(IndexFile(str(entry.get("filename", "")), uploaded))
        return found


@dataclass(frozen=True)
class DirectoryIndex:
    """A flat ``--find-links`` directory: file names only, no upload times."""

    directory: Path

    def files(self, name: str) -> list[IndexFile] | None:
        wanted = canonicalize_name(name)
        found = [
            IndexFile(path.name)
            for path in sorted(self.directory.iterdir())
            if _file_project(path.name) == wanted
        ]
        return found or None


def _file_project(filename: str) -> str | None:
    try:
        if filename.endswith(".whl"):
            return canonicalize_name(parse_wheel_filename(filename)[0])
        return canonicalize_name(parse_sdist_filename(filename)[0])
    except (InvalidWheelFilename, InvalidSdistFilename):
        return None


def _file_version(filename: str) -> object | None:
    try:
        if filename.endswith(".whl"):
            return parse_wheel_filename(filename)[1]
        return parse_sdist_filename(filename)[1]
    except (InvalidWheelFilename, InvalidSdistFilename):
        return None


def _matching_files(requirement: str, files: list[IndexFile]) -> list[IndexFile]:
    specifier = Requirement(requirement).specifier or SpecifierSet()
    found: list[IndexFile] = []
    for file in files:
        version = _file_version(file.filename)
        if version is not None and specifier.contains(str(version), prereleases=True):
            found.append(file)
    return found


def classify_failure(
    requirement: str, files: list[IndexFile] | None, *, arch: str, cutoff: datetime
) -> str:
    """Why a requirement that failed alone cannot be provided, from the index's file list."""
    matching = _matching_files(requirement, files or [])
    if not matching:
        return "unresolvable"
    before = [file for file in matching if file.uploaded is None or file.uploaded <= cutoff]
    if not before:
        return "after-cutoff"
    if any(file.filename.endswith(".whl") and wheel_fits(file.filename, arch) for file in before):
        return "unresolvable"
    return "no-wheel"


# ------------------------------------------------------------------------- uv
def _locked_uv() -> str:
    try:
        return locked_uv()
    except FileNotFoundError as error:
        raise DependencyInfraError(f"uv is unavailable: {error}") from error


def _uv_environment() -> dict[str, str]:
    return {key: os.environ[key] for key in _PASSED_ENV if key in os.environ}


def _canonical_cutoff(exclude_newer: str) -> datetime:
    return datetime.fromisoformat(exclude_newer.replace("Z", "+00:00")).astimezone(UTC)


@dataclass(frozen=True)
class UvResolver:
    """``Resolver`` over Raidar's locked ``uv``.

    ``compile_args`` is appended to the ``uv pip compile`` command (tests point it at a
    local ``--no-index --find-links`` directory); the policy flags are always present.
    """

    uv_bin: str = field(default_factory=_locked_uv)
    timeout_s: float = TIMEOUT_S
    timeout_retries: int = TIMEOUT_RETRIES
    """Extra identical attempts after a timed-out resolution; the production bound is one, so
    at most two attempts of ``timeout_s`` each are made."""
    max_packages: int = MAX_PACKAGES
    max_bytes: int = MAX_WHEEL_BYTES
    compile_args: tuple[str, ...] = ()
    canary: str = CANARY_REQUIREMENT
    index: IndexClient | None = None
    index_url: str = INDEX
    wheel_url_prefixes: tuple[str, ...] | None = None
    _version: list[str] = field(default_factory=list, compare=False, repr=False)

    @property
    def identity(self) -> str:
        if not self._version:
            try:
                done = subprocess.run(  # noqa: S603
                    [self.uv_bin, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=_uv_environment(),
                    check=True,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise DependencyInfraError(f"uv is unavailable: {error}") from error
            parts = done.stdout.split()
            if len(parts) < 2 or parts[0] != "uv":
                raise DependencyInfraError("uv returned an invalid version")
            try:
                Version(parts[1])
            except InvalidVersion as error:
                raise DependencyInfraError("uv returned an invalid version") from error
            self._version.append(parts[1])
        return f"uv@{self._version[0]}"

    def _index_client(self, deadline: float) -> IndexClient:
        if self.index is not None:
            return self.index
        if "--no-index" in self.compile_args and "--find-links" in self.compile_args:
            at = self.compile_args.index("--find-links") + 1
            return DirectoryIndex(Path(self.compile_args[at]))
        return SimpleIndex(self.index_url, deadline=deadline)

    def _compile(
        self, requirements: Sequence[str], arch: str, exclude_newer: str, deadline: float
    ) -> tuple[int, str, str]:
        remaining = _remaining(deadline)
        with tempfile.TemporaryDirectory(prefix="raidar-resolve-") as tmp:
            source = Path(tmp) / "requirements.in"
            source.write_text("\n".join(requirements) + "\n")
            argv = [
                self.uv_bin,
                "pip",
                "compile",
                str(source),
                "--format",
                "pylock.toml",
                "--generate-hashes",
                "--only-binary",
                ":all:",
                "--no-config",
                "--no-cache",
                "--no-header",
                "--python-version",
                PYTHON,
                "--python-platform",
                PLATFORMS[arch],
                "--exclude-newer",
                exclude_newer,
                "--index-url",
                self.index_url,
                *self.compile_args,
            ]
            try:
                done = subprocess.run(  # noqa: S603
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=remaining,
                    env=_uv_environment(),
                    cwd=tmp,
                )
            except subprocess.TimeoutExpired as error:
                raise _Exhausted from error
            except OSError as error:
                raise DependencyInfraError(f"uv is unavailable: {error}") from error
        return done.returncode, done.stdout, done.stderr

    def _canary_works(self, arch: str, exclude_newer: str, deadline: float) -> bool:
        """Check index availability within the resolution's remaining total budget."""
        try:
            code, _, _ = self._compile(
                [self.canary],
                arch,
                exclude_newer,
                min(deadline, time.monotonic() + CANARY_TIMEOUT_S),
            )
        except _Exhausted:
            return False
        return code == 0

    def _infra(self, arch: str, exclude_newer: str, stderr: str, deadline: float) -> None:
        lowered = stderr.lower()
        if any(sign in lowered for sign in _NETWORK_SIGNS) or not self._canary_works(
            arch, exclude_newer, deadline
        ):
            raise DependencyInfraError(
                f"dependency resolution unavailable: {stderr.strip()[-400:]}"
            )

    def __call__(self, requirements: Sequence[str], *, arch: str, exclude_newer: str) -> Resolution:
        started = time.perf_counter()
        refused: list[Unprovided] = []
        usable: list[str] = []
        for text in dict.fromkeys(requirements):
            try:
                parsed = Requirement(text)
            except InvalidRequirement:
                refused.append(Unprovided(requirement=text, reason=_refusal_reason(text)))
                continue
            if parsed.url is not None:
                refused.append(Unprovided(requirement=text, reason="direct-reference"))
            else:
                usable.append(text)
        lock, unprovided, attempts = self._resolve_with_retries(usable, arch, exclude_newer)
        elapsed = time.perf_counter() - started
        provided = provided_by_lock(usable, lock) if lock else ()
        return Resolution(
            lock_text=lock,
            provided=provided,
            unprovided=(*refused, *unprovided),
            resolve_s=elapsed,
            packages=len(pylock_packages(lock)) if lock else 0,
            wheel_bytes=_closure_bytes(lock) if lock else 0,
            attempts=attempts,
        )

    def _resolve_with_retries(
        self, usable: list[str], arch: str, exclude_newer: str
    ) -> tuple[str, list[Unprovided], int]:
        """Resolve within the time cap, retrying a timed-out attempt up to ``timeout_retries``.

        A timeout is never a verdict about the packages: when the attempts run out it is a
        ``DependencyInfraError`` that says how many were made. ``cap-exceeded`` stays for the
        deterministic package-count and byte caps only.
        """
        attempts = 0
        while True:
            attempts += 1
            deadline = time.monotonic() + self.timeout_s
            # Reserve availability diagnosis inside, not beyond, the total cap.
            work_deadline = deadline - min(CANARY_TIMEOUT_S, self.timeout_s / 4)
            try:
                lock, unprovided = self._resolve_usable(
                    usable, arch, exclude_newer, work_deadline, deadline
                )
            except _Exhausted:
                if attempts <= self.timeout_retries:
                    continue
                bound = self.timeout_retries
                raise DependencyInfraError(
                    f"dependency resolution timed out in {attempts} attempt(s) of "
                    f"{self.timeout_s:g} s each (retry bound: {bound})"
                ) from None
            return lock, unprovided, attempts

    def _resolve_usable(
        self,
        usable: list[str],
        arch: str,
        exclude_newer: str,
        deadline: float,
        total_deadline: float,
    ) -> tuple[str, list[Unprovided]]:
        """Bound package resolution and its availability diagnosis by one total deadline.

        Raises ``_Exhausted`` when the deadline passes: the caller retries or fails as infra.
        """
        if not usable:
            return "", []
        code, stdout, stderr = self._compile(usable, arch, exclude_newer, deadline)
        if code == 0:
            return self._within_caps(stdout, usable)
        self._infra(arch, exclude_newer, stderr, total_deadline)
        return self._diagnose(usable, arch, exclude_newer, deadline, total_deadline)

    def _prefixes(self) -> tuple[str, ...]:
        if self.wheel_url_prefixes is not None:
            return self.wheel_url_prefixes
        return ("file://",) if "--no-index" in self.compile_args else WHEEL_URL_PREFIXES

    def _within_caps(self, lock: str, usable: list[str]) -> tuple[str, list[Unprovided]]:
        """The lock if it is within the caps and index-sourced wheels only, else why not."""
        try:
            packages = len(pylock_packages(lock))
            wheel_bytes = _closure_bytes(lock)
            sources_ok = lock_sources_ok(lock, self._prefixes())
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise DependencyInfraError("uv returned an unreadable lock") from error
        if not sources_ok:
            # A transitive direct reference, vcs, path or sdist entry: nothing is fetched from it.
            return "", [Unprovided(requirement=text, reason="direct-reference") for text in usable]
        if packages > self.max_packages or wheel_bytes > self.max_bytes:
            return "", [Unprovided(requirement=text, reason="cap-exceeded") for text in usable]
        return lock, []

    def _diagnose(
        self,
        usable: list[str],
        arch: str,
        exclude_newer: str,
        deadline: float,
        total_deadline: float,
    ) -> tuple[str, list[Unprovided]]:
        cutoff = _canonical_cutoff(exclude_newer)
        index = self._index_client(deadline)
        failed: list[Unprovided] = []
        survivors: list[str] = []
        for text in usable:
            code, _, stderr = self._compile([text], arch, exclude_newer, deadline)
            if code == 0:
                survivors.append(text)
                continue
            self._infra(arch, exclude_newer, stderr, total_deadline)
            if time.monotonic() >= deadline:
                raise _Exhausted
            reason = classify_failure(
                text, index.files(Requirement(text).name), arch=arch, cutoff=cutoff
            )
            failed.append(Unprovided(requirement=text, reason=reason))
        if survivors:
            code, stdout, stderr = self._compile(survivors, arch, exclude_newer, deadline)
            if code == 0:
                lock, over = self._within_caps(stdout, survivors)
                return lock, [*failed, *over]
            self._infra(arch, exclude_newer, stderr, total_deadline)
            failed.extend(Unprovided(requirement=text, reason="unresolvable") for text in survivors)
        return "", failed


# ---------------------------------------------------------------------- pins
def _normalised(requirements: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({text.strip() for text in requirements if text.strip()}))


def _resolve_both(
    resolver: Resolver, key: str, requirements: tuple[str, ...], exclude_newer: str
) -> tuple[dict[str, Resolution], list[Unprovided]]:
    """One set resolved for every architecture, and why it cannot be provided (if not)."""
    outcomes = {
        arch: resolver(requirements, arch=arch, exclude_newer=exclude_newer) for arch in ARCHES
    }
    problems = [item for outcome in outcomes.values() for item in outcome.unprovided]
    if problems:
        return outcomes, problems
    versions = {tuple(sorted(pylock_packages(o.lock_text))) for o in outcomes.values()}
    if len(versions) != 1:
        return outcomes, [Unprovided(requirement=f"set {key[:12]}", reason="arch-mismatch")]
    return outcomes, []


def _resolve_sets(
    wanted: Mapping[str, tuple[str, ...]], chosen: Resolver, cutoff: str
) -> tuple[dict[str, DependencySet], dict[LockKey, str]]:
    sets: dict[str, DependencySet] = {}
    locks: dict[LockKey, str] = {}
    problems: list[Unprovided] = []
    for key, requirements in sorted(wanted.items()):
        outcomes, failed = _resolve_both(chosen, key, requirements, cutoff)
        problems.extend(failed)
        if failed:
            continue
        locks.update({(key, arch): outcome.lock_text for arch, outcome in outcomes.items()})
        sets[key] = DependencySet(
            requirements=requirements,
            **{arch: lock_sha256(outcomes[arch].lock_text) for arch in ARCHES},
        )
    if problems:
        unique = tuple(dict.fromkeys(problems))
        raise DependencyUnresolvable(
            "; ".join(f"{item.requirement}: {item.reason}" for item in unique), unique
        )
    return sets, locks


def resolve_pins(
    reference_sets: Mapping[str, Sequence[str]],
    test_dependencies: Sequence[str],
    *,
    exclude_newer: str,
    resolver: Resolver | None = None,
) -> tuple[DependencyPins, dict[LockKey, str]]:
    """Resolve every set a problem needs for both architectures.

    Sets: each reference step's declared set, ``seed`` (the test dependencies alone;
    the agent's) and ``grader`` (``GRADER_TOOLS`` plus the test dependencies). Empty sets
    need no lock and are omitted. Raises ``DependencyUnresolvable`` when any requirement
    is unprovided or the architectures resolve to different versions.
    """
    chosen = resolver or UvResolver()
    steps = {step: _normalised(items) for step, items in reference_sets.items()}
    seed = _normalised(test_dependencies)
    grader = _normalised((*GRADER_TOOLS, *test_dependencies))
    wanted = {set_sha256(items): items for items in (*steps.values(), seed, grader) if items}
    sets, locks = _resolve_sets(wanted, chosen, exclude_newer)
    return (
        DependencyPins(
            policy=POLICY,
            resolver=chosen.identity,
            index=INDEX,
            python=PYTHON,
            platforms=tuple(PLATFORMS[arch] for arch in ARCHES),
            exclude_newer=exclude_newer,
            sets=sets,
            steps={step: set_sha256(items) for step, items in steps.items() if items},
            seed=set_sha256(seed) if seed else None,
            grader=set_sha256(grader),
        ),
        locks,
    )
