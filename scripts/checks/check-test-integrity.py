#!/usr/bin/env python3
"""Check that tests exercise Raidar's real code and that production code carries no test hooks.

Test files are Python files under a test tier location (``orchestrator/tests``,
``review-surface/tests``, ``review-surface/journeys``), files matching ``orchestrator/tests/
test_*.py`` and every ``conftest.py``. Production files are the other Python files with an owned
package (``raidar``, ``review-surface``) among their directories or as their module name.

Rules:

* TEST.no-self-mocking (tests): no ``patch``/``patch.object``/``patch.dict``/``patch.multiple``/
  ``setattr``/``delattr``/``create_autospec`` call whose first positional argument resolves into an
  owned package; no ``Mock``-family call whose ``spec``/``spec_set`` resolves into one; no
  assignment to an attribute of an owned object. A target resolves through the file's absolute
  imports, or is a string literal naming a dotted path.
* TEST.no-evasion (tests): no skip, skipif, xfail or flaky decorator, no ``pytest.skip``/
  ``pytest.xfail``/``pytest.importorskip``/``self.skipTest`` call and no skipping ``pytestmark``.
* TEST.property-seed (tests): a Hypothesis ``@given`` test also carries ``@seed(...)``,
  ``print_blob=True`` or ``derandomize=True``.
* TEST.no-test-hooks (production): no plain string literal naming a test marker (``"pytest"``,
  ``"unittest"``, ``PYTEST*``, ``TESTING``, ``TEST_MODE``, ``UNDER_TEST``, ``TEST``/``TESTS`` with
  optional ``_``-joined affixes); no identifier ending in a test-only hook name (``for_test``,
  ``test_hook``, ``test_only``, ``test_mode`` and the like); no public top-level function or
  class that test files name and no other production file names. Symbols a third party loads
  are excepted: ``main``, a function registered by a Click ``command``/``group`` decorator, and a
  symbol named by a ``module:Symbol`` string in production code or by a ``[project.scripts]``,
  ``[project.gui-scripts]`` or ``[project.entry-points.*]`` value of the defining project's
  ``pyproject.toml``.

``test-integrity-exceptions.json`` waives a rule for a path (exact or ``fnmatch`` glob); each
entry names the ADR section that justifies it. An entry that waives nothing, or names a missing
ADR, is itself a finding (TEST.exceptions).

Paths are the tracked files plus untracked files that are not git-ignored, except fixture
programs (``git_inventory.is_fixture_program``), which are tool inputs rather than sources.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import token
import tokenize
import tomllib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from git_inventory import is_fixture_program, python_paths

ROOT = Path(__file__).resolve().parents[2]
EXCEPTIONS = "scripts/checks/test-integrity-exceptions.json"
OWNED_PACKAGES = frozenset({"raidar", "review-surface"})
ENTRY_POINT_SYMBOLS = frozenset({"main"})
TEST_LOCATIONS = ("orchestrator/tests", "review-surface/tests", "review-surface/journeys")
TEST_FILE_PATTERNS = ("orchestrator/tests/test_*.py",)

SELF_MOCKING = "TEST.no-self-mocking"
EVASION = "TEST.no-evasion"
PROPERTY_SEED = "TEST.property-seed"
TEST_HOOKS = "TEST.no-test-hooks"
EXCEPTIONS_RULE = "TEST.exceptions"
RULES = frozenset({SELF_MOCKING, EVASION, PROPERTY_SEED, TEST_HOOKS})

PATCH_CALL = re.compile(
    r"(^|\.)(patch|patch\.object|patch\.dict|patch\.multiple|setattr|delattr|create_autospec)$"
)
MOCK_CALL = re.compile(r"(^|\.)(Mock|MagicMock|AsyncMock|NonCallableMock|NonCallableMagicMock)$")
EVASION_DECORATOR = re.compile(
    r"^\s*((pytest\.)?mark\.(skip|skipif|xfail|flaky)"
    r"|(unittest\.)?(skip|skipIf|skipUnless|expectedFailure)|flaky)\b"
)
EVASION_CALL = re.compile(r"^(pytest\.(skip|xfail|importorskip)|self\.skipTest)$")
EVASION_MARK = re.compile(r"^pytestmark\b[^=]*=[\s\S]*mark\.(skip|skipif|xfail|flaky)\b")
GIVEN = re.compile(r"^\s*(hypothesis\.)?given\b")
SEEDED = re.compile(r"^\s*(hypothesis\.)?seed\b|print_blob\s*=\s*True|derandomize\s*=\s*True")
TEST_MARKER = re.compile(
    r"^[rRbBuU]*['\"]([A-Z0-9_]*_)?(PYTEST[A-Z0-9_]*|TESTING|TEST_MODE|UNDER_TEST|TESTS?)"
    r"(_[A-Z0-9_]*)?['\"]$|^[rRbBuU]*['\"](pytest|unittest)['\"]$"
)
HOOK_NAME = re.compile(
    r"(?i)(^|_)(for_tests?|for_testing|test_hooks?|testing_only|test_only|test_mode)$"
)
DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
# ``module:Symbol``: how Harbor, setuptools entry points and similar loaders name an object.
OBJECT_REFERENCE = re.compile(r"^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*):([A-Za-z_]\w*)$")
CLICK_REGISTRATION = re.compile(r"(^|\.)(command|group)$")
ENTRY_POINT_TABLES = ("scripts", "gui-scripts")


@dataclass(frozen=True)
class Finding:
    """One rule violation at a line of a repository file or an exceptions entry."""

    path: str
    rule: str
    location: str
    problem: str
    repair: str

    def render(self) -> str:
        return f"{self.path}: {self.rule}: {self.location}: {self.problem}; {self.repair}"


@dataclass(frozen=True)
class Waiver:
    """A waiver of one rule for a path, justified by an ADR section."""

    path: str
    rule: str
    adr: str


def is_test_file(path: str) -> bool:
    if PurePosixPath(path).name == "conftest.py":
        return True
    if any(path.startswith(f"{location}/") for location in TEST_LOCATIONS):
        return True
    return any(fnmatchcase(path, pattern) for pattern in TEST_FILE_PATTERNS)


def is_production_file(path: str) -> bool:
    pure = PurePosixPath(path)
    return not is_test_file(path) and bool(OWNED_PACKAGES & {*pure.parts[:-1], pure.stem})


def import_map(tree: ast.Module) -> dict[str, str]:
    """Local name -> dotted origin for the file's absolute imports."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    names[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    names[top] = top
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def resolve(node: ast.expr, imports: dict[str, str]) -> str | None:
    """Dotted origin of a string target or an imported name or attribute, else None."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if not isinstance(node, ast.Name | ast.Attribute):
        return None
    head, _, rest = ast.unparse(node).partition(".")
    origin = imports.get(head)
    if origin is None:
        return None
    return f"{origin}.{rest}" if rest else origin


def owned(origin: str | None) -> bool:
    return bool(origin) and str(origin).split(".")[0] in OWNED_PACKAGES


def snippet(node: ast.AST) -> str:
    return " ".join(ast.unparse(node).split())[:80]


def self_mocking(path: str, tree: ast.Module, imports: dict[str, str]) -> Iterator[Finding]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call | ast.Assign | ast.AnnAssign):
            continue
        targets: list[tuple[ast.expr, str, str]] = []
        if isinstance(node, ast.Call):
            function = ast.unparse(node.func)
            positional = [item for item in node.args if not isinstance(item, ast.Starred)]
            if PATCH_CALL.search(function) and positional:
                targets.append(
                    (
                        positional[0],
                        "test patches or mocks the repository's own code",
                        "drive the behaviour through the public entry point and fake only "
                        "third-party or OS boundaries",
                    )
                )
            if MOCK_CALL.search(function):
                targets.extend(
                    (
                        item.value,
                        "test builds a mock of the repository's own code",
                        "use the real owned object through the public entry point",
                    )
                    for item in node.keywords
                    if item.arg in {"spec", "spec_set"}
                )
        else:
            assigned = node.targets if isinstance(node, ast.Assign) else [node.target]
            targets.extend(
                (
                    target.value,
                    "test replaces an attribute of the repository's own code",
                    "drive the behaviour through the public entry point instead of rebinding "
                    "owned names",
                )
                for target in assigned
                if isinstance(target, ast.Attribute)
            )
        for target, problem, repair in targets:
            origin = resolve(target, imports)
            if owned(origin):
                yield Finding(
                    path, SELF_MOCKING, f"line {node.lineno}", f"{problem} ({origin})", repair
                )


def evasion(path: str, tree: ast.Module) -> Iterator[Finding]:
    repair = "make the test pass, or waive the path with an ADR-backed exception"
    problem = "test skips, expects failure or retries instead of passing"
    for node in ast.walk(tree):
        if isinstance(node, DEFINITIONS):
            hits: list[ast.expr | ast.stmt] = [
                item for item in node.decorator_list if EVASION_DECORATOR.search(ast.unparse(item))
            ]
        elif isinstance(node, ast.Call):
            hits = [node] if EVASION_CALL.search(ast.unparse(node.func)) else []
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            hits = [node] if EVASION_MARK.search(ast.unparse(node)) else []
        else:
            continue
        for hit in hits:
            yield Finding(
                path, EVASION, f"line {hit.lineno}", f"{problem} ({snippet(hit)})", repair
            )


def property_seed(path: str, tree: ast.Module) -> Iterator[Finding]:
    for node in ast.walk(tree):
        if not isinstance(node, DEFINITIONS):
            continue
        decorators = [ast.unparse(item) for item in node.decorator_list]
        if any(GIVEN.search(text) for text in decorators) and not any(
            SEEDED.search(text) for text in decorators
        ):
            yield Finding(
                path,
                PROPERTY_SEED,
                f"line {node.lineno}",
                f"Hypothesis property test '{node.name}' neither fixes nor reports its seed",
                "add @seed(<n>), or @settings(print_blob=True) or @settings(derandomize=True)",
            )


def hook_tokens(path: str, source: str) -> Iterator[Finding]:
    """Test-marker strings and test-hook identifiers, read from the token stream."""
    for item in tokenize.generate_tokens(io.StringIO(source).readline):
        line = item.start[0]
        if item.type == token.STRING and TEST_MARKER.search(item.string):
            yield Finding(
                path,
                TEST_HOOKS,
                f"line {line}",
                f"production code reads a test marker ({item.string})",
                "remove the test-only branch, tests must exercise the production path unchanged",
            )
        elif item.type == token.NAME and HOOK_NAME.search(item.string):
            yield Finding(
                path,
                TEST_HOOKS,
                f"line {line}",
                f"production code declares a test-only hook ({item.string})",
                "remove the hook and drive the behaviour through the public entry point",
            )


def used_names(tree: ast.Module, skip: frozenset[int] = frozenset()) -> set[str]:
    """Names a module uses; ``skip`` holds ``id()`` of definition nodes to leave out."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                names.update(alias.name.split("."))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
        elif isinstance(node, DEFINITIONS) and id(node) not in skip:
            names.add(node.name)
    return names


def source_split(path: str) -> tuple[str, str] | None:
    """A ``src``-layout file's project folder and dotted module name."""
    project, separator, inner = path.partition("/src/")
    if not separator or not inner.endswith(".py"):
        return None
    parts = inner.removesuffix(".py").split("/")
    return project, ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def object_references(texts: Iterator[str]) -> set[tuple[str, str]]:
    """``(module, symbol)`` for every text that is exactly a ``module:Symbol`` reference."""
    return {(match[1], match[2]) for text in texts if (match := OBJECT_REFERENCE.match(text))}


def project_entry_points(pyproject: Path) -> list[str]:
    """The script and entry-point values one ``pyproject.toml`` declares (none when absent)."""
    try:
        metadata = tomllib.loads(pyproject.read_text())["project"]
    except (OSError, KeyError):
        return []
    tables = [metadata.get(name, {}) for name in ENTRY_POINT_TABLES]
    tables.extend(metadata.get("entry-points", {}).values())
    return [str(value) for table in tables for value in table.values()]


def entry_point_references(root: Path, projects: set[str]) -> set[tuple[str, str]]:
    """Objects each project's ``pyproject.toml`` names as scripts or entry points."""
    values = (
        value
        for project in sorted(projects)
        for value in project_entry_points(root / project / "pyproject.toml")
    )
    return object_references(values)


def click_registered(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(item, ast.Call) and bool(CLICK_REGISTRATION.search(ast.unparse(item.func)))
        for item in node.decorator_list
    )


def externally_loaded(root: Path, production: dict[str, ast.Module]) -> set[tuple[str, str]]:
    """``(path, symbol)`` pairs a third party loads by name, so no production code names them."""
    strings = (
        node.value
        for tree in production.values()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    modules = {path: split for path in production if (split := source_split(path))}
    named = object_references(strings) | entry_point_references(
        root, {project for project, _ in modules.values()}
    )
    loaded = {
        (path, node.name)
        for path, tree in production.items()
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and click_registered(node)
    }
    loaded.update(
        (path, symbol)
        for path, (_, module) in modules.items()
        for reference, symbol in named
        if reference == module
    )
    return loaded


def symbols_only_tests_use(
    root: Path, production: dict[str, ast.Module], tests: dict[str, ast.Module]
) -> list[Finding]:
    """Public top-level production symbols that tests name and no other production file does."""
    defined: list[tuple[str, str, int]] = []
    production_uses: set[str] = set()
    loaded = externally_loaded(root, production)
    for path, tree in production.items():
        own = frozenset(id(node) for node in tree.body if isinstance(node, DEFINITIONS))
        production_uses |= used_names(tree, own)
        defined.extend(
            (path, node.name, node.lineno)
            for node in tree.body
            if isinstance(node, DEFINITIONS)
            and not node.name.startswith("_")
            and (path, node.name) not in loaded
        )
    test_uses: dict[str, str] = {}
    for path, tree in sorted(tests.items()):
        for name in used_names(tree):
            test_uses.setdefault(name, path)
    return [
        Finding(
            path,
            TEST_HOOKS,
            f"line {line}",
            f"production symbol '{name}' is used only by tests ({test_uses[name]})",
            "drive the behaviour through a public entry point and delete the symbol, "
            "or give it a production caller",
        )
        for path, name, line in defined
        if name in test_uses and name not in production_uses and name not in ENTRY_POINT_SYMBOLS
    ]


def parse(root: Path, path: str) -> tuple[str, ast.Module] | Finding:
    try:
        source = (root / path).read_text(encoding="utf-8")
        return source, ast.parse(source, filename=path)
    except (OSError, UnicodeError, SyntaxError, ValueError) as error:
        return Finding(path, "PYS000", "line 1", str(error), "restore readable UTF-8 Python")


def rule_findings(root: Path, inventory: Sequence[str]) -> list[Finding]:
    """Every rule's findings over the listed Python files, before exceptions apply."""
    found: list[Finding] = []
    production: dict[str, ast.Module] = {}
    tests: dict[str, ast.Module] = {}
    for path in inventory:
        test = is_test_file(path)
        if is_fixture_program(path) or (not test and not is_production_file(path)):
            continue
        parsed = parse(root, path)
        if isinstance(parsed, Finding):
            found.append(parsed)
            continue
        source, tree = parsed
        if test:
            tests[path] = tree
            found.extend(self_mocking(path, tree, import_map(tree)))
            found.extend(evasion(path, tree))
            found.extend(property_seed(path, tree))
        else:
            production[path] = tree
            found.extend(hook_tokens(path, source))
    found.extend(symbols_only_tests_use(root, production, tests))
    return found


def load_exceptions(root: Path) -> list[Waiver]:
    document = json.loads((root / EXCEPTIONS).read_text(encoding="utf-8"))
    return [Waiver(item["path"], item["rule"], item["adr"]) for item in document["exceptions"]]


def waives(entry: Waiver, item: Finding) -> bool:
    return entry.rule == item.rule and (
        item.path == entry.path or fnmatchcase(item.path, entry.path)
    )


def exception_findings(
    root: Path, entries: Sequence[Waiver], found: Sequence[Finding]
) -> list[Finding]:
    """Entries with an unknown rule, a missing ADR, or nothing to waive."""
    problems = []
    for index, entry in enumerate(entries, start=1):
        adr = entry.adr.split(" ", 1)[0]
        if entry.rule not in RULES:
            problem = f"exception for {entry.path} names unknown rule {entry.rule}"
        elif not adr.startswith("docs/adr/") or not (root / adr).is_file():
            problem = f"exception for {entry.path} names missing ADR {entry.adr}"
        elif not any(waives(entry, item) for item in found):
            problem = f"exception for {entry.path} waives no {entry.rule} finding"
        else:
            continue
        problems.append(
            Finding(
                EXCEPTIONS,
                EXCEPTIONS_RULE,
                f"entry {index}",
                problem,
                "remove or correct the entry",
            )
        )
    return problems


def findings(root: Path, inventory: Sequence[str]) -> list[Finding]:
    found = rule_findings(root, inventory)
    entries = load_exceptions(root)
    kept = [item for item in found if not any(waives(entry, item) for entry in entries)]
    return [*kept, *exception_findings(root, entries, found)]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    found = findings(args.repo_root, python_paths(args.repo_root))
    for item in found:
        print(item.render())
    return int(bool(found))


if __name__ == "__main__":
    raise SystemExit(main())
