#!/usr/bin/env python3
"""Apply layout, naming, dependency and test-placement owner policies."""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol, TypedDict, Unpack, cast

ROOT = Path(__file__).resolve().parents[2]


class RepositoryOwners(TypedDict):
    """Reviewed ownership declarations, independent of the inspected checkout."""

    files: dict[str, str]
    source_directories: dict[str, str]
    test_tiers: dict[str, list[str]]
    seam_owners: dict[str, list[str]]
    external_callback_names: dict[str, list[str]]


class LayoutPolicy(Protocol):
    """Existing structural policy shared with the quality gate."""

    def root_violations(self, paths: list[str]) -> list[str]: ...
    def lab_violations(self, paths: list[str]) -> list[str]: ...
    def package_violations(self, paths: list[str]) -> list[str]: ...
    def version_violations(self, paths: list[str]) -> list[str]: ...


class Diagnostic(Protocol):
    """Naming diagnostic from the Python policy."""

    @property
    def message(self) -> str: ...


class PythonPolicy(Protocol):
    """Python placement and naming decisions."""

    def is_maintained_python_relpath(self, path: str) -> bool: ...
    def structure_diagnostics_for_relpath(self, path: str) -> Sequence[Diagnostic]: ...
    def placement_problems(self, path: str, tree: ast.Module) -> list[str]: ...


class ImportResult(Protocol):
    """Import-linter process status."""

    @property
    def returncode(self) -> int: ...


class ImportOptions(TypedDict):
    """Keyword-only arguments of the bounded import checker."""

    cwd: Path
    text: Literal[True]
    capture_output: bool
    check: bool
    timeout: int


class ImportRunner(Protocol):
    """Injected bounded import-linter boundary."""

    def __call__(self, command: list[str], /, **options: Unpack[ImportOptions]) -> ImportResult: ...


def load_policy(name: str, filename: str) -> object:
    """Load a repository checker by its explicit path."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/checks" / filename)
    if spec is None or spec.loader is None:
        message = f"Cannot load repository policy {filename}"
        raise ImportError(message)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


LAYOUT = cast("LayoutPolicy", load_policy("repository_layout", "check-structure.py"))
QUALITY = cast("PythonPolicy", load_policy("repository_python_quality", "check-python-quality.py"))
OWNERS = cast(
    "RepositoryOwners",
    json.loads((ROOT / "scripts/checks/repository-owners.json").read_text(encoding="utf-8")),
)
LAYERS = (
    "contract",
    "judge",
    "subject",
    "grading",
    "scoring",
    "design",
    "engine",
    "evaluation",
    "cli",
)


def finding(path: str, rule: str, problem: str, repair: str) -> str:
    """Render an actionable, single-line policy finding."""
    detail = " ".join(problem.split()).replace(";", ",")
    return f"{path}: {rule}: {detail}; {repair}"


def file_markers(tree: ast.Module) -> set[str]:
    """Read only file-level tier declarations."""
    markers: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
        ):
            continue
        markers.update(
            item.attr
            for item in ast.walk(node.value)
            if isinstance(item, ast.Attribute)
            and isinstance(item.value, ast.Attribute)
            and ast.unparse(item.value) == "pytest.mark"
        )
    return markers & {"slow", "quality", "docker", "security", "perf"}


def ownership_findings(path: str) -> list[str]:
    """Require exact data ownership or a declared Python capability directory."""
    directory = PurePosixPath(path).parent.as_posix() + "/"
    fixture = re.match(r"^(lab/stages/[^/]+/[^/]+)/fixtures/", path)
    if fixture and fixture[1] + "/stage.yaml" in OWNERS["files"]:
        return []
    owned_source = path.endswith(".py") and directory in OWNERS["source_directories"]
    found = []
    if path not in OWNERS["files"] and not owned_source:
        found.append(
            finding(
                path,
                "STRUCT.folder-placement",
                "file has no declared capability owner",
                "move to an existing capability or declare its owner in repository-owners.json",
            )
        )
    return found


def tier_findings(path: str, tree: ast.Module) -> list[str]:
    """Reject undeclared behavior owners and wrong test tiers."""
    if not ("/tests/" in path or path.startswith("contract/conformance/")):
        return []
    if path not in OWNERS["test_tiers"]:
        return [
            finding(
                path,
                "TEST.placement",
                "test has no declared behavior owner or tier",
                "declare its behavior and file tier in repository-owners.json",
            )
        ]
    actual = file_markers(tree)
    if actual != set(OWNERS["test_tiers"][path]):
        return [
            finding(
                path,
                "TEST.placement",
                f"declared tier differs from markers {sorted(actual)}",
                "use the declared file-level tier in repository-owners.json",
            )
        ]
    return []


def symbol_findings(path: str, tree: ast.Module) -> list[str]:
    """Enforce canonical Python callable and class names."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if node.name in OWNERS["external_callback_names"].get(path, []):
            continue
        pattern = r"_*[A-Z][A-Za-z0-9]*" if isinstance(node, ast.ClassDef) else r"_*[a-z][a-z0-9_]*"
        if not re.fullmatch(pattern, node.name):
            found.append(
                finding(
                    path,
                    "NAME.conventions",
                    f"noncanonical symbol {node.name}",
                    "use PascalCase classes and snake_case functions",
                )
            )
    return found


def normalized_path(root: Path, raw: str) -> str | None:
    """Resolve an inside-root absolute path, refusing traversal and foreign paths."""
    relative = PurePosixPath(raw)
    if ".." in relative.parts:
        return None
    if relative.is_absolute():
        try:
            relative = PurePosixPath(Path(raw).resolve().relative_to(root.resolve()).as_posix())
        except ValueError:
            return None
    if ".." in relative.parts or not relative.parts or "\\" in raw:
        return None
    return relative.as_posix()


def python_findings(root: Path, path: str) -> list[str]:
    """Apply Python-specific rules after path ownership has been established."""
    source = root / path
    if not source.is_file() or source.suffix != ".py":
        return []
    found: list[str] = []
    if QUALITY.is_maintained_python_relpath(path):
        found.extend(
            finding(path, "NAME.conventions", item.message, "use the canonical root and snake_case")
            for item in QUALITY.structure_diagnostics_for_relpath(path)
        )
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=path)
    except (OSError, UnicodeError, SyntaxError) as error:
        return [finding(path, "PYS000", str(error), "restore readable UTF-8 Python")]
    found.extend(tier_findings(path, tree))
    found.extend(symbol_findings(path, tree))
    found.extend(
        finding(
            path,
            "TEST.placement",
            problem,
            "use the owning test directory and file-level pytestmark",
        )
        for problem in QUALITY.placement_problems(path, tree)
    )
    return found


def path_findings(root: Path, paths: Sequence[str]) -> list[str]:
    """Check complete path ownership and canonical Python names and test tiers."""
    found = []
    for raw in paths:
        path = normalized_path(root, raw)
        if path is None:
            found.append(
                finding(
                    raw,
                    "STRUCT.folder-placement",
                    "unsafe repository path",
                    "use a repository path without traversal",
                )
            )
            continue
        if path.startswith(("docs/", "var/")):
            continue
        problems = (
            LAYOUT.root_violations([path])
            + LAYOUT.lab_violations([path])
            + LAYOUT.package_violations([path])
        )
        found.extend(
            finding(
                path,
                "STRUCT.folder-placement",
                problem,
                "move to the allowlisted capability in check-structure.py",
            )
            for problem in problems
        )
        if not problems:
            found.extend(ownership_findings(path))
        found.extend(
            finding(path, "NAME.conventions", problem, "put versions in manifest data, not paths")
            for problem in LAYOUT.version_violations([path])
        )
        if not re.match(r"^lab/stages/[^/]+/[^/]+/fixtures/", path):
            found.extend(python_findings(root, path))
    return found


def imported_edges(tree: ast.Module, path: str) -> list[str]:
    """Resolve product import edges without importing authored code."""
    module = path.split("/src/", 1)[1].removesuffix(".py").replace("/", ".")
    package = (
        module.removesuffix(".__init__")
        if module.endswith(".__init__")
        else module.rpartition(".")[0]
    )
    edges: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            edges.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            destination = node.module or ""
            if node.level:
                destination = importlib.util.resolve_name("." * node.level + destination, package)
            if destination == "raidar" or not node.module:
                edges.extend(f"{destination}.{alias.name}" for alias in node.names)
            elif destination:
                edges.append(destination)
    return [edge for edge in edges if edge == "raidar" or edge.startswith("raidar.")]


def source_edge_findings(root: Path, path: str) -> list[str]:
    """Refuse unowned destinations and upward cross-capability imports."""
    source = root / path
    if not source.is_file():
        return []
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=path)
        edges = imported_edges(tree, path)
    except (OSError, UnicodeError, SyntaxError, ImportError) as error:
        return [
            finding(
                path,
                "STRUCT.dependency-direction",
                str(error),
                "restore readable Python imports within the declared source package",
            )
        ]
    source_owner = (
        PurePosixPath(path).parts[3] if path.startswith("orchestrator/src/raidar/") else None
    )
    found = []
    for edge in edges:
        destination = edge.split(".")[1] if "." in edge else None
        allowed = (
            source_owner is not None
            and destination is not None
            and source_owner in LAYERS
            and destination in LAYERS
            and LAYERS.index(destination) <= LAYERS.index(source_owner)
        )
        if not allowed:
            found.append(
                finding(
                    path,
                    "STRUCT.dependency-direction",
                    f"unmapped or forbidden import {edge}",
                    "import a declared lower-tier capability or portable raidar_contract",
                )
            )
    return found


def dependency_findings(
    paths: Sequence[str],
    *,
    root: Path = ROOT,
    run: ImportRunner = subprocess.run,
) -> list[str]:
    """Reject unowned imports independently of the legacy import-linter graph."""
    sources = [path for path in paths if path.endswith(".py") and "/src/" in path]
    found = [line for path in sources for line in source_edge_findings(root, path)]
    if not sources:
        return found
    command = [
        str(Path(sys.executable).with_name("lint-imports")),
        "--config",
        str(ROOT / "orchestrator/.importlinter"),
        "--no-cache",
    ]
    try:
        result = run(
            command,
            cwd=root / "orchestrator",
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        if result.returncode == 0:
            return found
        problem = "repository import contracts failed"
    except OSError as error:
        problem = f"cannot launch import-linter: {error}"
    return found + [
        finding(
            path,
            "STRUCT.dependency-direction",
            problem,
            "run lint-imports with orchestrator/.importlinter and remove forbidden edges",
        )
        for path in sources
    ]


def main(paths: Sequence[str], *, root: Path) -> int:
    """Check supplied paths, or the complete maintained repository inventory."""
    if not paths:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            check=True,
            text=True,
            timeout=30,
        )
        paths = sorted(set(result.stdout.split("\0")) - {""})
    sources = [
        normalized for path in paths if (normalized := normalized_path(root, path)) is not None
    ]
    findings = path_findings(root, paths) + dependency_findings(sources, root=root)
    for line in findings:
        print(line, file=sys.stderr)
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], root=ROOT))
