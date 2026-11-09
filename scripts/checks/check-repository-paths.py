#!/usr/bin/env python3
"""Apply the repository's layout, naming, dependency and test-placement policies."""
from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]


def load_policy(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/checks" / filename)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load repository policy {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


LAYOUT = load_policy("repository_layout", "check-structure.py")
QUALITY = load_policy("repository_python_quality", "check-python-quality.py")


def finding(path: str, rule: str, problem: str, repair: str) -> str:
    detail = " ".join(problem.split()).replace(";", ",")
    return f"{path}: {rule}: {detail}; {repair}"


def path_findings(root: Path, paths: Sequence[str]) -> list[str]:
    findings = []
    for raw in paths:
        relative = PurePosixPath(raw)
        if relative.is_absolute() and ".." not in relative.parts:
            try:
                relative = PurePosixPath(Path(raw).resolve().relative_to(root.resolve()).as_posix())
            except ValueError:
                pass
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            findings.append(finding(raw, "STRUCT.folder-placement", "unsafe repository path",
                                    "use a path inside the repository without '..'"))
            continue
        path = relative.as_posix()
        if relative.parts[0] in {"docs", "var"}:
            continue
        for problem in (
            LAYOUT.root_violations([path]) + LAYOUT.lab_violations([path])
            + LAYOUT.package_violations([path])
        ):
            findings.append(finding(path, "STRUCT.folder-placement", problem,
                                    "move to the allowlisted owner in check-structure.py"))
        for problem in LAYOUT.version_violations([path]):
            findings.append(finding(path, "NAME.conventions", problem,
                                    "put versions in manifest data, not paths"))
        source = root / path
        if re.match(r"^lab/stages/[^/]+/[^/]+/fixtures/", path):
            continue
        if not source.is_file() or source.suffix != ".py":
            continue
        if QUALITY.is_maintained_python_relpath(path):
            for diagnostic in QUALITY.structure_diagnostics_for_relpath(path):
                findings.append(finding(path, "NAME.conventions", diagnostic.message,
                                        "use the canonical root and snake_case"))
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=path)
        except (OSError, UnicodeError, SyntaxError) as error:
            findings.append(finding(path, "PYS000", str(error), "restore readable UTF-8 Python"))
            continue
        for problem in QUALITY.placement_problems(path, tree):
            findings.append(finding(path, "TEST.placement", problem,
                                    "use the owning test directory and file-level pytestmark"))
    return findings


def dependency_findings(paths: Sequence[str], *, run) -> list[str]:
    sources = [path for path in paths if path.startswith(("orchestrator/src/", "contract/src/"))]
    if not sources:
        return []
    command = [str(Path(sys.executable).with_name("lint-imports")), "--config",
               str(ROOT / "orchestrator/.importlinter"), "--no-cache"]
    try:
        result = run(command, cwd=ROOT / "orchestrator", text=True, capture_output=True,
                     check=False, timeout=120)
        if result.returncode == 0:
            return []
        problem = "repository import contracts failed"
    except OSError as error:
        problem = f"cannot launch import-linter: {error}"
    return [finding(path, "STRUCT.dependency-direction", problem,
                    "run lint-imports with orchestrator/.importlinter and remove forbidden edges")
            for path in sources]


def main(paths: Sequence[str], *, root: Path, run=subprocess.run) -> int:
    if not paths:
        result = run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                     cwd=root, capture_output=True, check=True)
        paths = sorted(set(result.stdout.decode().split("\0")) - {""})
    sources = []
    for path in paths:
        candidate = Path(path)
        if candidate.is_absolute():
            try:
                path = candidate.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                continue
        if ".." not in PurePosixPath(path).parts:
            sources.append(path)
    findings = path_findings(root, paths) + dependency_findings(sources, run=run)
    for line in findings:
        print(line, file=sys.stderr)
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], root=ROOT))
