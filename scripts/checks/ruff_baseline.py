#!/usr/bin/env python3
"""Run the complete check-only Ruff scan and explicitly record or enforce its baseline."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "scripts/checks/baselines/ruff.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    targets = ["orchestrator/src", "orchestrator/tests", "contract/src", "scripts"]
    targets.extend(
        str(path.relative_to(ROOT)) for path in sorted(ROOT.glob("lab/stages/*/*/tool/src"))
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-fix",
            "--config",
            "orchestrator/pyproject.toml",
            "--force-exclude",
            "--output-format=json",
            *targets,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        print(result.stderr, file=sys.stderr)
        return result.returncode
    report = ROOT / "var/tmp/quality/ruff-report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(result.stdout, encoding="utf-8")
    if args.record:
        spec = importlib.util.spec_from_file_location(
            "finding_baseline", ROOT / "scripts/checks/check-finding-baseline.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        diagnostics = json.loads(result.stdout)
        sources = {
            str(Path(item["filename"]).relative_to(ROOT)): Path(item["filename"]).read_text(
                encoding="utf-8"
            )
            for item in diagnostics
        }
        findings = module.parse_ruff(result.stdout, root=str(ROOT), sources=sources)
        destination = ROOT / BASELINE
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps({"schemaVersion": 1, "tool": "ruff", "findings": findings}, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Recorded {sum(item['count'] for item in findings)} Ruff findings")
        return 0
    return subprocess.run(
        [
            sys.executable,
            "scripts/checks/check-finding-baseline.py",
            "--tool",
            "ruff",
            "--baseline",
            BASELINE,
            "--report",
            str(report),
            "--base-ref",
            "origin/main",
        ],
        cwd=ROOT,
        check=False,
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
