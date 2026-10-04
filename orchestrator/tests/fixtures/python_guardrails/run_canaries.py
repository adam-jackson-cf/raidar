#!/usr/bin/env python3
"""Prove each python-guardrails rule: its violating canary fires and its clean one passes.

    run_canaries.py SPECS FIXTURES [--rules ID,ID,...]

SPECS is ``specs.json`` (``{rule id: {"codes": ..., "mode": ...}}``, written by
``make_fixtures.py``); FIXTURES holds ``<rule id>/pass.py`` and
``<rule id>/violate.py``. Each fixture runs through ``canary.py`` (beside this
file) against the installed pack (``$GUARDRAILS_HOME``):

- the passing fixture must exit 0;
- the violating fixture must exit 1 and print its diagnostic marker
  (``GATE-ERROR`` for the failure-propagation canary, ``CANARY-HIT`` otherwise).

One line per rule (``canary PASS <id>`` or ``canary FAIL <id>: <why>``), then a
summary line; exit 1 when any rule failed. ``--rules`` limits the run (the
negative control runs only the rules its mutant pack disables).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CANARY = HERE / "canary.py"
TIMEOUT_SEC = 120


def _marker(mode: str) -> str:
    return "GATE-ERROR" if mode == "propagation" else "CANARY-HIT"


def _run(codes: str, mode: str, fixture: Path) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(CANARY), codes or "-", str(fixture)]
    if mode != "rule":
        command += ["--mode", mode]
    return subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=TIMEOUT_SEC, cwd=HERE
    )


def check_rule(rule: str, spec: dict[str, str], fixtures: Path) -> str | None:
    """Why ``rule``'s canaries do not prove it, or None when both behave."""
    codes, mode = str(spec.get("codes") or ""), str(spec.get("mode") or "rule")
    passing, violating = fixtures / rule / "pass.py", fixtures / rule / "violate.py"
    missing = [path.name for path in (passing, violating) if not path.is_file()]
    if missing:
        return f"missing fixture(s) {', '.join(missing)}"
    clean = _run(codes, mode, passing)
    if clean.returncode != 0:
        tail = (clean.stdout + clean.stderr).strip()[-200:]
        return f"passing fixture exited {clean.returncode}: {tail}"
    fired = _run(codes, mode, violating)
    if fired.returncode == 0:
        return "violating canary passed"
    if fired.returncode != 1 or _marker(mode) not in fired.stdout:
        tail = (fired.stdout + fired.stderr).strip()[-200:]
        return f"violating fixture exited {fired.returncode} without {_marker(mode)}: {tail}"
    return None


def main(argv: list[str]) -> int:
    specs = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    fixtures = Path(argv[1]).resolve()
    rules = sorted(specs)
    if len(argv) > 3 and argv[2] == "--rules":
        wanted = [rule for rule in argv[3].split(",") if rule]
        unknown = sorted(set(wanted) - set(specs))
        if unknown:
            print(f"canary FAIL: unknown rule(s) {', '.join(unknown)}")
            return 2
        rules = sorted(wanted)
    failed = 0
    for rule in rules:
        why = check_rule(rule, specs[rule], fixtures)
        if why is None:
            print(f"canary PASS {rule}")
        else:
            failed += 1
            print(f"canary FAIL {rule}: {why}")
    print(f"canaries: {len(rules)} rule(s), {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
