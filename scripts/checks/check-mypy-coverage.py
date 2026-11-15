#!/usr/bin/env python3
"""Check maintained Python inventory and fail on any finding of the complete strict mypy scan."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from git_inventory import python_paths

ROOT = Path(__file__).resolve().parents[2]


def maintained(path: PurePosixPath) -> bool:
    """Whether a path belongs to the maintained Python inventory."""
    parts = path.parts
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
    roots = ("orchestrator/src", "orchestrator/tests", "contract/src", "scripts")
    discovered: set[str] = set()
    for path in python_paths(root):
        parts = PurePosixPath(path).parts
        source = next((prefix for prefix in roots if path.startswith(f"{prefix}/")), None)
        if len(parts) >= 7 and parts[:2] == ("lab", "stages") and parts[4:6] == ("tool", "src"):
            source = "/".join(parts[:6])
        if source is not None:
            discovered.add(source)
            if parts[-1] == "__init__.py":
                discovered.add(PurePosixPath(path).parent.as_posix())
    return sorted(discovered)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    root = args.root.absolute()
    config = args.config or root / "orchestrator/pyproject.toml"
    try:
        settings = tomllib.loads(config.read_text())["tool"]["mypy"]
        targets = [
            Path(os.path.abspath(config.parent / path)).relative_to(root).as_posix()
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
        if scan.returncode:
            print(scan.stdout, end="")
        return scan.returncode
    except (OSError, ValueError, KeyError) as exc:
        print(f"mypy-check: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
