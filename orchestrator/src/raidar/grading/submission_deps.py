"""Declared requirements of a snapshot and what the graded program was given of them.

Policy ``declared-wheels/1`` (``split-design.md`` §3.2 item 8): the packages a
snapshot declares (``pyproject.toml`` ``[project].dependencies`` and
``requirements.txt``) are resolved by uv, wheels only, for Python 3.12 on the
grader's Linux architecture, from ``pypi.org`` as of the problem's cutoff
(``raidar.grading.resolver``). The hash-checked closure is installed in a grader
image layer (``raidar.grading.closure``) and the graded program runs offline
with it on ``PYTHONPATH``. A requirement the policy cannot provide is recorded
as ``unprovided`` with a reason, never silently substituted.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from raidar.grading.dependency_pins import Unprovided
from raidar.grading.models import VerifierModel

SUBMISSION_DEPS_ROOT = "/opt/raidar-submission-deps"
_DECLARATION_FILES = ("pyproject.toml", "requirements.txt")
_CLOSURE_DIR_CHARS = 12


class SubmissionDependencies(VerifierModel):
    """What a snapshot declared and which closure the graded program saw."""

    declared: tuple[str, ...] = ()
    set_sha256: str = ""
    source: Literal["pin", "resolved"] = "resolved"
    lock_text: str = ""
    lock_sha256: str = ""
    arch: str = ""
    provided: tuple[str, ...] = ()
    unprovided: tuple[Unprovided, ...] = ()
    resolve_s: float = 0.0
    build_s: float = 0.0
    image_id: str = ""
    arch_divergent: bool = False
    """The other architecture resolved to different versions (no pooling across architectures)."""


def closure_dir(lock_sha: str) -> str:
    """Where the closure of one lock is installed inside the grader image."""
    return f"{SUBMISSION_DEPS_ROOT}/{lock_sha[:_CLOSURE_DIR_CHARS]}"


def unreadable(name: str) -> str:
    """The declared-set entry standing for a declaration file Raidar cannot read (an unparseable
    requirement, so health sees it instead of an empty set)."""
    return f"{name} (unreadable declaration)"


def _pyproject_requirements(path: Path) -> list[str]:
    try:
        document = tomllib.loads(path.read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return [unreadable(path.name)]
    project = document.get("project")
    dependencies = project.get("dependencies") if isinstance(project, dict) else None
    if dependencies is None:
        return []
    if not isinstance(dependencies, list):
        return [unreadable(path.name)]
    return [item if isinstance(item, str) else unreadable(path.name) for item in dependencies]


# Options that choose where packages come from; the policy fixes the index, so they are dropped.
_INDEX_OPTIONS = frozenset(
    {
        "-i",
        "--index-url",
        "--extra-index-url",
        "--no-index",
        "-f",
        "--find-links",
        "--trusted-host",
        "--pre",
        "--prefer-binary",
        "--no-binary",
        "--only-binary",
        "--use-feature",
    }
)


def _is_index_option(line: str) -> bool:
    return line.startswith("-") and line.replace("=", " ").split()[0] in _INDEX_OPTIONS


def _requirements_txt(path: Path) -> list[str]:
    """Requirement lines; ``-r``/``-c``/``-e`` and other directives stay as unsupported lines."""
    try:
        lines = path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return [unreadable(path.name)]
    stripped = (line.split(" #", 1)[0].strip() for line in lines)
    return [
        line
        for line in stripped
        if line and not line.startswith("#") and not _is_index_option(line)
    ]


def declared_requirements(snapshot: Path) -> tuple[str, ...]:
    """Requirement strings the snapshot declares, in file order, without duplicates."""
    found: list[str] = []
    readers = {"pyproject.toml": _pyproject_requirements, "requirements.txt": _requirements_txt}
    for name in _DECLARATION_FILES:
        path = snapshot / name
        if path.is_file():
            found.extend(readers[name](path))
    return tuple(dict.fromkeys(found))


def python_path_entries(dependencies: SubmissionDependencies) -> tuple[str, ...]:
    """Directories the graded program's ``PYTHONPATH`` gains (the closure, when one exists)."""
    return (closure_dir(dependencies.lock_sha256),) if dependencies.lock_text else ()


__all__ = [
    "SUBMISSION_DEPS_ROOT",
    "SubmissionDependencies",
    "Unprovided",
    "closure_dir",
    "declared_requirements",
    "python_path_entries",
    "unreadable",
]
