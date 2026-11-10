#!/usr/bin/env python3
"""Validate maintained Python structure and class shape."""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

CLASS_MAX_LINES = 700
CLASS_MAX_PUBLIC_METHODS = 20
CLASS_MAX_INIT_ARGS = 8
CLASS_MAX_INSTANCE_ATTRIBUTES = 12

MAINTAINED_ROOTS: tuple[str, ...] = (
    "contract/src/raidar_contract/",
    "contract/conformance/",
    "orchestrator/src/raidar/",
    "orchestrator/tests/",
    "scripts/checks/",
    "scripts/release/",
    ".agents/skills/",
)
SCRIPT_ROOTS = ("scripts/checks/", "scripts/release/")
TEST_ROOTS = ("orchestrator/tests/",)
SKILL_ROOTS = (".agents/skills/",)
# The repository deliberately keeps test cases flat; support and fixture owners
# are separate groups, rather than implicitly accepting arbitrary test paths.
CANONICAL_TEST_GROUPS = {
    "behaviour": ("orchestrator/tests/test_*.py",),
    "support": ("orchestrator/tests/*_support.py", "orchestrator/tests/process_guard.py"),
    "fixtures": ("orchestrator/tests/fixtures/**", "orchestrator/tests/review_fixtures/**"),
}
SCRIPT_NAME_EXCEPTIONS = {
    "scripts/checks/check_toolchain_pins.py",
    "scripts/checks/ruff_baseline.py",
    "scripts/checks/guardrail_test_report.py",
    "scripts/checks/test_budget_evidence.py",
}
GENERIC_NAME_ALLOWLIST: set[str] = set()
SOURCE_ROOTS = ("orchestrator/src/raidar/", "contract/src/raidar_contract/", "lab/stages/")
DYNAMIC_IMPORT_ROOTS = SOURCE_ROOTS
ADAPTER_IMPORT_ALLOWLIST = {
    "orchestrator/src/raidar/grading/runtime/raidar_oracle_plugin.py",
    "orchestrator/src/raidar/scoring/code_quality/measurement.py",
    "orchestrator/src/raidar/scoring/framework/registry.py",
}
CONTRACT_FILES = {
    "contract/src/raidar_contract/agent.py",
    "contract/src/raidar_contract/call.py",
    "orchestrator/src/raidar/judge/protocol.py",
    "orchestrator/src/raidar/subject/agent/protocol.py",
    "orchestrator/src/raidar/scoring/framework/context.py",
}
# These exact annotations carry untyped JSON records, not executable collaborators.
# Bindings and spelling are both pinned: a new field/parameter is never exempt.
CONTRACT_DATA_ANNOTATIONS = {
    "orchestrator/src/raidar/judge/protocol.py": {
        # Parser inputs are untrusted JSON values, refined by the parser itself.
        "parse_questions.raw": "Any",
        "parse_answers.raw": "Any",
    },
    "orchestrator/src/raidar/scoring/framework/context.py": {
        # Recorded agent results and scorer/problem configuration are JSON documents.
        "StepInputs.agent_result": "Mapping[str, Any] | None",
        "AssessContext.config": "Mapping[str, Any]",
        "AssessContext.problem_config": "Mapping[str, Any]",
    },
    "orchestrator/src/raidar/subject/agent/protocol.py": {
        # Harness isolation is serialized evidence, never an injected collaborator.
        "NativeAgent.harness_isolation": "dict[str, Any] | None",
    },
}
LOOP_DEPTH_ROOTS = SOURCE_ROOTS + SCRIPT_ROOTS
# Exact process seam owners. Nested functions do not inherit approval.
SEAM_SUBPROCESS_WRAPPERS: dict[str, set[str]] = {
    "orchestrator/src/raidar/cli/agent.py": {"agent_setup_auth"},
    "orchestrator/src/raidar/cli/env_harbor.py": {"compose_preflight_reason", "env_setup"},
    "orchestrator/src/raidar/cli/process.py": {"run_or_raise"},
    "orchestrator/src/raidar/cli/repo_state.py": {
        "repo_paths_from_git_cmd",
        "repo_name_status_from_git_cmd",
        "has_unstaged_changes",
    },
    "orchestrator/src/raidar/cli/stage_lock.py": {"_uv"},
    "orchestrator/src/raidar/design/problem/variant_shim.py": {"main"},
    "orchestrator/src/raidar/engine/job/detach.py": {"launch", "supervise"},
    "orchestrator/src/raidar/engine/target/transport.py": {"run_command", "spawn_detached"},
    "orchestrator/src/raidar/engine/trial/cleanup.py": {"_run"},
    "orchestrator/src/raidar/engine/trial/environment.py": {
        "RunScopedDockerEnvironment._resolve_main_container",
        "RunScopedDockerEnvironment._run_engine_command",
        "RunScopedDockerEnvironment.download_dir_with_exclusions",
    },
    "orchestrator/src/raidar/grading/closure.py": {"run_docker"},
    "orchestrator/src/raidar/grading/resolver.py": {"UvResolver.identity", "UvResolver._compile"},
    "orchestrator/src/raidar/grading/sandbox.py": {
        "DockerCopySandbox._exec",
        "DockerCopySandbox._start_and_wait",
        "sweep_grader_orphans",
    },
    "orchestrator/src/raidar/scoring/code_quality/measurement.py": {"run_process"},
    "orchestrator/src/raidar/subject/release_check.py": {"_git"},
    "orchestrator/src/raidar/subject/environment/python/image.py": {"run_docker"},
    "orchestrator/src/raidar/subject/environment/python/provisioner.py": {"run_command"},
    "orchestrator/src/raidar/subject/primitive/gate_tool.py": {
        "_git",
        "_spawn",
        "prerequisites",
        "_version",
    },
    "orchestrator/src/raidar/subject/primitive/payload/install_tree.py": {"_run"},
    "orchestrator/src/raidar/subject/workflow/package_checks.py": {"uv_lock_stale"},
    "orchestrator/src/raidar/subject/workflow/stage_container.py": {"PackContainer._run"},
    "lab/stages/raidar/verify/tool/src/verify_tool/checks.py": {"run"},
    "scripts/checks/check-finding-baseline.py": {"main"},
    "scripts/checks/check-mypy-coverage.py": {"main"},
    "scripts/checks/check-ruff-suppressions.py": {"main"},
    "scripts/checks/ruff_baseline.py": {"main"},
    "scripts/checks/check-python-fanout.py": {"_run_git"},
    "scripts/checks/check-python-quality.py": {"_check_baseline"},
    "scripts/checks/check-structure.py": {"repo_paths"},
    "scripts/checks/check-test-evidence.py": {"_git_bytes"},
    "scripts/checks/run-budgeted-fast.py": {
        "machine_details",
        "run_pytest",
        "current_revision",
    },
    "scripts/checks/test_budget_evidence.py": {"_run"},
    "scripts/checks/check_toolchain_pins.py": {"_registry_resolver_violations"},
    "scripts/release/bump-version.py": {
        "git_output",
        "get_commits_since_last_bump",
        "render_lock_update",
    },
    "scripts/release/verify-version-wiring.py": {"_git"},
    "scripts/checks/check-toolchain-parity.py": {"run_process"},
    "scripts/checks/run-affected-seams.py": {"main"},
    "scripts/checks/check-repository-paths.py": {"main", "dependency_findings"},
    "scripts/checks/record-host-readback.py": {"main"},
    "scripts/checks/check-hook-installation.py": {"run_process"},
    "scripts/checks/check-gate-rule-canary.py": {"run_process"},
    "scripts/checks/validate-rule-coverage.py": {"main"},
    "scripts/checks/check-tool-rule-canary.py": {"analyzer_canary"},
    "scripts/checks/check-bespoke-rule-canary.py": {"run_fanout_checker"},
}
EXCLUDED_PREFIXES = (
    # Exact upstream bytes and canary inputs are not maintained source.
    "scripts/vendor/setup-repository-guardrails/validate_rule_coverage.py",
    "orchestrator/tests/fixtures/guardrails/",
    ".cache/",
    ".git/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".venv/",
    "docs/",
    "lab/experiments/",
    "orchestrator/.cache/",
    "lab/stages/raidar/verify/fixtures/",
    "orchestrator/.venv/",
    "var/",
)
GENERIC_NAME_PARTS = {"common", "helpers", "misc", "stuff", "temp"}
ALLOWED_TEST_ROOT_FILES = {
    "orchestrator/tests/__init__.py",
    "orchestrator/tests/conftest.py",
}
SCRIPT_NAME_ALLOWLIST = {
    "check-test-budgets.py",
    "run-budgeted-fast.py",
    "check-rule-evidence.py",
    "validate-rule-coverage.py",
    "check-hook-installation.py",
    "check-gate-rule-canary.py",
    "record-host-readback.py",
    "check-tool-rule-canary.py",
    "check-bespoke-rule-canary.py",
    "bump-version.py",
    "check-identity.py",
    "check-finding-baseline.py",
    "check-ruff-suppressions.py",
    "check-mypy-coverage.py",
    "check-python-fanout.py",
    "check-python-quality.py",
    "check-structure.py",
    "check-test-hygiene.py",
    "check-repository-paths.py",
    "run-repository-gate.py",
    "verify-version-wiring.py",
    "check_toolchain_pins.py",
    "check-test-evidence.py",
    "check-toolchain-parity.py",
    "check-analyzer-scope.py",
    "run-affected-seams.py",
}

# V2: only these lab-owned paths may install dependencies; no workflow installs independently.
INSTALL_MODULES = frozenset(
    {
        "cli/env_harbor.py",
        "design/problem/harbor_engine.py",
        "engine/trial/workspace.py",
        "grading/closure.py",
        "subject/environment/python/provisioner.py",
        "subject/primitive/install.py",
        "subject/primitive/payload/install_tree.py",
    }
)
INSTALL_COMMAND = re.compile(r"\b(?:pip\s+install|uv\s+sync)\b")
PRODUCT_PREFIX = "orchestrator/src/raidar/"
CALLS_MODULE = "subject/primitive/calls.py"


@dataclass(frozen=True)
class Diagnostic:
    code: str
    relpath: str
    line: int
    message: str

    def render(self) -> str:
        return f"{self.relpath}:{self.line}: {self.code} {self.message}"


def repo_relative_path(repo_root: Path, path: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def path_matches_prefix(relpath: str, prefix: str) -> bool:
    normalized_prefix = prefix.rstrip("/")
    return relpath == normalized_prefix or relpath.startswith(f"{normalized_prefix}/")


def is_excluded_relpath(relpath: str) -> bool:
    normalized = Path(relpath).as_posix()
    return normalized.startswith(EXCLUDED_PREFIXES) or "/__pycache__/" in f"/{normalized}/"


def _is_maintained_root(relpath: str) -> bool:
    return any(path_matches_prefix(relpath, root) for root in MAINTAINED_ROOTS) or bool(
        re.match(r"^lab/stages/[^/]+/[^/]+/tool/src/", relpath)
    )


def is_maintained_python_relpath(relpath: str) -> bool:
    normalized = Path(relpath).as_posix()
    if not normalized.endswith(".py") or is_excluded_relpath(normalized):
        return False
    return _is_maintained_root(normalized)


def discover_maintained_python_files(repo_root: Path) -> list[Path]:
    return sorted(
        path
        for path in repo_root.rglob("*.py")
        if not is_excluded_relpath(repo_relative_path(repo_root, path))
    )


def _is_snake_case_name(name: str) -> bool:
    if name == "__init__":
        return True
    if not name or name[0].isdigit():
        return False
    return all(char == "_" or char.islower() or char.isdigit() for char in name)


def _root_diagnostics(relpath: str) -> list[Diagnostic]:
    if _is_maintained_root(relpath):
        return []
    return [
        Diagnostic(
            "PYQ100",
            relpath,
            1,
            "Python file is outside canonical maintained roots; "
            "move it into an owned root or register its owner",
        )
    ]


def _python_dir_parts(relpath: str) -> tuple[str, ...]:
    """Stage namespaces and names are data labels, not Python import packages."""
    parts = Path(relpath).parts[:-1]
    if relpath.startswith("lab/stages/"):
        return parts[:2] + parts[4:]
    if relpath.startswith("orchestrator/tests/fixtures/stages/"):
        return parts[:4] + parts[6:]
    return parts


def _generic_name_diagnostics(relpath: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    parts = Path(relpath).parts[:-1]
    # Stage namespace/name segments are data labels, not Python package names.
    label_indices: set[int] = set()
    if relpath.startswith("lab/stages/"):
        label_indices = {2, 3}
    elif relpath.startswith("orchestrator/tests/fixtures/stages/"):
        label_indices = {4, 5}
    for index, part in enumerate(parts):
        if index in label_indices:
            continue
        prefix = "/".join(parts[: index + 1])
        if part in GENERIC_NAME_PARTS and prefix not in GENERIC_NAME_ALLOWLIST:
            diagnostics.append(
                Diagnostic(
                    "PYQ103",
                    relpath,
                    1,
                    f"generic catch-all directory '{part}' is not approved; "
                    "rename it for its owner or approve its exact path",
                )
            )
    stem = Path(relpath).stem
    if stem in GENERIC_NAME_PARTS and relpath not in GENERIC_NAME_ALLOWLIST:
        diagnostics.append(
            Diagnostic(
                "PYQ104",
                relpath,
                1,
                f"generic catch-all module '{stem}' is not approved; "
                "rename it for its responsibility or approve its exact path",
            )
        )
    return diagnostics


def _module_name_diagnostics(relpath: str) -> list[Diagnostic]:
    if any(path_matches_prefix(relpath, root) for root in SCRIPT_ROOTS):
        return []
    stem = Path(relpath).stem
    if stem.startswith("test_") or _is_snake_case_name(stem):
        return []
    return [
        Diagnostic(
            "PYQ107",
            relpath,
            1,
            f"Python module '{Path(relpath).name}' must use snake_case",
        )
    ]


def _package_dir_diagnostics(relpath: str) -> list[Diagnostic]:
    if any(path_matches_prefix(relpath, root) for root in SKILL_ROOTS):
        return []
    diagnostics: list[Diagnostic] = []
    ignored_parts = {
        "checks",
        "orchestrator",
        "raidar",
        "release",
        "scripts",
        "src",
        "tests",
    }
    parts = _python_dir_parts(relpath)
    for part in parts:
        if part in ignored_parts or _is_snake_case_name(part):
            continue
        diagnostics.append(
            Diagnostic(
                "PYQ105",
                relpath,
                1,
                f"Python package directory '{part}' must use snake_case",
            )
        )
    return diagnostics


def structure_diagnostics_for_relpath(relpath: str) -> list[Diagnostic]:
    normalized = Path(relpath).as_posix()
    if is_excluded_relpath(normalized):
        return []
    diagnostics = _root_diagnostics(normalized)
    if diagnostics:
        return diagnostics
    if normalized in ALLOWED_TEST_ROOT_FILES:
        return []
    diagnostics.extend(_policy_path_diagnostics(normalized))
    diagnostics.extend(_package_dir_diagnostics(normalized))
    diagnostics.extend(_module_name_diagnostics(normalized))
    diagnostics.extend(_generic_name_diagnostics(normalized))
    return diagnostics


def _policy_path_diagnostics(relpath: str) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    name = Path(relpath).name
    if (
        any(path_matches_prefix(relpath, root) for root in SCRIPT_ROOTS)
        and relpath not in SCRIPT_NAME_EXCEPTIONS
        and (
            name not in SCRIPT_NAME_ALLOWLIST
            or re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\.py", name) is None
        )
    ):
        findings.append(
            Diagnostic(
                "PYQ101",
                relpath,
                1,
                "command is not approved kebab-case; rename it and register its command basename",
            )
        )
    if any(path_matches_prefix(relpath, root) for root in TEST_ROOTS) and not any(
        fnmatchcase(relpath, pattern)
        and ("**" in pattern or relpath.count("/") == pattern.count("/"))
        for patterns in CANONICAL_TEST_GROUPS.values()
        for pattern in patterns
    ):
        findings.append(
            Diagnostic(
                "PYQ102",
                relpath,
                1,
                "test path has no canonical group; "
                "move it into an owned group or map its behaviour",
            )
        )
    for root in SKILL_ROOTS:
        if not path_matches_prefix(relpath, root):
            continue
        skill = relpath.removeprefix(root.rstrip("/") + "/").split("/")[0]
        if re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", skill) is None:
            findings.append(
                Diagnostic(
                    "PYQ106",
                    relpath,
                    1,
                    f"skill directory '{skill}' is not kebab-case; rename the skill directory",
                )
            )
    return findings


def placement_problems(relpath: str, tree: ast.Module) -> list[str]:
    """File-level test ownership and tier markers shared by repository checks."""
    functions = [
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    problems = []
    if any(node.name.startswith("test_") for node in functions) and not (
        relpath.startswith(("orchestrator/tests/", "contract/conformance/"))
        or re.match(r"^lab/stages/[^/]+/[^/]+/tests/", relpath)
    ):
        problems.append("test functions live outside an owned test directory")
    tier_markers = {
        f"pytest.mark.{tier}" for tier in ("slow", "quality", "docker", "security", "perf")
    }
    if relpath.startswith("orchestrator/tests/"):
        for node in functions:
            for decorator in node.decorator_list:
                marker = decorator.func if isinstance(decorator, ast.Call) else decorator
                if ast.unparse(marker) in tier_markers:
                    problems.append(f"{ast.unparse(marker)} decorates {node.name}")
    return problems


def _skill_directory_selected(directory: Path, selected: Sequence[Path]) -> bool:
    return not selected or any(
        scope == directory or scope in directory.parents or directory in scope.parents
        for scope in selected
    )


def _skill_directory_diagnostics(repo_root: Path, raw_paths: Sequence[str]) -> list[Diagnostic]:
    selected = [(repo_root / raw).resolve() for raw in raw_paths]
    findings: list[Diagnostic] = []
    for root in SKILL_ROOTS:
        skill_root = repo_root / root
        if not skill_root.is_dir():
            continue
        for directory in sorted(skill_root.iterdir()):
            if not directory.is_dir():
                continue
            if _skill_directory_selected(directory, selected):
                findings.extend(_policy_path_diagnostics(repo_relative_path(repo_root, directory)))
    return findings


def structure_diagnostics(repo_root: Path, paths: Sequence[Path]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for path in paths:
        diagnostics.extend(structure_diagnostics_for_relpath(repo_relative_path(repo_root, path)))
    return diagnostics


def _public_methods(
    class_node: ast.ClassDef,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    ]


def _init_arg_count(method: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    args = list(method.args.posonlyargs) + list(method.args.args)
    if args and args[0].arg == "self":
        args = args[1:]
    return len(args) + len(method.args.kwonlyargs)


def _self_attributes(class_node: ast.ClassDef) -> set[str]:
    attributes: set[str] = set()
    for node in ast.walk(class_node):
        if not isinstance(node, ast.Attribute):
            continue
        if not isinstance(node.ctx, ast.Store):
            continue
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            attributes.add(node.attr)
    return attributes


def _class_shape_diagnostics(class_node: ast.ClassDef, relpath: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    line_count = (
        (getattr(class_node, "end_lineno", class_node.lineno) or class_node.lineno)
        - class_node.lineno
        + 1
    )
    if line_count > CLASS_MAX_LINES:
        diagnostics.append(
            Diagnostic(
                "PYQ200",
                relpath,
                class_node.lineno,
                f"class '{class_node.name}' has {line_count} lines > {CLASS_MAX_LINES}",
            )
        )
    public_methods = _public_methods(class_node)
    if len(public_methods) > CLASS_MAX_PUBLIC_METHODS:
        diagnostics.append(
            Diagnostic(
                "PYQ201",
                relpath,
                class_node.lineno,
                f"class '{class_node.name}' has {len(public_methods)} public methods "
                f"> {CLASS_MAX_PUBLIC_METHODS}",
            )
        )
    attributes = _self_attributes(class_node)
    if len(attributes) > CLASS_MAX_INSTANCE_ATTRIBUTES:
        diagnostics.append(
            Diagnostic(
                "PYQ203",
                relpath,
                class_node.lineno,
                f"class '{class_node.name}' assigns {len(attributes)} instance attributes "
                f"> {CLASS_MAX_INSTANCE_ATTRIBUTES}",
            )
        )
    return diagnostics


def _constructor_diagnostics(class_node: ast.ClassDef, relpath: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for method in class_node.body:
        if not isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if method.name != "__init__":
            continue
        arg_count = _init_arg_count(method)
        if arg_count > CLASS_MAX_INIT_ARGS:
            diagnostics.append(
                Diagnostic(
                    "PYQ202",
                    relpath,
                    method.lineno,
                    f"constructor for '{class_node.name}' has {arg_count} parameters "
                    f"> {CLASS_MAX_INIT_ARGS}",
                )
            )
    return diagnostics


def class_interface_diagnostics_for_source(source: str, relpath: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    tree = ast.parse(source, filename=relpath)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        diagnostics.extend(_class_shape_diagnostics(node, relpath))
        diagnostics.extend(_constructor_diagnostics(node, relpath))
    diagnostics.extend(_seam_diagnostics(tree, relpath))
    diagnostics.extend(_contract_diagnostics(tree, relpath))
    return diagnostics


def _import_names(tree: ast.AST) -> dict[str, str]:
    """Resolve aliases without mistaking unrelated .run methods for processes."""
    names: dict[str, str] = {}
    pending = list(ast.iter_child_nodes(tree))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        pending.extend(ast.iter_child_nodes(node))
        if isinstance(node, ast.Import):
            for alias in node.names:
                names[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def _qualified_name(node: ast.AST, names: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return names.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return f"{_qualified_name(node.value, names)}.{node.attr}"
    return ""


def _seam_diagnostics(tree: ast.AST, relpath: str) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    imported = _import_names(tree)
    process_scope = any(path_matches_prefix(relpath, root) for root in SOURCE_ROOTS + SCRIPT_ROOTS)
    dynamic_scope = any(path_matches_prefix(relpath, root) for root in DYNAMIC_IMPORT_ROOTS)
    allowed = SEAM_SUBPROCESS_WRAPPERS.get(relpath, set())
    process_calls = {
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.getoutput",
        "subprocess.getstatusoutput",
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
        "os.system",
        "os.popen",
    }

    def visit(node: ast.AST, names: dict[str, str], owner: str = "") -> None:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = f"{owner}.{node.name}" if owner else node.name
            names = names | _import_names(node)
        elif isinstance(node, ast.Lambda):
            owner = f"{owner}.<lambda>"
        if isinstance(node, ast.Call):
            name = _qualified_name(node.func, names)
            if process_scope and name in process_calls and owner not in allowed:
                findings.append(
                    Diagnostic(
                        "PYQ220",
                        relpath,
                        node.lineno,
                        f"{name} is outside an approved process wrapper; "
                        "move the call into an exact mapped seam function",
                    )
                )
            if (
                dynamic_scope
                and relpath not in ADAPTER_IMPORT_ALLOWLIST
                and name
                in {
                    "importlib.import_module",
                    "__import__",
                    "builtins.__import__",
                }
            ):
                findings.append(
                    Diagnostic(
                        "PYQ221",
                        relpath,
                        node.lineno,
                        "dynamic import is outside an approved adapter; "
                        "use a static import or register the exact adapter module",
                    )
                )
        for child in ast.iter_child_nodes(node):
            visit(child, names, owner)

    visit(tree, imported)
    return findings


def _annotation_tree(annotation: ast.AST) -> ast.AST:
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            return ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return annotation
    return annotation


def _contract_aliases(tree: ast.Module) -> dict[str, ast.AST]:
    aliases: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                aliases[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            aliases[node.target.id] = node.value
        elif isinstance(node, ast.TypeAlias) and isinstance(node.name, ast.Name):
            aliases[node.name.id] = node.value
    return aliases


def _annotation_contains(
    annotation: ast.AST,
    names: dict[str, str],
    aliases: dict[str, ast.AST],
    wanted: set[str],
    seen: frozenset[str] = frozenset(),
) -> bool:
    annotation = _annotation_tree(annotation)
    for part in ast.walk(annotation):
        if _qualified_name(part, names) in wanted:
            return True
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            parsed = _annotation_tree(part)
            if parsed is not part and _annotation_contains(parsed, names, aliases, wanted, seen):
                return True
        if (
            isinstance(part, ast.Name)
            and part.id in aliases
            and part.id not in seen
            and _annotation_contains(aliases[part.id], names, aliases, wanted, seen | {part.id})
        ):
            return True
    return False


def _callable_arguments(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.arg]:
    return [
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
        *([node.args.vararg] if node.args.vararg else []),
        *([node.args.kwarg] if node.args.kwarg else []),
    ]


def _collaborator_diagnostics(
    annotation: ast.AST,
    binding: str,
    relpath: str,
    names: dict[str, str],
    aliases: dict[str, ast.AST],
) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    if _annotation_contains(
        annotation, names, {}, {"typing.Callable", "collections.abc.Callable", "Callable"}
    ):
        findings.append(
            Diagnostic(
                "PYQ230",
                relpath,
                getattr(annotation, "lineno", 1),
                "collaborator annotation uses an inline Callable; "
                "define and use a named callable alias",
            )
        )
    data_annotation = CONTRACT_DATA_ANNOTATIONS.get(relpath, {}).get(binding)
    is_json_data = data_annotation == ast.unparse(_annotation_tree(annotation))
    if not is_json_data and _annotation_contains(annotation, names, aliases, {"typing.Any", "Any"}):
        findings.append(
            Diagnostic(
                "PYQ231",
                relpath,
                getattr(annotation, "lineno", 1),
                "collaborator annotation contains Any; "
                "use a concrete type or a capability protocol",
            )
        )
    return findings


def _contract_diagnostics(tree: ast.Module, relpath: str) -> list[Diagnostic]:
    if relpath not in CONTRACT_FILES:
        return []
    names = _import_names(tree)
    aliases = _contract_aliases(tree)
    findings: list[Diagnostic] = []

    def visit(node: ast.AST, owner: str = "") -> None:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = f"{owner}.{node.name}" if owner else node.name
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in _callable_arguments(node):
                if arg.annotation:
                    findings.extend(
                        _collaborator_diagnostics(
                            arg.annotation, f"{owner}.{arg.arg}", relpath, names, aliases
                        )
                    )
            return
        if isinstance(node, ast.AnnAssign) and owner:
            findings.extend(
                _collaborator_diagnostics(
                    node.annotation, f"{owner}.{ast.unparse(node.target)}", relpath, names, aliases
                )
            )
        for child in ast.iter_child_nodes(node):
            visit(child, owner)

    visit(tree)
    return findings


def scalability_diagnostics_for_source(source: str, relpath: str) -> list[Diagnostic]:
    """Bound syntactic loop depth; independent callable bodies start at zero."""
    if not any(path_matches_prefix(relpath, root) for root in LOOP_DEPTH_ROOTS):
        return []
    findings: list[Diagnostic] = []

    def visit(node: ast.AST, depth: int) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            depth = 0
        increment = 0
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            increment = 1
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            increment = len(node.generators)
        nested = depth + increment
        if depth <= 2 < nested:
            findings.append(
                Diagnostic(
                    "PYS250",
                    relpath,
                    getattr(node, "lineno", 1),
                    f"loop/comprehension depth {nested} exceeds 2; "
                    "extract an independently named computation",
                )
            )
        for child in ast.iter_child_nodes(node):
            visit(child, nested)

    visit(ast.parse(source, filename=relpath), 0)
    return findings


def _command_literals(tree: ast.AST) -> list[tuple[int, str]]:
    """Inspect executable string/list literals, not prose docstrings."""
    docstrings = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                found.append((node.lineno, node.value))
        elif isinstance(node, ast.List | ast.Tuple):
            words = [item.value for item in node.elts if isinstance(item, ast.Constant)]
            if all(isinstance(word, str) for word in words):
                found.append((node.lineno, " ".join(words)))
    return found


def execution_boundary_diagnostics_for_source(source: str, relpath: str) -> list[Diagnostic]:
    """Enforce V2 install ownership and V5's single container transport boundary."""
    if not relpath.startswith(PRODUCT_PREFIX):
        return []
    module = relpath.removeprefix(PRODUCT_PREFIX)
    tree = ast.parse(source, filename=relpath)
    found = []
    if module not in INSTALL_MODULES:
        for line, literal in _command_literals(tree):
            if INSTALL_COMMAND.search(literal):
                found.append(
                    Diagnostic("PYQ300", relpath, line, "install outside allowlisted module")
                )
    if module.startswith(("subject/primitive/", "subject/workflow/")) and module != CALLS_MODULE:
        found.extend(_transport_diagnostics(tree, relpath))
    return found


def _transport_diagnostics(tree: ast.AST, relpath: str) -> list[Diagnostic]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"exec", "upload_file"}:
            continue
        receiver = node.func.value
        if isinstance(receiver, ast.Name) and receiver.id == "calls":
            continue
        found.append(
            Diagnostic(
                "PYQ301",
                relpath,
                node.lineno,
                "container calls must use primitive/calls.py",
            )
        )
    return found


def class_interface_diagnostics(repo_root: Path, paths: Sequence[Path]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for path in paths:
        relpath = repo_relative_path(repo_root, path)
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            diagnostics.append(Diagnostic("PYQ299", relpath, 1, f"could not decode source: {exc}"))
            continue
        diagnostics.extend(class_interface_diagnostics_for_source(source, relpath))
        diagnostics.extend(execution_boundary_diagnostics_for_source(source, relpath))
        diagnostics.extend(scalability_diagnostics_for_source(source, relpath))
    return diagnostics


def _selected_paths(repo_root: Path, raw_paths: Sequence[str]) -> list[Path]:
    if not raw_paths:
        return discover_maintained_python_files(repo_root)
    selected: list[Path] = []
    for raw in raw_paths:
        path = (repo_root / raw).resolve()
        if path.is_dir():
            selected.extend(
                child
                for child in path.rglob("*.py")
                if not is_excluded_relpath(repo_relative_path(repo_root, child))
            )
            continue
        if path.suffix == ".py" and not is_excluded_relpath(repo_relative_path(repo_root, path)):
            selected.append(path)
    return sorted(set(selected))


def _filter_checks(checks: Sequence[str]) -> set[str]:
    requested = set(checks)
    if "all" in requested:
        return {"structure", "class-interface"}
    return requested


def finding_records(repo_root: Path, diagnostics: Sequence[Diagnostic]) -> list[dict[str, str]]:
    """Emit source/symbol identities, independent of wording and absolute line shifts."""
    sources: dict[str, tuple[list[str], ast.Module]] = {}
    records: list[dict[str, str]] = []
    for diagnostic in diagnostics:
        if (repo_root / diagnostic.relpath).is_dir():
            records.append(
                {
                    "path": diagnostic.relpath,
                    "code": diagnostic.code,
                    "symbol": "",
                    "source_line": "",
                }
            )
            continue
        if diagnostic.relpath not in sources:
            source = (repo_root / diagnostic.relpath).read_text(encoding="utf-8")
            sources[diagnostic.relpath] = (source.splitlines(), ast.parse(source))
        lines, tree = sources[diagnostic.relpath]
        row = diagnostic.line

        def enclosing(node: ast.AST, parents: tuple[str, ...], row: int = row) -> tuple[str, ...]:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    start = min([child.lineno, *[d.lineno for d in child.decorator_list]])
                    if start <= row <= (child.end_lineno or child.lineno):
                        return enclosing(child, (*parents, child.name))
                else:
                    found = enclosing(child, parents)
                    if found != parents:
                        return found
            return parents

        records.append(
            {
                "path": diagnostic.relpath,
                "code": diagnostic.code,
                "symbol": ".".join(enclosing(tree, ())),
                "source_line": lines[row - 1],
            }
        )
    return records


def _check_baseline(repo_root: Path, diagnostics: Sequence[Diagnostic], baseline: Path) -> int:
    report = repo_root / "var/tmp/quality/python-policy-report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(finding_records(repo_root, diagnostics)), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts/checks/check-finding-baseline.py"),
            "--root",
            str(repo_root),
            "--tool",
            "python-policy",
            "--format",
            "findings-json",
            "--baseline",
            str(baseline),
            "--report",
            str(report),
            "--base-ref",
            "origin/main",
        ],
        cwd=repo_root,
        check=False,
        timeout=180,
    ).returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--format", choices=("text", "findings-json"), default="text")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument(
        "--check",
        action="append",
        choices=("all", "structure", "class-interface"),
        default=None,
    )
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)
    if args.baseline is not None and (args.format != "text" or args.paths or args.check):
        parser.error("--baseline requires a complete default scan in text format")

    repo_root = args.repo_root.resolve()
    paths = _selected_paths(repo_root, args.paths)
    checks = _filter_checks(args.check or ["all"])
    diagnostics: list[Diagnostic] = []
    if "structure" in checks:
        diagnostics.extend(structure_diagnostics(repo_root, paths))
        diagnostics.extend(_skill_directory_diagnostics(repo_root, args.paths))
    if "class-interface" in checks:
        diagnostics.extend(class_interface_diagnostics(repo_root, paths))
    if args.format == "findings-json":
        print(json.dumps(finding_records(repo_root, diagnostics)))
        return int(bool(diagnostics))
    if args.baseline is not None:
        return _check_baseline(repo_root, diagnostics, args.baseline)

    if diagnostics:
        # Each finding is independently actionable, including a single unknown root.
        for diagnostic in sorted(diagnostics, key=lambda item: item.render()):
            print(diagnostic.render(), file=sys.stderr)
        return 1
    print(f"[python-quality] OK: {len(paths)} maintained Python files checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
