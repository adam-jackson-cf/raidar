"""A refused suppression comment's second finding, located per gate run.

The gate refuses every lint or type suppression comment (``SUPPRESS``, a
bespoke check). An unused ``# type: ignore`` is also reported by mypy, as
``MYPY.unused-ignore`` at the same line, so one comment is two blocking
findings (``smoke-3b-3`` r2 cp8: ``SUPPRESS`` and ``MYPY.unused-ignore`` at
``forge.py:359``). ``duplicates`` counts, for one gate run, the block-tier
findings with a code in ``FOLDED_CODES`` at a ``(path, line)`` that also has a
block-tier ``SUPPRESS``: the findings the ``*_net`` counts fold into their
``SUPPRESS``.

Locations come from the run's report (``python-guardrails.gate-report/1``,
every finding with its path and line) or else its output log (the listed
``path:line:col: CODE`` lines of the ``Blocking`` section). A run with no folded
code needs no location (0); a run whose locations the stored files do not give
in full is unknown (None). Nothing is written.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from raidar.scoring.gate_effort.records import (
    BLOCK_TIER,
    RECORD_PATH,
    _inside,
    _reports,
)

SUPPRESS_CODE = "SUPPRESS"
FOLDED_CODES = frozenset({"MYPY.unused-ignore"})
"""Codes that restate a refused suppression comment at its own line."""

_SECTION_RE = re.compile(r"^(?P<title>Blocking|Warnings)\b.*\((?P<count>\d+)\):\s*$")
_LOCATED_RE = re.compile(
    r"^\s+(?P<path>\S.*?):(?P<line>\d+):(?:\d+:)? (?P<code>[A-Za-z][A-Za-z0-9._-]*) "
)

Location = tuple[str, str, int]
"""(code, path, line) of one blocking finding."""


def _report_locations(trace_dir: Path, report_dir: Any) -> list[Location] | None:
    folder = _inside(trace_dir, report_dir)
    reports = _reports(folder) if folder is not None and folder.is_dir() else []
    if len(reports) != 1 or not isinstance(reports[0].get("findings"), list):
        return None
    found: list[Location] = []
    for item in reports[0]["findings"]:
        if not isinstance(item, dict) or item.get("tier") != BLOCK_TIER:
            continue
        line = item.get("line")
        if not isinstance(line, int):
            return None
        found.append((str(item.get("code")), str(item.get("relpath")), line))
    return found


def _log_locations(trace_dir: Path, log: Any) -> list[Location] | None:
    path = _inside(trace_dir, log)
    if path is None or not path.is_file():
        return None
    section: str | None = None
    found: list[Location] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if heading := _SECTION_RE.match(line):
            section = heading["title"]
        elif section == "Blocking" and (finding := _LOCATED_RE.match(line)):
            found.append((finding["code"], finding["path"], int(finding["line"])))
        elif not line.startswith(" "):
            section = None
    return found or None


def _no_pair(codes: Sequence[str] | None) -> bool:
    """The listed codes hold no folded code or no ``SUPPRESS``: nothing to fold."""
    if codes is None:
        return False
    return SUPPRESS_CODE not in codes or not FOLDED_CODES.intersection(codes)


def _locations(trace_dir: Path, run: dict[str, Any], complete: bool) -> list[Location] | None:
    located = _report_locations(trace_dir, run.get("report_dir"))
    if located is None and complete:
        # The log lists every blocking finding only when its codes are complete.
        located = _log_locations(trace_dir, run.get("log"))
    return located


def duplicates(trace_dir: Path, run: dict[str, Any], codes: Sequence[str] | None) -> int | None:
    """Folded findings of one gate run (None: its codes or locations are not stored)."""
    if _no_pair(codes):
        return 0
    located = _locations(trace_dir, run, codes is not None)
    if located is None:
        return None
    suppressed = {(path, line) for code, path, line in located if code == SUPPRESS_CODE}
    return sum(
        1 for code, path, line in located if code in FOLDED_CODES and (path, line) in suppressed
    )


def raw_runs(trace_dir: Path | None) -> list[dict[str, Any]]:
    """The step's gate runs as recorded (in the order ``read_step_gate`` reads them)."""
    if trace_dir is None:
        return []
    try:
        payload = json.loads((trace_dir / RECORD_PATH).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    runs = payload.get("gate_runs") if isinstance(payload, dict) else None
    return [run for run in runs if isinstance(run, dict)] if isinstance(runs, list) else []


__all__ = ["FOLDED_CODES", "SUPPRESS_CODE", "duplicates", "raw_runs"]
