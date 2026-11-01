"""Code identity of the Raidar-owned primitives (contract ids such as ``frame/1``).

A package's bytes and a workflow's definition can stay unchanged while the primitive that runs
them changes behaviour, so the lock records one ``primitive_code`` digest per primitive the arm
uses (``stage-tree/1`` identity covers the package; this covers the code that runs it).
Each digest is ``python-ast/1`` over the primitive's module *import
closure*, the container-side payload scripts, the native adapter's closure and the Harbor
release. The closure is read from the source (every ``raidar`` import, nested ones included),
so a module a primitive starts to use joins its identity without anybody listing it; a module
reached only by name at run time is covered by the closure test (``test_package_lock``).

Modules are hashed as normalised syntax trees (``raidar.contract.source_digest``): a comment or
docstring edit leaves a digest unchanged.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from functools import cache
from importlib.metadata import version
from pathlib import Path

import raidar_contract
from raidar_contract.agent import AgentName
from raidar_contract.canonical import digest_json
from raidar_contract.package.manifest import StagePackage
from raidar_contract.workflow import PackageWorkflowDefinition

from raidar.contract.source_digest import SOURCE_DIGEST_SCHEME, python_source_digest
from raidar.subject.primitive.registry import PRIMITIVE_MODULES, WORKFLOW_PRIMITIVE_MODULES

RAIDAR_DIR = Path(__file__).resolve().parents[2]
CONTRACT_DIR = Path(raidar_contract.__file__).resolve().parent
ROOT_PACKAGE = "raidar"
CONTRACT_PACKAGE = "raidar_contract"
PAYLOAD_DIR = "subject/primitive/payload"
RUNNER_ID = "runner/1"
IMPLICIT_CONTRACTS = ("frame/1", "install/1")
RUNNER_MODULES = ("raidar.subject.workflow.package", "raidar.subject.workflow.runner")
ADAPTER_SHARED = (
    "raidar.subject.agent.protocol",
    "raidar.subject.agent.adapters.base",
    "raidar.subject.agent.adapters.harbor_cli",
    "raidar.subject.agent.adapters.probe",
    "raidar.subject.agent.adapters.instructions",
)
ADAPTER_ENTRIES: Mapping[AgentName, tuple[str, ...]] = {
    AgentName.CODEX: (
        *ADAPTER_SHARED,
        "raidar.subject.agent.adapters.codex_cli",
        "raidar.subject.agent.adapters.isolated_codex",
        "raidar.subject.agent.adapters.codex_audit",
        "raidar.subject.agent.adapters.codex_auth",
    ),
    AgentName.CLAUDE_CODE: (
        *ADAPTER_SHARED,
        "raidar.subject.agent.adapters.claude_code_cli",
        "raidar.subject.agent.adapters.isolated_claude",
        "raidar.subject.agent.adapters.claude_audit",
    ),
}
"""Every module of a harness's native adapter. The isolated native agent is named by an import
path string (``native_agent_class``), which a source closure cannot follow, so each is a root."""


def _source(module: str) -> Path | None:
    """The file of ``raidar`` or ``raidar_contract`` module ``module`` (a package ``__init__``)."""
    root, _, rest = module.partition(".")
    if root == ROOT_PACKAGE:
        base = RAIDAR_DIR.parent / module.replace(".", "/")
    elif root == CONTRACT_PACKAGE:
        base = CONTRACT_DIR / rest.replace(".", "/") if rest else CONTRACT_DIR / "__init__"
    else:
        return None
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    init = base / "__init__.py"
    return init if init.is_file() else None


def _from_base(node: ast.ImportFrom, package: str) -> str:
    """The absolute module an ``import from`` statement names (relative levels resolved)."""
    if not node.level:
        return node.module or ""
    anchor = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
    return f"{anchor}.{node.module}" if node.module else anchor


def _statement_names(node: ast.AST, package: str) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}
    if isinstance(node, ast.ImportFrom):
        base = _from_base(node, package)
        return {base, *(f"{base}.{alias.name}" for alias in node.names)}
    return set()


def _imported(module: str, path: Path) -> set[str]:
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_bytes(), filename=str(path))):
        names |= _statement_names(node, package)
    return {name for name in names if name.split(".")[0] in (ROOT_PACKAGE, CONTRACT_PACKAGE)}


@cache
def import_closure(module: str) -> frozenset[str]:
    """Every ``raidar`` module (and package ``__init__``) ``module`` imports, itself included."""
    found: set[str] = set()
    pending = [module]
    while pending:
        name = pending.pop()
        if name in found:
            continue
        path = _source(name)
        if path is None:
            continue
        found.add(name)
        parent = name.rpartition(".")[0]
        if parent:
            pending.append(parent)
        pending.extend(_imported(name, path))
    return frozenset(found)


def _files(modules: Iterable[str]) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for module in modules:
        path = _source(module)
        if path is not None:
            files[_inventory_key(path)] = path
    for path in (RAIDAR_DIR / PAYLOAD_DIR).rglob("*.py"):
        files[_inventory_key(path)] = path
    return files


def _inventory_key(path: Path) -> str:
    """``subject/...`` below ``raidar/``, or ``raidar_contract/...`` for contract modules."""
    if path.is_relative_to(CONTRACT_DIR):
        return f"{CONTRACT_PACKAGE}/{path.relative_to(CONTRACT_DIR).as_posix()}"
    return path.relative_to(RAIDAR_DIR).as_posix()


def _inventory(roots: tuple[str, ...], adapter: AgentName | None) -> dict[str, Path]:
    modules: set[str] = set()
    entries = () if adapter is None else ADAPTER_ENTRIES[adapter]
    for root in (*roots, *entries):
        modules |= import_closure(root)
    return _files(modules)


@cache
def _digest(roots: tuple[str, ...], adapter: AgentName | None) -> str:
    files = _inventory(roots, adapter)
    return digest_json(
        {
            "scheme": SOURCE_DIGEST_SCHEME,
            "harbor": version("harbor"),
            "modules": {name: python_source_digest(path) for name, path in sorted(files.items())},
        }
    )


def primitive_inventory(contract: str, adapter: AgentName | None) -> tuple[str, ...]:
    """Every file (below ``raidar/``) whose bytes decide ``contract``'s identity for ``adapter``."""
    return tuple(sorted(_inventory(_roots(contract), adapter)))


def _roots(contract: str) -> tuple[str, ...]:
    if contract == RUNNER_ID:
        return RUNNER_MODULES
    try:
        return ((PRIMITIVE_MODULES | WORKFLOW_PRIMITIVE_MODULES)[contract],)
    except KeyError:
        raise ValueError(f"unsupported primitive contract {contract!r}") from None


def primitive_code(contracts: Iterable[str], adapter: AgentName) -> dict[str, str]:
    """``{contract id: digest}`` for the primitives a package arm uses, with ``frame/1``,
    ``install/1`` and the package runner always included."""
    wanted = sorted({*IMPLICIT_CONTRACTS, *contracts, RUNNER_ID})
    return {contract: _digest(_roots(contract), adapter) for contract in wanted}


def package_contracts(packages: Iterable[StagePackage]) -> set[str]:
    """The contract ids the packages require."""
    return {contract for package in packages for contract in package.manifest.requires.values()}


def workflow_contracts(definition: PackageWorkflowDefinition) -> set[str]:
    """The workflow-level primitives a package workflow opts into: its reply and judge."""
    found: set[str] = set()
    if definition.build.reply is not None:
        found.add(definition.build.reply)
    if definition.judge is not None:
        found.add("jev-judge/1")
    return found


def package_primitives(packages: Iterable[StagePackage]) -> dict[str, str]:
    """The adapter-independent code identity of the primitives ``packages`` exercise: what
    ``stage-check`` records and a trial's canary compares (the packages' fixtures run no agent)."""
    wanted = sorted({*IMPLICIT_CONTRACTS, *package_contracts(packages)})
    return {contract: _digest(_roots(contract), None) for contract in wanted}
