"""Builders for stage-package tests: real files in a temp tree, never a mock."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml

NAMESPACE = "acme"
STAGE_FIXTURES = Path(__file__).parent / "fixtures" / "stages" / NAMESPACE

BASE_MANIFEST: dict[str, Any] = {"contract": "stage/1", "name": "demo", "summary": "A demo."}


def fixture_package(name: str, destination: Path) -> Path:
    """A mutable copy of fixture package ``name`` at ``destination/acme/name``."""
    target = destination / NAMESPACE / name
    shutil.copytree(STAGE_FIXTURES / name, target)
    return target


def write_package(
    parent: Path,
    manifest: dict[str, Any] | None = None,
    files: dict[str, str | bytes] | None = None,
    *,
    prompt: str | None = "Prompt.\n",
) -> Path:
    """A package folder named by its manifest, holding ``stage.yaml``, a prompt and ``files``."""
    data = {**BASE_MANIFEST, **(manifest or {})}
    root = parent / str(data["name"])
    root.mkdir(parents=True)
    (root / "stage.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    if prompt is not None:
        (root / "prompt.md").write_text(prompt, encoding="utf-8")
    for relative, content in (files or {}).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
    return root
