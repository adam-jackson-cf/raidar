#!/usr/bin/env python3
"""Compare identified Ruff/mypy findings with a finding-specific, shrink-only baseline."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]


class ScanInputError(ValueError):
    """A report or baseline cannot establish a valid finding comparison."""


def fingerprint(code: str, symbol: str, source_line: str) -> str:
    content = json.dumps([code, symbol, " ".join(source_line.split())], ensure_ascii=False)
    return hashlib.sha256(content.encode()).hexdigest()


def _path(filename: str, root: str) -> str:
    if not isinstance(filename, str) or not filename:
        raise ScanInputError("diagnostic requires a filename")
    path = Path(filename)
    if ".." in path.parts:
        raise ScanInputError(f"unsafe diagnostic path: {filename}")
    if path.is_absolute():
        try:
            path = path.relative_to(Path(root).resolve())
        except ValueError as exc:
            raise ScanInputError(f"diagnostic outside root: {filename}") from exc
    if not path.parts or path.as_posix() == ".":
        raise ScanInputError("diagnostic requires a file path")
    return path.as_posix()


def _identified(path: str, code: str, row: int, sources: Mapping[str, str]) -> tuple[str, str, str]:
    if not isinstance(code, str) or not code.strip():
        raise ScanInputError(f"{path}: diagnostic requires a rule code")
    if type(row) is not int or row < 1:
        raise ScanInputError(f"{path}: invalid source row")
    try:
        source = sources[path]
        lines = source.splitlines()
        if row > len(lines):
            raise ScanInputError(f"{path}:{row}: row outside source")
        tree = ast.parse(source, filename=path)
    except (KeyError, SyntaxError, TypeError) as exc:
        raise ScanInputError(f"{path}: missing or invalid Python source: {exc}") from exc

    def enclosing(node: ast.AST, parents: tuple[str, ...]) -> tuple[str, ...]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = min([child.lineno, *[d.lineno for d in child.decorator_list]])
                if start <= row <= (child.end_lineno or child.lineno):
                    return enclosing(child, (*parents, child.name))
            else:
                found = enclosing(child, parents)
                if found != parents:
                    return found
        return parents

    return path, code, fingerprint(code, ".".join(enclosing(tree, ())), lines[row - 1])


def _findings(counts: Counter) -> list[dict]:
    return [
        {"path": path, "code": code, "fingerprint": mark, "count": count}
        for (path, code, mark), count in sorted(counts.items())
    ]


def parse_ruff(text: str, *, root: str, sources: Mapping[str, str]) -> list[dict]:
    try:
        diagnostics = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ScanInputError("missing, empty or malformed Ruff JSON scan") from exc
    if not isinstance(diagnostics, list):
        raise ScanInputError("Ruff scan must be a diagnostic list")
    counts: Counter = Counter()
    for diagnostic in diagnostics:
        try:
            path = _path(diagnostic["filename"], root)
            location = diagnostic["location"]
            if type(location["column"]) is not int or location["column"] < 1:
                raise ScanInputError(f"{path}: invalid source column")
            if not isinstance(diagnostic["message"], str):
                raise ScanInputError(f"{path}: invalid diagnostic message")
            counts[_identified(path, diagnostic["code"], location["row"], sources)] += 1
        except (KeyError, TypeError) as exc:
            raise ScanInputError("malformed Ruff diagnostic") from exc
    return _findings(counts)


_MYPY_ERROR = re.compile(r"^(.+?):(\d+)(?::(\d+))?: error: .+ \[([\w-]+)\]$")
_MYPY_NOTE = re.compile(r"^.+?:\d+(?::\d+)?: note: .+$")
_MYPY_SUCCESS = re.compile(r"^Success: no issues found in \d+ source files?$")
_MYPY_SUMMARY = re.compile(r"^Found (\d+) errors? in \d+ files? \(checked \d+ source files?\)$")


def parse_mypy(text: str, *, root: str, sources: Mapping[str, str]) -> list[dict]:
    if not isinstance(text, str) or not text.strip():
        raise ScanInputError("missing or empty mypy scan")
    counts: Counter = Counter()
    success = False
    summary_count = None
    for line in text.splitlines():
        if not line.strip():
            continue
        match = _MYPY_ERROR.fullmatch(line)
        if match:
            filename, row, column, code = match.groups()
            if column is not None and int(column) < 1:
                raise ScanInputError("invalid mypy source column")
            counts[_identified(_path(filename, root), code, int(row), sources)] += 1
        elif _MYPY_NOTE.fullmatch(line):
            continue
        elif _MYPY_SUCCESS.fullmatch(line):
            success = True
        elif summary := _MYPY_SUMMARY.fullmatch(line):
            summary_count = int(summary[1])
        else:
            raise ScanInputError(f"unrecognised mypy scan line: {line}")
    total = sum(counts.values())
    if (not total and not success) or (success and total) or (
        summary_count is not None and summary_count != total
    ):
        raise ScanInputError("incomplete or inconsistent mypy scan")
    return _findings(counts)


def _entries(findings: list[dict]) -> dict[tuple[str, str, str], int]:
    if not isinstance(findings, list):
        raise ScanInputError("findings must be a list")
    entries = {}
    for item in findings:
        if not isinstance(item, dict) or set(item) != {"path", "code", "fingerprint", "count"}:
            raise ScanInputError("invalid finding shape")
        path, code, mark, count = (item[key] for key in ("path", "code", "fingerprint", "count"))
        if (
            any(not isinstance(value, str) or not value.strip() for value in (path, code, mark))
            or PurePosixPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
            or PurePosixPath(path).as_posix() != path
            or "\\" in path
            or type(count) is not int
            or count < 1
        ):
            raise ScanInputError("invalid finding path, identity or count")
        key = (path, code, mark)
        if key in entries:
            raise ScanInputError(f"duplicate finding: {key}")
        entries[key] = count
    return entries


def _baseline(document: dict) -> dict[tuple[str, str, str], int]:
    if (
        not isinstance(document, dict)
        or set(document) != {"schemaVersion", "tool", "findings"}
        or type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != 1
        or document["tool"] not in ("ruff", "mypy")
    ):
        raise ScanInputError("invalid baseline schema or tool")
    return _entries(document["findings"])


def evaluate_baseline(baseline: dict, findings: list[dict], base_baseline: dict) -> tuple[int, list[str]]:
    recorded = _baseline(baseline)
    base = _baseline(base_baseline)
    current = _entries(findings)
    if baseline["tool"] != base_baseline["tool"]:
        raise ScanInputError("baseline tools differ")
    lines = []
    for key in sorted(recorded.keys() | current.keys() | base.keys()):
        identity = " ".join(key)
        old, new, previous = recorded.get(key, 0), current.get(key, 0), base.get(key, 0)
        if new > old:
            lines.append(f"BASELINE.new {identity}: {old} -> {new}; fix the new finding, do not refresh the baseline")
        if new < old:
            fix = "remove the stale entry" if new == 0 else "reduce the baseline count"
            lines.append(f"BASELINE.stale {identity}: {old} -> {new}; {fix}")
        if old > previous:
            lines.append(f"BASELINE.refresh {identity}: {previous} -> {old}; revert the baseline addition or count increase")
    return int(bool(lines)), lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", choices=("ruff", "mypy"), required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--root", default=str(ROOT))
    base = parser.add_mutually_exclusive_group()
    base.add_argument("--base-baseline")
    base.add_argument("--base-ref", default="origin/main")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    try:
        baseline_path = root / args.baseline
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        if args.base_baseline:
            base_baseline = json.loads((root / args.base_baseline).read_text(encoding="utf-8"))
        else:
            relative = baseline_path.resolve().relative_to(root).as_posix()
            result = subprocess.run(
                ["git", "show", f"{args.base_ref}:{relative}"], cwd=root,
                text=True, capture_output=True, check=False,
            )
            if result.returncode:
                raise ScanInputError(f"cannot read base baseline: {result.stderr.strip()}")
            base_baseline = json.loads(result.stdout)
        _baseline(baseline)
        if baseline["tool"] != args.tool:
            raise ScanInputError("baseline tool does not match --tool")
        report = (root / args.report).read_text(encoding="utf-8")
        # Load only diagnostic sources, lazily through the parser's mapping seam.
        class Sources(dict):
            def __missing__(self, path):
                try:
                    source_path = (root / path).resolve()
                    source_path.relative_to(root)
                    self[path] = source_path.read_text(encoding="utf-8")
                    return self[path]
                except (OSError, ValueError) as exc:
                    raise ScanInputError(f"{path}: missing or unreadable source") from exc

        parse = parse_ruff if args.tool == "ruff" else parse_mypy
        status, lines = evaluate_baseline(
            baseline, parse(report, root=str(root), sources=Sources()), base_baseline
        )
    except (OSError, ValueError, UnicodeError) as exc:
        print(f"baseline-check: {exc}", file=sys.stderr)
        return 2
    for line in lines:
        print(line, file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main())
