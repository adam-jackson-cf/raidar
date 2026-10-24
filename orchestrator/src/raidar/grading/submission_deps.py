"""Third-party packages a submission declares, provided offline to the graded program.

SCB's native runner grades with network: its Python environment
(``configs/environments/docker-python3.12-uv.yaml`` in the SCB fork) runs
``uv add -r requirements.txt`` and then ``uv run <entry>``, which also syncs
whatever the submission's ``pyproject.toml`` declares. The Raidar grader runs
with ``--network none`` and ``UV_OFFLINE``/``UV_NO_SYNC``, so a declared package
such as PyYAML was missing and every case failed (fault catalog FCR-007).

Raidar keeps grading offline. The grader image carries a pinned copy of each
package the problem's own SCB configuration names as a dependency
(``test_dependencies``, the packages SCB locks into the problem's evaluator
environment), one ``--target`` directory per package. When a snapshot declares
one of them (in ``pyproject.toml`` ``[project].dependencies`` or
``requirements.txt``) with a specifier the pin satisfies, that directory joins
the graded program's ``PYTHONPATH``. A package the snapshot does not declare
stays unavailable, as it is under native grading; a declared package Raidar has
no pin for, or whose specifier the pin does not satisfy, is recorded as
``unprovided`` (the remaining fidelity gap), never silently substituted.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from raidar.grading.models import VerifierModel

# Exact pins, keyed by canonical project name. Raise a pin only with a regrade check.
SUBMISSION_DEPENDENCY_PINS: Mapping[str, str] = {"pyyaml": "PyYAML==6.0.3"}
SUBMISSION_DEPS_ROOT = "/opt/raidar-submission-deps"
_DECLARATION_FILES = ("pyproject.toml", "requirements.txt")


class SubmissionDependencies(VerifierModel):
    """What a snapshot declared and which pinned packages the graded program saw."""

    declared: tuple[str, ...] = ()
    provided: tuple[str, ...] = ()
    unprovided: tuple[str, ...] = ()


def provisioned_pins(test_dependencies: Iterable[str]) -> dict[str, str]:
    """``{canonical name: exact pin}`` for the problem's dependencies Raidar pins."""
    pins: dict[str, str] = {}
    for dependency in test_dependencies:
        try:
            name = canonicalize_name(Requirement(dependency).name)
        except InvalidRequirement:
            continue
        if name in SUBMISSION_DEPENDENCY_PINS:
            pins[name] = SUBMISSION_DEPENDENCY_PINS[name]
    return dict(sorted(pins.items()))


def install_dir(name: str) -> str:
    return f"{SUBMISSION_DEPS_ROOT}/{canonicalize_name(name)}"


def dockerfile_layer(pins: Mapping[str, str], python: str) -> str:
    """One ``RUN`` installing each pin into its own directory (empty when there are none)."""
    if not pins:
        return ""
    installs = " \\\n && ".join(
        f'uv pip install --no-cache --python {python} --target {install_dir(name)} "{pin}"'
        for name, pin in pins.items()
    )
    return f"RUN {installs} \\\n && chmod -R a+rX {SUBMISSION_DEPS_ROOT}\n"


def _pyproject_requirements(path: Path) -> list[str]:
    try:
        document = tomllib.loads(path.read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return []
    project = document.get("project")
    dependencies = project.get("dependencies") if isinstance(project, dict) else None
    if not isinstance(dependencies, list):
        return []
    return [item for item in dependencies if isinstance(item, str)]


def _requirements_txt(path: Path) -> list[str]:
    try:
        lines = path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    stripped = (line.split(" #", 1)[0].strip() for line in lines)
    return [line for line in stripped if line and not line.startswith(("#", "-"))]


def declared_requirements(snapshot: Path) -> tuple[str, ...]:
    """Requirement strings the snapshot declares, in file order, without duplicates."""
    found: list[str] = []
    readers = {"pyproject.toml": _pyproject_requirements, "requirements.txt": _requirements_txt}
    for name in _DECLARATION_FILES:
        path = snapshot / name
        if path.is_file():
            found.extend(readers[name](path))
    return tuple(dict.fromkeys(found))


def _pin_version(pin: str) -> str:
    return pin.split("==", 1)[1]


def resolve_dependencies(snapshot: Path, pins: Mapping[str, str]) -> SubmissionDependencies:
    """Which declared requirements a pinned package satisfies (``provided``) and which not."""
    provided: dict[str, str] = {}
    unprovided: list[str] = []
    declared = declared_requirements(snapshot)
    for text in declared:
        try:
            requirement = Requirement(text)
        except InvalidRequirement:
            unprovided.append(f"{text} (unparseable requirement)")
            continue
        name = canonicalize_name(requirement.name)
        pin = pins.get(name)
        if pin is None:
            unprovided.append(f"{text} (no pinned offline package)")
        elif not requirement.specifier.contains(_pin_version(pin), prereleases=True):
            unprovided.append(f"{text} (pinned {pin} does not satisfy it)")
        else:
            provided[name] = pin
    return SubmissionDependencies(
        declared=declared,
        provided=tuple(sorted(provided.values())),
        unprovided=tuple(unprovided),
    )


def python_path_entries(dependencies: SubmissionDependencies) -> tuple[str, ...]:
    """Directories the graded program's ``PYTHONPATH`` gains, in a stable order."""
    names = sorted(canonicalize_name(pin.split("==", 1)[0]) for pin in dependencies.provided)
    return tuple(install_dir(name) for name in names)
