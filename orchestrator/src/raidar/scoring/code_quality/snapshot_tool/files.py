"""Per-file quality rows and symbol rows for a snapshot (runs in the tool env).

Port of the file walk in ``slop-code-bench``
``src/slop_code/metrics/driver.py`` (``measure_files`` /
``measure_snapshot_quality``) at ``25069ba``, limited to what V/E read:

- every ``.py`` file under the snapshot, except paths with a part matching the
  fork's exclude patterns (``__pycache__``, virtualenvs, ``.git``, ...);
- a file row exists (``success: true``) only when the fork would have
  measured the file: it decodes as UTF-8 and radon's raw and maintainability
  analysers accept it. A file the fork skips (``UnicodeDecodeError`` or
  ``SyntaxError``) has no row, which makes V/E ineligible
  (``score_evidence_invalid``) when that file is production code;
- symbol rows come from the tree-sitter extractor for each measured file.
  Files are walked in UTF-8 path order. The fork walks ``Path.rglob`` in
  directory-listing order, which varies by filesystem and changes the last
  digits of E's precision-50 mass sum; sorting makes replays identical on any
  machine (the goldens feed the fork its rows in the same file order).

The fork's other file metrics (lint counts, MI value, waste, type checks) feed
no score and are not computed.
"""

from __future__ import annotations

import fnmatch
import tokenize
from pathlib import Path
from typing import Any

import radon.metrics
import radon.raw

from .symbols import get_symbols

EXCLUDE_PATTERNS = frozenset(
    {
        "__pycache__",
        "*.pyc",
        "venv",
        ".venv",
        "virtualenv",
        ".virtualenv",
        ".git",
        "node_modules",
        ".tox",
        ".nox",
    }
)
_SKIPPED = (UnicodeDecodeError, SyntaxError, tokenize.TokenError)


def _excluded(relative: Path) -> bool:
    return any(
        fnmatch.fnmatch(part, pattern) for pattern in EXCLUDE_PATTERNS for part in relative.parts
    )


def _key(path: Path, snapshot: Path) -> bytes:
    return path.relative_to(snapshot).as_posix().encode("utf-8")


def _measured(path: Path) -> list[dict[str, Any]] | None:
    """Symbol rows when the fork would measure the file, else None."""
    try:
        code = path.read_text(encoding="utf-8")
        radon.raw.analyze(code)
        radon.metrics.mi_visit(code, multi=False)
        return get_symbols(path)
    except _SKIPPED:
        return None


def measure_snapshot(snapshot: Path) -> dict[str, list[dict[str, Any]]]:
    """``files`` rows and ``symbols`` rows (with ``file_path``) for every measured file."""
    file_rows: list[dict[str, Any]] = []
    symbol_rows: list[dict[str, Any]] = []
    # Sorted, not the fork's raw ``rglob`` order: E's mass is summed in row order at
    # Decimal precision 50, and directory listing order differs between filesystems.
    for path in sorted(snapshot.rglob("*"), key=lambda item: _key(item, snapshot)):
        relative = path.relative_to(snapshot)
        if path.is_dir() or path.suffix != ".py" or _excluded(relative):
            continue
        symbols = _measured(path)
        if symbols is None:
            continue
        file_path = relative.as_posix()
        file_rows.append({"file_path": file_path, "success": True})
        symbol_rows.extend({**row, "file_path": file_path} for row in symbols)
    return {"files": file_rows, "symbols": symbol_rows}
