#!/usr/bin/env python3
"""Verify vendored validator bytes and host freshness before executing all evidence."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "scripts/vendor/setup-repository-guardrails/validate_rule_coverage.py"
SHA256 = "4def562a709b844399bb52495cdbc43f7e407a832f046addf6288b3052b51734"


def evidence_errors(root: Path) -> list[str]:
    """Reject corrupted vendor bytes and stale or scope-less host readbacks."""
    errors = []
    source = VENDOR.read_bytes()
    if hashlib.sha256(source).hexdigest() != SHA256:
        errors.append("vendored validator differs; restore the recorded skill bytes")
    record = json.loads((root / ".omp/host-readback.json").read_text())
    measured = datetime.fromisoformat(record["query_time"])
    age = datetime.now(UTC) - measured
    if age < timedelta(0) or age > timedelta(days=30):
        errors.append("GIT.required-checks: stale host readback; run make record-host-readback")
    if (
        record.get("repository") != "adam-jackson-cf/raidar"
        or record.get("default_branch") != "main"
        or len(record.get("verified_head", "")) != 40
    ):
        errors.append("GIT.required-checks: invalid host scope; run make record-host-readback")
    return errors


def main() -> int:
    """Run the exact upstream validator, preserving its diagnostics and exit code."""
    try:
        errors = evidence_errors(ROOT)
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors = [f"invalid rule evidence; restore vendor/readback: {error}"]
    for diagnostic in errors:
        print(f"rule coverage FAIL: {diagnostic}", file=sys.stderr)
    if errors:
        return 1
    return subprocess.run(
        [sys.executable, str(VENDOR), *sys.argv[1:]],
        cwd=ROOT,
        check=False,
        timeout=120,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
