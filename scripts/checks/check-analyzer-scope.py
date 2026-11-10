#!/usr/bin/env python3
"""Check behavioral analyzer boundaries; PYQ210/211 are intentionally inapplicable.

Scorers use a structural Protocol and CoverageScorer inherits its entrypoint.
See docs/reboot/evidence/decide-analyzer-sol.md for the adopted architecture.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCOPE_ROOTS = (
    "orchestrator/src/raidar/scoring/",
    "orchestrator/src/raidar/evaluation/analysis/",
    "orchestrator/src/raidar/engine/health/",
)
FILESYSTEM_SEAMS = frozenset(
    {
        f"{SCOPE_ROOTS[0]}framework/store.py",
        f"{SCOPE_ROOTS[0]}framework/trial_inputs.py",
        f"{SCOPE_ROOTS[1]}sources.py",
    }
)
PROCESS_SEAM = f"{SCOPE_ROOTS[0]}code_quality/measurement.py"
HEALTH_SEAM = f"{SCOPE_ROOTS[2]}base.py"
PROCESS_CALLS = frozenset(
    {
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.getoutput",
        "subprocess.getstatusoutput",
    }
)
FILESYSTEM_METHODS = frozenset({"open", "read_text", "read_bytes", "write_text", "write_bytes"})


class BoundaryVisitor(ast.NodeVisitor):
    """Resolve imported spellings and local Path bindings without executing input."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.aliases: dict[str, str] = {}
        self.symbols: list[str] = []
        self.operations: list[tuple[ast.AST, str, str]] = []
        self.parents: dict[ast.AST, ast.AST] = {}

    def qualified(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return f"{self.qualified(node.value)}.{node.attr}"
        if isinstance(node, ast.Call):
            return self.qualified(node.func)
        return ""

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.aliases[alias.asname or alias.name.split(".")[0]] = alias.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    def visit_Assign(self, node: ast.Assign) -> None:
        qualified = self.qualified(node.value)
        if qualified == "pathlib.Path" or qualified.startswith("pathlib.Path."):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.aliases[target.id] = "pathlib.Path"
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and self.qualified(node.annotation) == "pathlib.Path":
            self.aliases[node.target.id] = "pathlib.Path"
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        previous = self.aliases.copy()
        self.symbols.append(node.name)
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            if (
                argument.annotation is not None
                and self.qualified(argument.annotation) == "pathlib.Path"
            ):
                self.aliases[argument.arg] = "pathlib.Path"
        self.generic_visit(node)
        self.symbols.pop()
        self.aliases = previous

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.symbols.append(node.name)
        self.generic_visit(node)
        self.symbols.pop()

    def record(self, node: ast.AST, code: str) -> None:
        symbol = ".".join(self.symbols)
        filesystem_seam = self.path in FILESYSTEM_SEAMS or (
            self.path == HEALTH_SEAM and symbol in {"Audit.from_disk", "read_ledger"}
        )
        process_seam = self.path == PROCESS_SEAM and symbol == "run_process"
        if (code == "PYS251" and filesystem_seam) or (code == "PYS252" and process_seam):
            return
        self.operations.append((node, code, symbol))

    def visit_Call(self, node: ast.Call) -> None:
        qualified = self.qualified(node.func)
        if qualified in {"open", "builtins.open", "io.open", "os.open"} or (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in FILESYSTEM_METHODS
            and (
                node.func.attr != "open"
                or self.qualified(node.func.value).startswith("pathlib.Path")
            )
        ):
            self.record(node, "PYS251")
        if qualified == "os.getenv" or qualified in PROCESS_CALLS:
            self.record(node, "PYS252")
        self.generic_visit(node)

    def record_environment(self, node: ast.AST) -> None:
        parent = self.parents.get(node)
        while (isinstance(parent, (ast.Attribute, ast.Subscript)) and parent.value is node) or (
            isinstance(parent, ast.Call) and parent.func is node
        ):
            node = parent
            parent = self.parents.get(node)
        self.record(node, "PYS252")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self.qualified(node) == "os.environ":
            self.record_environment(node)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if self.qualified(node) == "os.environ":
            self.record_environment(node)


def _matches_parts(pattern: list[str], parts: list[str]) -> bool:
    if not pattern:
        return not parts
    if pattern[0] == "**":
        return any(_matches_parts(pattern[1:], parts[index:]) for index in range(len(parts) + 1))
    return (
        bool(parts)
        and fnmatch.fnmatchcase(parts[0], pattern[0])
        and _matches_parts(pattern[1:], parts[1:])
    )


def _matches_owner(pattern: str, root: str) -> bool:
    """Match CODEOWNERS' anchored paths, directory inheritance and basename globs."""
    anchored = pattern.startswith("/")
    pattern = pattern.lstrip("/")
    parts = root.rstrip("/").split("/")
    pattern = pattern.rstrip("/")
    if anchored or "/" in pattern:
        return any(
            _matches_parts(pattern.split("/"), parts[:index]) for index in range(1, len(parts) + 1)
        )
    return any(fnmatch.fnmatchcase(part, pattern) for part in parts)


def ownership_findings(root: Path) -> list[str]:
    owners_path = root / "CODEOWNERS"
    entries = owners_path.read_text(encoding="utf-8").splitlines() if owners_path.is_file() else []
    findings = []
    for scope in SCOPE_ROOTS:
        owned = False
        for entry in entries:
            fields = entry.split("#", 1)[0].split()
            if fields and _matches_owner(fields[0], scope):
                owned = len(fields) > 1
        if not owned:
            findings.append(
                f"{scope} PYS253 missing applicable CODEOWNERS owner; "
                "assign an owner to this subsystem in CODEOWNERS"
            )
    return findings


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _scan(root: Path, paths: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    diagnostics = ownership_findings(root)
    findings: list[dict[str, str]] = []
    for path in sorted(set(paths)):
        if not path.endswith(".py") or not path.startswith(SCOPE_ROOTS):
            continue
        source = (root / path).read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            diagnostics.append(
                f"{path}:{exc.lineno} PYS000 {exc.msg}; repair Python syntax before analysis"
            )
            continue
        visitor = BoundaryVisitor(path)
        visitor.parents = _parents(tree)
        visitor.visit(tree)
        for node, code, symbol in visitor.operations:
            row = getattr(node, "lineno", 1)
            column = getattr(node, "col_offset", 0) + 1
            operation = ast.unparse(node)
            findings.append(
                {"path": path, "code": code, "symbol": symbol, "source_line": operation}
            )
            fix = (
                "inject retained inputs through an approved filesystem seam"
                if code == "PYS251"
                else "inject environment or process results through run_process"
            )
            diagnostics.append(f"{path}:{row}:{column} {code} direct {operation}; {fix}")
    return diagnostics, findings


def analyzer_findings(root: Path, paths: list[str]) -> list[str]:
    """Return unsuppressed operations and whole-scope ownership diagnostics."""
    return _scan(root, paths)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--format", choices=("text", "findings-json"), default="text")
    arguments = parser.parse_args()
    paths = [
        path.relative_to(arguments.repo_root).as_posix()
        for scope in SCOPE_ROOTS
        for path in (arguments.repo_root / scope).rglob("*.py")
    ]
    diagnostics, findings = _scan(arguments.repo_root, paths)
    if arguments.format == "findings-json":
        # Ownership and parse failures must never disappear behind an I/O baseline.
        blockers = [line for line in diagnostics if " PYS253 " in line or " PYS000 " in line]
        if blockers:
            parser.exit(2, "\n".join(blockers) + "\n")
        print(json.dumps(findings))
        return 0
    else:
        print("\n".join(diagnostics))
    return int(bool(diagnostics))


if __name__ == "__main__":
    raise SystemExit(main())
