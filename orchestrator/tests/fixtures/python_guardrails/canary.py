#!/usr/bin/env python3
"""Run the installed gate on a one-fixture workspace; fail only when the named rule fires.

    canary.py CODES FIXTURE [--mode MODE]

Runs inside the pack image against the installed pack
(``$GUARDRAILS_HOME``, default ``/opt/raidar-tools/python-guardrails``).
FIXTURE is one file. Its first lines may be ``# canary-path: rel/path.py``
(where to place it in the throwaway workspace) and ``# canary-extra:
rel/path=<text with \\n escapes>`` (extra files). CODES is a comma list of gate
codes (prefix match, e.g. ``MYPY.no-untyped-def`` or ``PLR0913``).

Modes:
- ``rule`` (default): exit 1 and print ``CANARY-HIT <code>`` when any of CODES is
  among the gate findings; exit 0 otherwise; exit 3 on a gate error.
- ``unchanged``: as ``rule``, and the fixture bytes must be unchanged after the
  gate (check-only proof); a rewritten fixture exits 5.
- ``propagation``: the fixture names a scenario (``ok`` or ``missing-mypy``); the
  gate runs with that home and the canary exits 1 printing ``GATE-ERROR`` when
  the gate exits 126, 0 when it passes, 4 when an error was reported as a pass.
- ``commit``: the step-end runner. ``gate/guardrail-gate-commit.sh`` (rendered
  from guardrail-gate@2 for the experiment's enforced gate command) commits the workspace in a
  fresh git repository with the gate as the pre-commit hook. A refused commit
  that names one of CODES exits 1 printing ``CANARY-HIT``; a recorded commit
  exits 0; anything else exits 3.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
HOME = Path(os.environ.get("GUARDRAILS_HOME", "/opt/raidar-tools/python-guardrails"))
GATE = HOME / "bin" / "guardrail-check"
GATE_SCRIPT = HERE / "gate" / "guardrail-gate-commit.sh"
TIMEOUT_SEC = 110


def _layout(fixture: Path, workspace: Path) -> Path:
    raw = fixture.read_bytes()
    rel = fixture.name
    extras: list[tuple[str, str]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines()[:6]:
        if line.startswith("# canary-path:"):
            rel = line.split(":", 1)[1].strip()
        elif line.startswith("# canary-extra:"):
            spec = line.split(":", 1)[1].strip()
            path, _, text = spec.partition("=")
            extras.append((path, text.encode().decode("unicode_escape")))
    target = workspace / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    for path, text in extras:
        (workspace / path).parent.mkdir(parents=True, exist_ok=True)
        (workspace / path).write_text(text, encoding="utf-8")
    return target


def _gate(workspace: Path, home: Path) -> tuple[int, dict[str, Any]]:
    report = workspace.parent / "report.json"
    env = {**os.environ, "GUARDRAILS_HOME": str(home)}
    env.pop("RAIDAR_GATE_REPORT_DIR", None)
    completed = subprocess.run(
        [str(home / "bin" / "guardrail-check"), "--root", str(workspace)]
        + ["--json-out", str(report)],
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT_SEC,
        env=env,
    )
    data = json.loads(report.read_text()) if report.exists() else {}
    return completed.returncode, data if isinstance(data, dict) else {}


def _broken_home(base: Path) -> Path:
    """A copy of the installed pack whose venv has no mypy."""
    home = base / "home"
    for name in ("config", "lib", "bin"):
        shutil.copytree(HOME / name, home / name)
    venv = home / "venv" / "bin"
    venv.mkdir(parents=True)
    for name in ("python", "ruff"):
        (venv / name).symlink_to((HOME / "venv" / "bin" / name).resolve())
    return home


def _propagation(fixture: Path, base: Path, workspace: Path) -> int:
    scenario = fixture.read_text().strip().splitlines()[-1].strip()
    (workspace / "ok.py").write_text("def one() -> int:\n    return 1\n")
    home = _broken_home(base) if scenario == "missing-mypy" else HOME
    rc, _ = _gate(workspace, home)
    if rc == 126:
        print("GATE-ERROR propagated as exit 126")
        return 1
    return 4 if scenario == "missing-mypy" else rc


def _commit(codes: list[str], fixture: Path, workspace: Path) -> int:
    _layout(fixture, workspace)
    completed = subprocess.run(
        ["bash", str(GATE_SCRIPT)],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT_SEC,
    )
    head, _, output = completed.stdout.partition("----raidar-guardrail-gate-output----\n")
    values = dict(line.split("=", 1) for line in head.splitlines() if "=" in line)
    rc, git_rc = values.get("rc"), values.get("git_rc")
    if rc == "0" and git_rc == "0":
        return 0
    if rc == "1" and git_rc not in (None, "0") and any(code in output for code in codes):
        print(f"CANARY-HIT commit refused by the pre-commit hook (hook rc {rc}, git rc {git_rc})")
        return 1
    print(f"commit gate did not decide: hook rc {rc!r}, git rc {git_rc!r}\n{output[-400:]}")
    return 3


def _hits(report: dict[str, Any], codes: list[str]) -> list[dict[str, Any]]:
    findings = report.get("findings")
    return [
        finding
        for finding in (findings if isinstance(findings, list) else [])
        if isinstance(finding, dict) and any(str(finding["code"]).startswith(c) for c in codes)
    ]


def main(argv: list[str]) -> int:
    codes = [code for code in argv[0].split(",") if code and code != "-"]
    fixture = Path(argv[1]).resolve()
    mode = argv[3] if len(argv) > 3 and argv[2] == "--mode" else "rule"
    with tempfile.TemporaryDirectory(prefix="guardrails-canary-") as tmp:
        base = Path(tmp)
        workspace = base / "app"
        workspace.mkdir()
        if mode == "propagation":
            return _propagation(fixture, base, workspace)
        if mode == "commit":
            return _commit(codes, fixture, workspace)
        target = _layout(fixture, workspace)
        before = target.read_bytes()
        rc, report = _gate(workspace, HOME)
        if rc == 126:
            print(f"gate error: {report.get('error')}")
            return 3
        if mode == "unchanged" and target.read_bytes() != before:
            print("CANARY-MODIFIED the gate rewrote the workspace")
            return 5
        hits = _hits(report, codes)
        for hit in hits[:5]:
            print(f"CANARY-HIT {hit['code']} {hit['relpath']}:{hit['line']} {hit['message']}")
        return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
