#!/usr/bin/env python3
"""Run the complete check-only Ruff scan; any finding fails."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    targets = [
        "orchestrator/src",
        "orchestrator/stubs",
        "orchestrator/tests",
        "contract/src",
        "scripts",
    ]
    targets.extend(
        str(path.relative_to(ROOT)) for path in sorted(ROOT.glob("lab/stages/*/*/tool/src"))
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-fix",
            "--config",
            "orchestrator/pyproject.toml",
            "--force-exclude",
            "--output-format=concise",
            *targets,
        ],
        cwd=ROOT,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
