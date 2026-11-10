#!/usr/bin/env python3
"""Check maintained Python inventory and ratchet the complete strict mypy scan."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]


def maintained(path: PurePosixPath) -> bool:
    """Whether a path belongs to the maintained Python inventory."""
    parts = path.parts
    # This exact upstream program is vendored and hash-checked, not maintained.
    if path == PurePosixPath(
        "scripts/vendor/setup-repository-guardrails/validate_rule_coverage.py"
    ):
        return False
    if "fixtures" in parts or parts[:1] in (("docs",), ("var",)):
        return False
    return any(
        path == root or root in path.parents
        for root in map(
            PurePosixPath,
            ("orchestrator/src", "orchestrator/tests", "contract/src", "scripts"),
        )
    ) or (len(parts) >= 6 and parts[:2] == ("lab", "stages") and parts[4:6] == ("tool", "src"))


def coverage_findings(discovered: Sequence[str], targets: Sequence[str]) -> list[str]:
    """Report each omitted maintained root or package without filesystem access."""
    selected = tuple(map(PurePosixPath, targets))
    return [
        f"{path}: MYPY.package-coverage: maintained root or package is omitted; "
        "add it or an ancestor to tool.mypy.files"
        for path in sorted(set(discovered))
        if maintained(PurePosixPath(path))
        and not any(
            target == PurePosixPath(path) or target in PurePosixPath(path).parents
            for target in selected
        )
    ]


def inventory(root: Path) -> list[str]:
    """Discover maintained roots independently from the configured target list."""
    roots = [
        root / path
        for path in (
            "orchestrator/src",
            "orchestrator/tests",
            "contract/src",
            "scripts",
        )
    ]
    roots.extend(root.glob("lab/stages/*/*/tool/src"))
    discovered = []
    for source in roots:
        if source.is_dir():
            discovered.append(source.relative_to(root).as_posix())
            discovered.extend(
                path.parent.relative_to(root).as_posix() for path in source.rglob("__init__.py")
            )
    return discovered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    config = args.config or root / "orchestrator/pyproject.toml"
    try:
        settings = tomllib.loads(config.read_text())["tool"]["mypy"]
        targets = [
            (config.parent / path).resolve().relative_to(root).as_posix()
            for path in settings["files"]
        ]
        findings = coverage_findings(inventory(root), targets)
        if findings:
            print("\n".join(findings), file=sys.stderr)
            return 1
        # A declared root can be absent; independently discovered roots must still be covered.
        targets = [path for path in targets if (root / path).exists()]
        scan = subprocess.run(
            [
                sys.executable,
                "-m",
                "mypy",
                "--config-file",
                str(config),
                "--no-incremental",
                "--follow-imports=skip",
                "--no-pretty",
                "--no-color-output",
                *targets,
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if scan.stderr:
            print(scan.stderr, file=sys.stderr, end="")
        if scan.returncode not in (0, 1):
            raise ValueError(f"mypy scan failed ({scan.returncode}): {scan.stderr or scan.stdout}")
        spec = importlib.util.spec_from_file_location(
            "finding_baseline", Path(__file__).with_name("check-finding-baseline.py")
        )
        if spec is None or spec.loader is None:
            raise ValueError("cannot load finding baseline checker")
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)

        class Sources(dict[str, str]):
            def __missing__(self, path: str) -> str:
                self[path] = (root / path).read_text()
                return self[path]

        entries = checker.parse_mypy(scan.stdout, root=str(root), sources=Sources())
        baseline = root / ".omp/baselines/mypy.json"
        if args.write_baseline:
            baseline.parent.mkdir(parents=True, exist_ok=True)
            baseline.write_text(
                json.dumps({"schemaVersion": 1, "tool": "mypy", "findings": entries}, indent=2)
                + "\n"
            )
            print(
                f"Recorded {sum(entry['count'] for entry in entries)} mypy findings "
                f"in {baseline.relative_to(root)}"
            )
            return 0
        report_dir = Path(os.environ.get("RAIDAR_VAR", root / "var")) / "tmp/quality"
        report_dir.mkdir(parents=True, exist_ok=True)
        report = report_dir / "mypy.txt"
        report.write_text(scan.stdout)
        return subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("check-finding-baseline.py")),
                "--tool",
                "mypy",
                "--root",
                str(root),
                "--baseline",
                ".omp/baselines/mypy.json",
                "--report",
                str(report),
                "--base-ref",
                "origin/main",
            ],
            cwd=root,
            check=False,
        ).returncode
    except (OSError, ValueError, KeyError) as exc:
        print(f"mypy-check: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
