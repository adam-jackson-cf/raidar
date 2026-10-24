"""Shared helpers for built-in component definitions."""

from __future__ import annotations

import hashlib
from collections.abc import Collection
from pathlib import Path

from raidar.design.experiments.canonical import IGNORED_TREE_NAMES
from raidar.subjects.agents.config import Harness
from raidar.subjects.treatments.definition.manifest import ComponentConfigError, ComponentUseContext

BOTH_HARNESSES = (Harness.CODEX_CLI, Harness.CLAUDE_CODE)
# The SDK files every built-in's behaviour depends on: the contract, the merge
# and compile rules, the generated setup.sh (reset + overlay evidence) and the
# probe evidence readers. Changing any of them changes every component digest.
COMMON_FINGERPRINT = (
    "raidar/subjects/treatments/definition/manifest.py",
    "raidar/subjects/treatments/definition/contribution.py",
    "raidar/subjects/treatments/definition/evidence.py",
    "raidar/subjects/treatments/definition/compiler.py",
    "raidar/subjects/treatments/definition/compiled.py",
    "raidar/subjects/treatments/runtime/setup_script.py",
    "raidar/subjects/treatments/runtime/step_reset.sh",
    "raidar/subjects/treatments/catalog/_common.py",
)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def require_with_keys(
    use: ComponentUseContext, *, allowed: Collection[str], required: Collection[str] = ()
) -> None:
    unknown = sorted(set(use.with_) - set(allowed))
    if unknown:
        raise ComponentConfigError(f"unknown 'with' keys {unknown}; allowed: {sorted(allowed)}")
    missing = sorted(set(required) - set(use.with_))
    if missing:
        raise ComponentConfigError(f"missing required 'with' keys {missing}")


def require_file_count(use: ComponentUseContext, *, minimum: int, maximum: int | None) -> None:
    count = len(use.files)
    if count < minimum or (maximum is not None and count > maximum):
        expected = str(minimum) if minimum == maximum else f"{minimum}..{maximum or 'n'}"
        raise ComponentConfigError(f"expects {expected} file(s), got {count}")


def read_file(use: ComponentUseContext, relative: str) -> bytes:
    path = use.file_path(relative)
    if not path.is_file():
        raise ComponentConfigError(f"{relative!r} must be a file")
    return path.read_bytes()


def tree_files(root: Path) -> list[Path]:
    """Files a component ships from ``root``: exactly the files the lock hashes."""
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and not IGNORED_TREE_NAMES.intersection(path.relative_to(root).parts)
    )


def text_lines(text: str) -> list[str]:
    """Non-empty lines, trimmed: the evidence that a whole text reached a context."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def missing_lines(text: str, context: str) -> list[str]:
    return [line for line in text_lines(text) if line not in context]
