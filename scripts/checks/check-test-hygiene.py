#!/usr/bin/env python3
"""Check bounded test processes, skill metadata, and registered stage evidence."""

from __future__ import annotations

import argparse
import ast
import json
import re
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml
from git_inventory import repo_paths

from raidar.subject.package.registry import load_stage_package
from raidar.subject.workflow.identity import package_primitives

ROOT = Path(__file__).resolve().parents[2]
SHA256 = re.compile(r"[0-9a-f]{64}")


def _finding(path: Path, root: Path, rule: str, fix: str) -> str:
    return f"{path.relative_to(root).as_posix()}: {rule}: {fix}"


def _positive_timeout(node: ast.AST | None) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Constant):
        return (
            isinstance(node.value, int | float)
            and not isinstance(node.value, bool)
            and node.value > 0
        )
    if isinstance(node, ast.UnaryOp) and isinstance(node.operand, ast.Constant):
        if isinstance(node.op, ast.USub) and isinstance(node.operand.value, int | float):
            return -node.operand.value > 0
        return _positive_timeout(node.operand)
    return True


def _timeout(call: ast.Call) -> ast.AST | None:
    return next((item.value for item in call.keywords if item.arg == "timeout"), None)


class ProcessWaits(ast.NodeVisitor):
    """Resolve imported process APIs without flagging unrelated run/wait methods."""

    def __init__(self) -> None:
        self.imports: dict[str, str] = {}
        self.processes: dict[str, str] = {}
        self.bounded: set[int] = set()
        self.lines: list[int] = []

    def name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return self.imports.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return f"{self.name(node.value)}.{node.attr}"
        return ""

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports[alias.asname or alias.name] = alias.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.imports[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        imports, processes = self.imports, self.processes
        self.imports, self.processes = imports.copy(), processes.copy()
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            self.processes.pop(argument.arg, None)
            self.imports.pop(argument.arg, None)
            annotation = self.name(argument.annotation) if argument.annotation else ""
            if annotation in {"subprocess.Popen", "asyncio.subprocess.Process"}:
                self.processes[argument.arg] = annotation
        self.generic_visit(node)
        self.imports, self.processes = imports, processes

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def _assign(self, target: ast.AST, value: ast.AST | None) -> None:
        if not isinstance(target, ast.Name | ast.Attribute):
            return
        if isinstance(value, ast.Await):
            value = value.value
        kind = self.name(value.func) if isinstance(value, ast.Call) else ""
        if kind in {
            "subprocess.Popen",
            "asyncio.create_subprocess_exec",
            "asyncio.create_subprocess_shell",
        }:
            self.processes[ast.unparse(target)] = kind
        else:
            self.processes.pop(ast.unparse(target), None)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._assign(node.target, node.value)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._assign(target, node.value)
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if item.optional_vars is not None:
                self._assign(item.optional_vars, item.context_expr)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = self.name(node.func)
        if name == "asyncio.wait_for":
            timeout = _timeout(node) or (node.args[1] if len(node.args) > 1 else None)
            future = (
                node.args[0]
                if node.args
                else next((item.value for item in node.keywords if item.arg == "fut"), None)
            )
            if _positive_timeout(timeout) and future is not None:
                self.bounded.update(id(child) for child in ast.walk(future))
        synchronous = name in {
            f"subprocess.{method}" for method in ("run", "call", "check_call", "check_output")
        }
        process = ""
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"wait", "communicate"}:
            if isinstance(node.func.value, ast.Name | ast.Attribute):
                process = self.processes.get(ast.unparse(node.func.value), "")
            elif isinstance(node.func.value, ast.Call):
                constructor = self.name(node.func.value.func)
                process = constructor if constructor == "subprocess.Popen" else ""
        if synchronous or process:
            bounded = self._bounded(node, process)
            if not bounded:
                self.lines.append(node.lineno)
        self.generic_visit(node)

    def _bounded(self, node: ast.Call, process: str) -> bool:
        if process.startswith("asyncio."):
            return id(node) in self.bounded
        timeout = _timeout(node)
        if timeout is None and process and isinstance(node.func, ast.Attribute):
            position = 0 if node.func.attr == "wait" else 1
            timeout = node.args[position] if len(node.args) > position else None
        return _positive_timeout(timeout)


def subprocess_findings(root: Path, inventory: Sequence[str]) -> list[str]:
    findings: list[str] = []
    paths = [
        root / relative
        for relative in inventory
        if relative.endswith(".py")
        and (
            relative.startswith("orchestrator/tests/")
            or re.match(r"^lab/stages/[^/]+/[^/]+/tests/", relative)
        )
    ]
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as error:
            findings.append(
                _finding(
                    path,
                    root,
                    "TEST.subprocess-timeouts",
                    f"repair Python syntax at line {error.lineno}",
                )
            )
            continue
        visitor = ProcessWaits()
        visitor.visit(tree)
        findings.extend(
            _finding(
                path,
                root,
                "TEST.subprocess-timeouts",
                f"line {line}: supply a positive timeout; bound async waits with asyncio.wait_for",
            )
            for line in visitor.lines
        )
    return findings


def skill_findings(root: Path, inventory: Sequence[str]) -> list[str]:
    findings: list[str] = []
    for relative in inventory:
        if not re.fullmatch(r"\.agents/skills/[^/]+/SKILL\.md", relative):
            continue
        path = root / relative
        text = path.read_text(encoding="utf-8")
        frontmatter = re.match(r"\A---\s*\n(.*?)\n---(?:\s*\n|\Z)", text, re.DOTALL)
        try:
            metadata: object = yaml.safe_load(frontmatter.group(1)) if frontmatter else None
        except yaml.YAMLError:
            metadata = None
        if (
            not isinstance(metadata, dict)
            or metadata.get("name") != path.parent.name
            or not isinstance(metadata.get("description"), str)
            or not metadata["description"].lower().startswith("use when")
        ):
            findings.append(
                _finding(
                    path,
                    root,
                    "SKILL.metadata",
                    "set mapping frontmatter name to the skill directory and "
                    "a string description beginning 'Use when'",
                )
            )
        links = re.findall(r"!?\[[^\]]*\]\(\s*<?([^\s)>]+)>?(?:\s+[^)]*)?\)", text)
        links.extend(re.findall(r"^\s*\[[^\]]+\]:\s*<?([^\s>]+)", text, re.MULTILINE))
        for link in links:
            target = urlsplit(link)
            if (
                not target.scheme
                and not target.netloc
                and target.path
                and not (path.parent / unquote(target.path)).exists()
            ):
                findings.append(
                    _finding(
                        path,
                        root,
                        "SKILL.metadata",
                        f"restore or correct missing local support link {link}",
                    )
                )
    return findings


def _json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _sha256(value: object) -> bool:
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def _installed_valid(record: object) -> bool:
    return (
        isinstance(record, dict)
        and bool(record)
        and all(
            isinstance(arch, str)
            and isinstance(item, dict)
            and _sha256(item.get("tree_digest"))
            and isinstance(item.get("wheels"), list)
            for arch, item in record.items()
        )
    )


def _canary_valid(
    record: object, digest: str, primitives: dict[str, str], outputs: set[str]
) -> bool:
    if not isinstance(record, dict):
        return False
    result = record.get("outputs")
    return (
        record.get("package") == digest
        and record.get("primitives") == primitives
        and _sha256(record.get("pack"))
        and _sha256(record.get("stdout_sha256"))
        and isinstance(result, dict)
        and set(result) == outputs
        and all(_sha256(value) for value in result.values())
    )


def _package_findings(root: Path, name: str, digest: str) -> list[str]:
    manifest = root / "lab/stages" / name / "stage.yaml"
    fix = f"run make stage-check NAME={name}"
    try:
        package = load_stage_package(manifest.parent)
    except (OSError, ValueError) as error:
        return [_finding(manifest, root, "SEAM.evidence", f"repair manifest ({error}); {fix}")]
    findings: list[str] = []
    installed = root / "lab/stage-checks" / f"{digest}.installed.json"
    if package.digest != digest:
        findings.append(
            _finding(
                installed,
                root,
                "SEAM.evidence",
                f"registered digest differs from stage tree; register current release and {fix}",
            )
        )
    if not _installed_valid(_json(installed)):
        findings.append(
            _finding(
                installed,
                root,
                "SEAM.evidence",
                f"missing or invalid architecture/tree_digest/wheels evidence; {fix}",
            )
        )
    if package.manifest.on_stop is not None:
        canary = installed.with_name(f"{digest}.json")
        if not _canary_valid(
            _json(canary),
            package.digest,
            package_primitives([package]),
            set(package.manifest.outputs),
        ):
            findings.append(
                _finding(
                    canary,
                    root,
                    "SEAM.evidence",
                    f"missing, stale runtime, or inconsistent canary/output digests; {fix}",
                )
            )
    return findings


def seam_findings(root: Path, inventory: Sequence[str]) -> list[str]:
    register = root / "lab/releases.yaml"
    if not register.exists():
        return []
    try:
        document = yaml.safe_load(register.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return [
            _finding(
                register,
                root,
                "SEAM.evidence",
                "repair the stage release register; run make stage-check NAME=<namespace/name>",
            )
        ]
    stages = document.get("stages", {}) if isinstance(document, dict) else None
    if not isinstance(stages, dict):
        return [
            _finding(
                register,
                root,
                "SEAM.evidence",
                "restore the stages mapping; run make stage-check NAME=<namespace/name>",
            )
        ]
    latest: dict[str, tuple[int, str]] = {}
    registered: set[str] = set()
    findings: list[str] = []
    for reference, digest in stages.items():
        match = re.fullmatch(r"([^@]+)@([1-9][0-9]*)", str(reference))
        if match is None or not _sha256(digest):
            findings.append(
                _finding(
                    register,
                    root,
                    "SEAM.evidence",
                    f"repair stage release {reference}; run make stage-check NAME=<namespace/name>",
                )
            )
            continue
        registered.add(digest)
        name, version = match.group(1), int(match.group(2))
        if name not in latest or version > latest[name][0]:
            latest[name] = version, digest
    for name, (_, digest) in sorted(latest.items()):
        findings.extend(_package_findings(root, name, digest))
    paths = (
        root / relative
        for relative in inventory
        if re.fullmatch(r"lab/stage-checks/[^/]+\.json", relative)
    )
    for path in paths:
        digest = path.name.removesuffix(".installed.json").removesuffix(".json")
        if digest not in registered:
            findings.append(
                _finding(
                    path,
                    root,
                    "SEAM.evidence",
                    "remove evidence not belonging to any registered stage release; "
                    "run make stage-check NAME=<namespace/name>",
                )
            )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args()
    inventory = repo_paths(args.repo_root)
    findings = [
        *subprocess_findings(args.repo_root, inventory),
        *skill_findings(args.repo_root, inventory),
        *seam_findings(args.repo_root, inventory),
    ]
    for finding in findings:
        print(finding)
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
