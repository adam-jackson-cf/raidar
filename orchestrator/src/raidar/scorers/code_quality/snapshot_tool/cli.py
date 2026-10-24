"""Command entry for the tool-env measurement program.

``evaluate`` reads ``{"files": [{"path", "bytes"}]}`` (hex bytes) on stdin and
prints the evaluator payload; a file the interpreter cannot tokenize or parse
prints ``{"error": ...}`` and exits 2, as the fork's evaluator program does.
``measure <snapshot> [<entry_file>]`` prints file rows, symbol rows and the
entrypoint-bounded call graph (``null`` without an entry file). Output is
canonical JSON (sorted keys, compact separators).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from .evaluator import EvaluatorInputError, evaluate
from .files import measure_snapshot
from .graph import build_dependency_graph, dependency_graph_traversal

EXIT_INPUT_INVALID = 2
EXIT_USAGE = 64


def _dump(payload: Any, out: TextIO) -> None:
    out.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def measure(snapshot: Path, entry_file: str | None) -> dict[str, Any]:
    measured: dict[str, Any] = dict(measure_snapshot(snapshot))
    measured["graph"] = (
        dependency_graph_traversal(build_dependency_graph(snapshot, Path(entry_file)))
        if entry_file
        else None
    )
    return measured


def main(argv: Sequence[str], stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    if argv[:1] == ["evaluate"] and len(argv) == 1:
        try:
            payload = evaluate(json.loads(stdin.read())["files"])
        except EvaluatorInputError as error:
            _dump({"error": str(error)}, stdout)
            return EXIT_INPUT_INVALID
        _dump(payload, stdout)
        return 0
    if argv[:1] == ["measure"] and len(argv) in (2, 3):
        _dump(measure(Path(argv[1]), argv[2] if len(argv) == 3 else None), stdout)
        return 0
    sys.stderr.write("usage: evaluate | measure <snapshot> [<entry_file>]\n")
    return EXIT_USAGE
