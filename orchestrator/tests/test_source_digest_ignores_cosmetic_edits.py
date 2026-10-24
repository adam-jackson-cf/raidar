"""Source digests change with the code, never with its docstrings, comments or layout."""

from __future__ import annotations

from pathlib import Path

import pytest

from raidar.contract.source_digest import python_source_digest

BASE = """from dataclasses import dataclass


@dataclass
class Weights:
    limit: int = 3

    def scale(self, value: float) -> float:
        return value * self.limit


def weight(x):
    if x:
        return 1
    return 2
"""

COSMETIC = {
    "docstrings": BASE.replace(
        "class Weights:\n", 'class Weights:\n    """Scoring weights."""\n\n'
    ).replace("def weight(x):\n", "def weight(x):\n    '''Pick a weight.'''\n"),
    "module docstring": '"""Weights for the kernel."""\n\n' + BASE,
    "comments": BASE.replace("return 1", "return 1  # truthy input"),
    "blank lines and wrapping": BASE.replace(
        "return value * self.limit",
        "return (\n            value\n            * self.limit\n        )",
    ).replace("\n\n\ndef weight", "\n\n\n\ndef weight"),
    "string-only statement in a body": BASE.replace(
        "    return 2\n", '    "fallthrough"\n    return 2\n'
    ),
}

SEMANTIC = {
    "literal": BASE.replace("return 2", "return 3"),
    "name": BASE.replace("limit: int = 3", "cap: int = 3").replace("self.limit", "self.cap"),
    "annotation": BASE.replace("limit: int = 3", "limit: float = 3"),
    "decorator": BASE.replace("@dataclass\n", "@dataclass(frozen=True)\n"),
    "import": "import os\n" + BASE,
    "string value that is used": BASE.replace("return 2", 'return "2"'),
    "branch": BASE.replace("    if x:\n        return 1\n", "    if not x:\n        return 1\n"),
}


def _digest(tmp_path: Path, name: str, source: str) -> str:
    path = tmp_path / f"{name.replace(' ', '_')}.py"
    path.write_text(source, encoding="utf-8")
    return python_source_digest(path)


@pytest.mark.parametrize("edit", sorted(COSMETIC))
def test_cosmetic_edits_keep_the_digest(tmp_path: Path, edit: str) -> None:
    assert _digest(tmp_path, edit, COSMETIC[edit]) == _digest(tmp_path, "base", BASE)


@pytest.mark.parametrize("edit", sorted(SEMANTIC))
def test_code_edits_change_the_digest(tmp_path: Path, edit: str) -> None:
    assert _digest(tmp_path, edit, SEMANTIC[edit]) != _digest(tmp_path, "base", BASE)


def test_a_docstring_only_body_hashes_like_pass(tmp_path: Path) -> None:
    documented = _digest(tmp_path, "doc", 'def hook():\n    """Nothing to do."""\n')
    assert documented == _digest(tmp_path, "pass", "def hook():\n    pass\n")
    assert documented != _digest(tmp_path, "ellipsis", "def hook():\n    ...\n")
