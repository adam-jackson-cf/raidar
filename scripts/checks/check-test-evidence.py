#!/usr/bin/env python3
"""Require changed-behavior evidence for test growth and reject duplicate paths."""

from __future__ import annotations

import argparse
import ast
import copy
import io
import itertools
import json
import subprocess
import sys
import tarfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

MAP_PATH = ".omp/test-behavior-map.json"
type TestFunction = ast.FunctionDef | ast.AsyncFunctionDef
type Bindings = dict[str, ast.expr]
type ParameterRow = tuple[str, Bindings]
type Dimension = list[ParameterRow]


@dataclass(frozen=True)
class Case:
    """One collected test invocation with its normalized behavior."""

    selector: str
    fingerprint: str


def _expression(node: ast.AST) -> str:
    return ast.dump(node, include_attributes=False)


def _qualified(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_qualified(node.value)}.{node.attr}"
    return ""


def _imports(tree: ast.AST) -> dict[str, str]:
    names: dict[str, str] = {}
    pending = list(ast.iter_child_nodes(tree))
    while pending:
        node = pending.pop(0)
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            pending.extend(ast.iter_child_nodes(node))
        if isinstance(node, ast.Import):
            for alias in node.names:
                names[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                module = f"{'.' * node.level}{node.module or ''}"
                names[alias.asname or alias.name] = (
                    f"{module}{'.' if node.module else ''}{alias.name}"
                )
    return names


def _literal_id(node: ast.expr, name: str, index: int) -> str:
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError):
        return f"{name}{index}"
    if value is None or isinstance(value, (str, int, float, bool, complex)):
        return str(value).encode("unicode_escape").decode("ascii")
    return f"{name}{index}"


def _parameter_names(node: ast.expr) -> list[str]:
    raw_names = ast.literal_eval(node)
    if isinstance(raw_names, str):
        return [part.strip() for part in raw_names.split(",")]
    if isinstance(raw_names, (tuple, list)) and all(
        isinstance(name, str) for name in raw_names
    ):
        return list(raw_names)
    raise ValueError("parametrize names must be static strings")


def _row_values(row: ast.expr, width: int) -> tuple[list[ast.expr], ast.expr | None]:
    explicit = None
    if isinstance(row, ast.Call) and _qualified(row.func).endswith(".param"):
        values = row.args
        explicit = next(
            (keyword.value for keyword in row.keywords if keyword.arg == "id"), None
        )
    else:
        values = (
            row.elts if width > 1 and isinstance(row, (ast.Tuple, ast.List)) else [row]
        )
    if len(values) != width:
        raise ValueError("parametrize row width does not match names")
    return values, explicit


def _unique_ids(rows: Dimension) -> Dimension:
    counts: dict[str, int] = {}
    for label, _ in rows:
        counts[label] = counts.get(label, 0) + 1
    offsets: dict[str, int] = {}
    unique = []
    occupied = set(counts)
    for label, bindings in rows:
        if counts[label] == 1:
            unique.append((label, bindings))
            continue
        offset = offsets.get(label, 0)
        separator = "_" if label[-1:].isdigit() else ""
        candidate = f"{label}{separator}{offset}"
        while candidate in occupied:
            offset += 1
            candidate = f"{label}{separator}{offset}"
        offsets[label] = offset + 1
        occupied.add(candidate)
        unique.append((candidate, bindings))
    return unique


def _row_id(
    names: Sequence[str],
    values: Sequence[ast.expr],
    index: int,
    explicit: ast.expr | None,
) -> str:
    label = ast.literal_eval(explicit) if explicit is not None else None
    if label is not None:
        return str(label).encode("unicode_escape").decode("ascii")
    return "-".join(
        _literal_id(value, name, index)
        for name, value in zip(names, values, strict=True)
    )


def _parameter_rows(decorator: ast.Call) -> Dimension:
    if len(decorator.args) < 2:
        raise ValueError("parametrize requires static names and rows")
    names = _parameter_names(decorator.args[0])
    rows = decorator.args[1]
    if not isinstance(rows, (ast.List, ast.Tuple)):
        raise ValueError("parametrize rows must be a static list or tuple")
    ids = next(
        (keyword.value for keyword in decorator.keywords if keyword.arg == "ids"), None
    )
    if ids is not None and not isinstance(ids, (ast.List, ast.Tuple)):
        raise ValueError("parametrize ids must be a static list or tuple")
    if isinstance(ids, (ast.List, ast.Tuple)) and len(ids.elts) != len(rows.elts):
        raise ValueError("parametrize ids must match the number of rows")
    result: Dimension = []
    for index, row in enumerate(rows.elts):
        values, explicit = _row_values(row, len(names))
        if explicit is None and isinstance(ids, (ast.List, ast.Tuple)):
            explicit = ids.elts[index]
        result.append(
            (
                _row_id(names, values, index, explicit),
                dict(zip(names, values, strict=True)),
            )
        )
    return _unique_ids(result)


class Normalizer(ast.NodeTransformer):
    """Resolve aliases and local bindings without evaluating test code."""

    def __init__(
        self, aliases: Mapping[str, str], bindings: Mapping[str, ast.expr]
    ) -> None:
        self.aliases = aliases
        self.bindings = dict(bindings)
        self.locals: dict[str, str] = {}

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if isinstance(node.ctx, ast.Load) and node.id in self.bindings:
            return self.bindings[node.id]
        if node.id in self.locals:
            return ast.Name(id=self.locals[node.id], ctx=node.ctx)
        if node.id in self.aliases:
            return ast.Name(id=self.aliases[node.id], ctx=node.ctx)
        return node

    def visit_arg(self, node: ast.arg) -> ast.AST:
        return ast.arg(arg=self.locals.get(node.arg, node.arg))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        node.name = self.locals.get(node.name, node.name)
        return self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        node.name = self.locals.get(node.name, node.name)
        return self.generic_visit(node)


def _local_names(
    function: TestFunction, bindings: Mapping[str, ast.expr]
) -> dict[str, str]:
    names: dict[str, str] = {}
    arguments = [
        *function.args.posonlyargs,
        *function.args.args,
        *function.args.kwonlyargs,
    ]
    arguments.extend(
        argument
        for argument in (function.args.vararg, function.args.kwarg)
        if argument is not None
    )
    for argument in arguments:
        if argument.arg not in bindings:
            names[argument.arg] = f"argument{len(names)}"
    for node in ast.walk(function):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.setdefault(node.id, f"local{len(names)}")
        elif (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node is not function
        ):
            names.setdefault(node.name, f"function{len(names)}")
    return names


def _name_count(
    function: TestFunction, target: str, context: type[ast.expr_context]
) -> int:
    return sum(
        isinstance(node, ast.Name)
        and isinstance(node.ctx, context)
        and node.id == target
        for node in ast.walk(function)
    )


def _inline_temporary(
    function: TestFunction, statement: ast.stmt, normalizer: Normalizer
) -> bool:
    if (
        not isinstance(statement, ast.Assign)
        or len(statement.targets) != 1
        or not isinstance(statement.targets[0], ast.Name)
    ):
        return False
    target = statement.targets[0].id
    if (
        _name_count(function, target, ast.Load) != 1
        or _name_count(function, target, ast.Store) != 1
    ):
        return False
    normalized = normalizer.visit(copy.deepcopy(statement.value))
    if not isinstance(normalized, ast.expr):
        return False
    normalizer.bindings[target] = normalized
    return True


def _ignored_statement(statement: ast.stmt) -> bool:
    return isinstance(statement, (ast.Import, ast.ImportFrom)) or (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _fingerprint(
    function: TestFunction, aliases: Mapping[str, str], bindings: Mapping[str, ast.expr]
) -> str:
    normalizer = Normalizer({**aliases, **_imports(function)}, bindings)
    normalizer.locals = _local_names(function, bindings)
    body: list[str] = []
    calls: list[str] = []
    for statement in function.body:
        if _ignored_statement(statement):
            continue
        calls.extend(
            _expression(normalizer.visit(copy.deepcopy(node)))
            for node in ast.walk(statement)
            if isinstance(node, ast.Call)
        )
        # Calls retain execution order even when a single-use temporary is inlined.
        if not _inline_temporary(function, statement, normalizer):
            body.append(_expression(normalizer.visit(copy.deepcopy(statement))))
    return "|".join(calls) + "::" + "|".join(body)


class Constants(ast.NodeTransformer):
    """Resolve static module constants used in pytest row IDs."""

    def __init__(self) -> None:
        self.values: dict[str, ast.expr] = {}

    def visit_Name(self, node: ast.Name) -> ast.AST:
        return self.values.get(node.id, node)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> ast.AST:
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                resolved = self.visit(value.value)
                if not isinstance(resolved, ast.Constant):
                    return node
                parts.append(str(resolved.value))
            else:
                return node
        return ast.Constant(value="".join(parts))

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and not node.keywords:
            if node.func.id == "len" and len(node.args) == 1:
                argument = node.args[0]
                if isinstance(argument, ast.Constant) and isinstance(argument.value, (str, bytes)):
                    return ast.Constant(value=len(argument.value))
                if isinstance(argument, (ast.List, ast.Tuple)):
                    return ast.Constant(value=len(argument.elts))
            if (
                node.func.id == "range"
                and 1 <= len(node.args) <= 3
                and all(
                    isinstance(argument, ast.Constant) and type(argument.value) is int
                    for argument in node.args
                )
            ):
                values = [
                    argument.value
                    for argument in node.args
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, int)
                ]
                try:
                    resolved = range(*values)
                except ValueError:
                    return node
                return ast.List(
                    elts=[ast.Constant(value=value) for value in resolved],
                    ctx=ast.Load(),
                )
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "replace"
            and isinstance(node.func.value, ast.Constant)
            and isinstance(node.func.value.value, str)
            and len(node.args) == 2
            and not node.keywords
        ):
            old, new = node.args
            if (
                isinstance(old, ast.Constant)
                and isinstance(old.value, str)
                and isinstance(new, ast.Constant)
                and isinstance(new.value, str)
            ):
                return ast.Constant(
                    value=node.func.value.value.replace(old.value, new.value)
                )
        return node


def _dimensions(
    decorators: Sequence[ast.expr], constants: Constants, aliases: Mapping[str, str]
) -> list[Dimension]:
    dimensions = []
    for decorator in reversed(decorators):
        resolved = Normalizer(aliases, {}).visit(copy.deepcopy(decorator))
        resolved = constants.visit(resolved)
        if (
            isinstance(resolved, ast.Call)
            and _qualified(resolved.func) == "pytest.mark.parametrize"
        ):
            dimensions.append(_parameter_rows(resolved))
    return dimensions


def _expanded_cases(
    selector: str,
    function: TestFunction,
    aliases: Mapping[str, str],
    dimensions: Sequence[Dimension],
) -> list[Case]:
    if not dimensions:
        return [Case(selector, _fingerprint(function, aliases, {}))]
    cases = []
    for combination in itertools.product(*dimensions):
        labels = [label for label, _ in combination]
        bindings: Bindings = {}
        for _, row in combination:
            bindings.update(row)
        cases.append(
            Case(
                f"{selector}[{'-'.join(labels)}]",
                _fingerprint(function, aliases, bindings),
            )
        )
    return cases


def _cases(path: str, text: str) -> tuple[list[Case], set[str]]:
    tree = ast.parse(text, filename=path)
    aliases = _imports(tree)
    constants = Constants()
    for statement in tree.body:
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
        ):
            value = constants.visit(statement.value)
            if isinstance(value, ast.expr):
                constants.values[statement.targets[0].id] = value
    cases: list[Case] = []
    functions: set[str] = set()

    def collect(
        nodes: Sequence[ast.stmt], prefix: str, inherited: list[Dimension]
    ) -> None:
        for node in nodes:
            if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                collect(
                    node.body,
                    f"{prefix}{node.name}::",
                    _dimensions(node.decorator_list, constants, aliases) + inherited,
                )
            elif isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and node.name.startswith("test"):
                selector = f"{path}::{prefix}{node.name}"
                functions.add(selector)
                dimensions = (
                    _dimensions(node.decorator_list, constants, aliases) + inherited
                )
                cases.extend(_expanded_cases(selector, node, aliases, dimensions))

    collect(tree.body, "", [])
    return cases, functions


def _snapshot(sources: Mapping[str, str]) -> tuple[dict[str, str], set[str], list[str]]:
    cases: dict[str, str] = {}
    selectors: set[str] = set(sources)
    errors: list[str] = []
    for path, text in sorted(sources.items()):
        try:
            file_cases, functions = _cases(path, text)
            selectors.update(functions)
            for case in file_cases:
                cases[case.selector] = case.fingerprint
                selectors.add(case.selector)
        except (SyntaxError, ValueError, TypeError) as error:
            errors.append(
                f"{path}: TEST.growth.justified: cannot statically collect tests: "
                f"{' '.join(str(error).split())}; map or remove dynamic collection"
            )
    return cases, selectors, errors


def _mapping_entry(entry: object) -> dict[str, str] | None:
    if not isinstance(entry, dict):
        return None
    source, behavior = entry.get("source"), entry.get("behavior")
    if (
        not isinstance(source, str)
        or not source.strip()
        or not isinstance(behavior, str)
        or not behavior.strip()
    ):
        return None
    return {"source": source, "behavior": behavior}


def _valid_mapping(behavior_map: object) -> dict[str, dict[str, str]] | None:
    if (
        not isinstance(behavior_map, dict)
        or type(behavior_map.get("schemaVersion")) is not int
        or behavior_map.get("schemaVersion") != 1
    ):
        return None
    tests = behavior_map.get("tests")
    if not isinstance(tests, dict):
        return None
    entries: dict[str, dict[str, str]] = {}
    for selector, entry in tests.items():
        validated = _mapping_entry(entry)
        if not isinstance(selector, str) or validated is None:
            return None
        entries[selector] = validated
    return entries


def _growth_findings(
    growth: set[str],
    changed_source_paths: frozenset[str],
    behavior_map: object,
    *,
    map_is_tracked: bool,
) -> list[str]:
    findings = []
    if growth and not map_is_tracked:
        findings.append(
            f"{MAP_PATH}: TEST.growth.justified: evidence map must be tracked; "
            "map or remove added tests"
        )
    tests = _valid_mapping(behavior_map)
    for selector in sorted(growth):
        entry = tests.get(selector) if tests is not None else None
        valid = entry is not None and entry["source"] in changed_source_paths
        if not valid:
            findings.append(
                f"{selector}: TEST.growth.justified: missing changed-source behavior evidence "
                f"in {MAP_PATH}; map or remove"
            )
    return findings


def _duplicate_findings(
    base_cases: Mapping[str, str], current_cases: Mapping[str, str]
) -> list[str]:
    findings = []
    seen: dict[str, str] = {}
    for selector, fingerprint in sorted(base_cases.items()):
        seen.setdefault(fingerprint, selector)
    for selector in sorted(current_cases.keys() & base_cases.keys()):
        seen.setdefault(current_cases[selector], selector)
    for selector in sorted(current_cases.keys() - base_cases.keys()):
        fingerprint = current_cases[selector]
        if fingerprint in seen:
            findings.append(
                f"{selector}: TEST.duplicate-path: duplicates {seen[fingerprint]}; merge or remove"
            )
        else:
            seen[fingerprint] = selector
    return findings


def _named_cases(path: str, text: str) -> dict[str, str]:
    """Case fingerprints keyed by selector without the file path; empty when uncollectable."""
    try:
        file_cases, _ = _cases(path, text)
    except (SyntaxError, ValueError, TypeError):
        return {}
    return {case.selector.split("::", 1)[1]: case.fingerprint for case in file_cases}


def _renamed_files(
    base_sources: Mapping[str, str], current_sources: Mapping[str, str]
) -> dict[str, str]:
    """Map removed base test files to the added file that keeps every case and its behavior."""
    removed = {
        path: _named_cases(path, text)
        for path, text in base_sources.items()
        if path not in current_sources
    }
    renames: dict[str, str] = {}
    for path, text in sorted(current_sources.items()):
        if path in base_sources:
            continue
        cases = _named_cases(path, text)
        for old, old_cases in sorted(removed.items()):
            if cases and cases == old_cases and old not in renames:
                renames[old] = path
                break
    return renames


def evidence_findings(
    *,
    base_sources: Mapping[str, str],
    current_sources: Mapping[str, str],
    changed_source_paths: frozenset[str],
    behavior_map: object,
    map_is_tracked: bool,
) -> list[str]:
    """Return actionable growth and duplicate findings for in-memory snapshots."""
    # A renamed file whose cases keep their names and behavior is neither growth nor a duplicate.
    renames = _renamed_files(base_sources, current_sources)
    base_sources = {renames.get(path, path): text for path, text in base_sources.items()}
    base_cases, base_selectors, _ = _snapshot(base_sources)
    current_cases, current_selectors, current_errors = _snapshot(current_sources)
    findings = [
        error
        for error in current_errors
        if current_sources.get(error.split(": ", 1)[0])
        != base_sources.get(error.split(": ", 1)[0])
    ]
    findings.extend(
        _growth_findings(
            current_selectors - base_selectors,
            changed_source_paths,
            behavior_map,
            map_is_tracked=map_is_tracked,
        )
    )
    findings.extend(_duplicate_findings(base_cases, current_cases))
    return findings


def _git_bytes(root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", *arguments], cwd=root, check=True, capture_output=True, timeout=60
    ).stdout


def _git(root: Path, *arguments: str) -> str:
    return _git_bytes(root, *arguments).decode("utf-8")


def _base_sources(root: Path, ref: str, paths: Sequence[str]) -> dict[str, str]:
    tests = [path for path in paths if _is_test(path)]
    if not tests:
        return {}
    payload = _git_bytes(root, "archive", "--format=tar", ref, "--", *tests)
    sources: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for member in archive:
            if member.isfile():
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError(f"cannot read base test {member.name}")
                sources[member.name] = stream.read().decode("utf-8")
    return sources


def _is_test(path: str) -> bool:
    parts = Path(path).parts
    owned = path.startswith(("orchestrator/tests/", "contract/conformance/")) or (
        path.startswith("lab/stages/") and "tests" in parts
    )
    return owned and path.endswith(".py") and Path(path).name.startswith("test_")


def main() -> int:
    """Compare the working tree and index with the requested git base."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--base-ref", default="origin/main")
    args = parser.parse_args()
    root = args.repo_root.resolve()
    try:
        base_paths = _git(
            root, "ls-tree", "-r", "--name-only", "-z", args.base_ref
        ).split("\0")
        paths = _git(
            root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"
        ).split("\0")
        base = _base_sources(root, args.base_ref, base_paths)
        current = {
            path: (root / path).read_text(encoding="utf-8")
            for path in set(paths)
            if _is_test(path) and (root / path).is_file()
        }
        changed_paths = set(
            _git(root, "diff", "--name-only", "-z", args.base_ref).split("\0")
        ) | (set(paths) - set(base_paths))
        changed = frozenset(
            path
            for path in changed_paths
            if path and path != MAP_PATH and not _is_test(path)
        )
        tracked = MAP_PATH in _git(
            root, "ls-files", "--cached", "-z", "--", MAP_PATH
        ).split("\0")
        try:
            behavior_map: object = json.loads(
                (root / MAP_PATH).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            behavior_map = None
        findings = evidence_findings(
            base_sources=base,
            current_sources=current,
            changed_source_paths=changed,
            behavior_map=behavior_map,
            map_is_tracked=tracked,
        )
    except (
        OSError,
        UnicodeError,
        ValueError,
        tarfile.TarError,
        subprocess.SubprocessError,
    ) as error:
        findings = [
            f"{MAP_PATH}: TEST.growth.justified: cannot compare git snapshots: "
            f"{' '.join(str(error).split())}; map or remove after restoring the base ref"
        ]
    for finding in findings:
        print(finding, file=sys.stderr)
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
