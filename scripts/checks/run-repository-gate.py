#!/usr/bin/env python3
"""Execute the check-only argv declared by .omp/gate.json."""
from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


class GateConfigError(Exception):
    """The repository gate needs configuration repair."""


def check_argv(gate: object, check: str) -> list[str]:
    if check not in {"commit", "ci"}:
        raise GateConfigError("GATE.local-ci-parity: select commit or ci")
    if not isinstance(gate, dict) or gate.get("schemaVersion") != 2:
        raise GateConfigError("GATE.local-ci-parity: repair .omp/gate.json to use schemaVersion 2")
    checks = gate.get("checks")
    command = checks.get(check) if isinstance(checks, dict) else None
    if not isinstance(command, list) or not command or not all(
        isinstance(part, str) and part.strip() for part in command
    ):
        raise GateConfigError(
            f"GATE.local-ci-parity: repair checks.{check} to contain a nonempty string argv"
        )
    return command


def main(argv: Sequence[str], *, root: Path, run=subprocess.run) -> int:
    try:
        if len(argv) != 1:
            raise GateConfigError("GATE.local-ci-parity: select exactly one of commit or ci")
        command = check_argv(
            json.loads((root / ".omp/gate.json").read_text(encoding="utf-8")), argv[0]
        )
    except (OSError, ValueError, GateConfigError) as error:
        detail = " ".join(str(error).splitlines())
        if not isinstance(error, GateConfigError):
            detail = f"GATE.local-ci-parity: repair .omp/gate.json: {detail}"
        print(detail, file=sys.stderr)
        return 2
    try:
        return run(command, cwd=root, check=False).returncode
    except OSError as error:
        detail = " ".join(str(error).splitlines())
        print(f"GATE.local-ci-parity: install the declared runner: {detail}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], root=Path(__file__).resolve().parents[2]))
