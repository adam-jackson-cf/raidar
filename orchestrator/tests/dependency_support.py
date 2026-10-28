"""Local wheels, sdists, locks and fake resolvers for ``declared-wheels/1`` tests."""

from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from raidar.grading.bundle import VerifierBundle, write_bundle_dependencies
from raidar.grading.dependency_pins import Unprovided
from raidar.grading.resolver import Resolution, pylock_packages, resolve_pins
from tests.verifier_support import make_bundle

CUTOFF = "2026-10-01T00:00:00Z"


def make_wheel(
    directory: Path,
    name: str,
    version: str = "1.0",
    *,
    requires: Sequence[str] = (),
    files: Mapping[str, str] | None = None,
) -> Path:
    """A minimal pure-Python wheel (no code runs when it is resolved or installed)."""
    directory.mkdir(parents=True, exist_ok=True)
    module = name.replace("-", "_")
    dist = f"{module}-{version}.dist-info"
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    metadata += "".join(f"Requires-Dist: {item}\n" for item in requires)
    members = {
        f"{module}/__init__.py": "",
        f"{dist}/METADATA": metadata,
        f"{dist}/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        f"{dist}/RECORD": "",
        **(files or {}),
    }
    path = directory / f"{module}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for member, text in members.items():
            archive.writestr(member, text)
    return path


def make_sdist(directory: Path, name: str, version: str = "1.0", *, setup_py: str = "") -> Path:
    """A source distribution whose ``setup.py`` must never run."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}-{version}.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        data = (setup_py or "raise SystemExit('setup.py must never run')\n").encode()
        entry = tarfile.TarInfo(f"{name}-{version}/setup.py")
        entry.size = len(data)
        archive.addfile(entry, io.BytesIO(data))
    return path


def fake_lock(packages: Mapping[str, str], *, wheels: Mapping[str, Path] | None = None) -> str:
    """A pylock text for ``{name: version}``; real wheel files make its urls fetchable."""
    lines = ['lock-version = "1.0"', 'created-by = "uv"', 'requires-python = ">=3.12"', ""]
    for name, version in sorted(packages.items()):
        lines += ["[[packages]]", f'name = "{name}"', f'version = "{version}"']
        wheel = (wheels or {}).get(name)
        if wheel is not None:
            sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
            url, size = wheel.resolve().as_uri(), wheel.stat().st_size
        else:
            sha = hashlib.sha256(name.encode()).hexdigest()
            url, size = f"https://files.example/{name}-{version}-py3-none-any.whl", 10
        lines += [
            f'wheels = [{{ url = "{url}", size = {size}, hashes = {{ sha256 = "{sha}" }} }}]',
            "",
        ]
    return "\n".join(lines)


@dataclass
class FakeResolver:
    """A ``Resolver`` with no uv and no network: one fixed version per requirement name."""

    identity: str = "uv@0.0-test"
    unprovided: dict[str, str] = field(default_factory=dict)
    version: str = "1.0"
    arch_versions: dict[str, str] = field(default_factory=dict)
    wheels: dict[str, Path] = field(default_factory=dict)
    error: Exception | None = None
    calls: list[tuple[tuple[str, ...], str, str]] = field(default_factory=list)

    def __call__(self, requirements: Sequence[str], *, arch: str, exclude_newer: str) -> Resolution:
        self.calls.append((tuple(requirements), arch, exclude_newer))
        if self.error is not None:
            raise self.error
        refused = tuple(
            Unprovided(requirement=text, reason=self.unprovided[text])
            for text in requirements
            if text in self.unprovided
        )
        if refused:
            return Resolution("", (), refused, 0.0)
        version = self.arch_versions.get(arch, self.version)
        names = [canonicalize_name(Requirement(text).name) for text in requirements]
        lock = fake_lock(dict.fromkeys(names, version), wheels=self.wheels)
        provided = tuple(sorted(f"{name}=={version}" for name, version in pylock_packages(lock)))
        return Resolution(lock, provided, (), 0.01, packages=len(names), wheel_bytes=10)


def pinned_bundle(
    root: Path,
    *,
    reference_sets: Mapping[str, Sequence[str]] | None = None,
    test_dependencies: Sequence[str] = (),
    resolver: FakeResolver | None = None,
) -> tuple[VerifierBundle, FakeResolver]:
    """The ``adder`` fixture bundle carrying a ``declared-wheels/1`` pin made by ``resolver``."""
    fake = resolver or FakeResolver()
    bundle = make_bundle(root)
    sets: Mapping[str, Sequence[str]] = reference_sets or {"checkpoint_1": (), "checkpoint_2": ()}
    pins, locks = resolve_pins(sets, test_dependencies, exclude_newer=CUTOFF, resolver=fake)
    carried: Any = write_bundle_dependencies(bundle.root, pins, locks)
    return carried, fake
