"""Cached tree-sitter Python parser (fork ``languages/python/parser.py``)."""

from __future__ import annotations

from functools import cache
from typing import Any

from tree_sitter_language_pack import get_parser


@cache
def get_python_parser() -> Any:
    """The process-wide Python parser from the pinned tree-sitter language pack."""
    return get_parser("python")
