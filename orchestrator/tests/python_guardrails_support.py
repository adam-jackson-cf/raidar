"""Builders for python-guardrails@1 tests: a host gate home and the verification root.

- ``host_home``: the payload's gate with this environment's Python, Ruff and
  mypy (not the pinned versions) as its venv, for behaviour that does not
  depend on a tool version (exit codes, reports, tiers, protection evidence).
- ``assemble_verify_root``: the layout the rule-coverage ledger names (``pack/``
  is the shipped payload, the canary harness and validator at the root, and
  ``gate/guardrail-gate-commit.sh``, guardrail-gate@1's commit script rendered
  for the guardrails experiment's own gate command). Fixtures are generated in
  the pack image (``make guardrails-verify``), where the lock canary's
  ``uv.lock`` is made by the uv the gate runs.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from raidar.components.builtins.guardrails import PAYLOAD_DIR, PAYLOAD_FILES
from raidar.components.gate_run import GateMode, GuardrailGateOptions, gate_script
from raidar.components.review_loop import InnerAgent
from tests.experiment_spec_support import GUARDRAILS_EXPERIMENT

VERIFY_DIR = Path(__file__).resolve().parent / "fixtures" / "python_guardrails"
COMMIT_SCRIPT = "gate/guardrail-gate-commit.sh"
CANARY_GATE_DIR = PurePosixPath("/tmp/guardrails-canary-gate")


def host_home(base: Path, *, tools: tuple[str, ...] = ("python", "ruff", "mypy")) -> Path:
    """A gate home using the payload and this environment's tools (missing ones omitted)."""
    home = base / "home"
    venv = home / "venv" / "bin"
    venv.mkdir(parents=True)
    for name in ("bin", "lib", "config"):
        shutil.copytree(PAYLOAD_DIR / name, home / name)
    (home / "bin" / "guardrail-check").chmod(0o755)
    here = Path(sys.executable).parent
    for name in tools:
        (venv / name).symlink_to(here / name if name != "python" else Path(sys.executable))
    return home


def experiment_gate_use() -> dict[str, Any]:
    """The guardrails experiment's guardrail-gate@1 ``with`` (the r2 condition)."""
    spec = yaml.safe_load(GUARDRAILS_EXPERIMENT.read_text())
    for condition in spec["conditions"]:
        for use in condition["components"]:
            if use["use"] == "guardrail-gate@1":
                return dict(use["with"])
    raise AssertionError("the guardrails experiment declares no guardrail-gate@1 use")


def commit_gate_script() -> str:
    """guardrail-gate@1's commit-mode script for the experiment's gate command."""
    gate = experiment_gate_use()
    options = GuardrailGateOptions(
        inner_agent=InnerAgent(name="nop"),
        command=gate["command"],
        mode=GateMode(gate.get("mode", "commit")),
        max_rounds=gate.get("max_rounds", 3),
        timeout_sec=gate.get("timeout_sec", 600),
        work_dir=str(CANARY_GATE_DIR / "work"),
    )
    return gate_script(options, 1, CANARY_GATE_DIR / "logs" / "gate_run_1.log")


def assemble_verify_root(root: Path) -> Path:
    """Copy the payload, the harness and the rendered commit gate into ``root``."""
    for relative in PAYLOAD_FILES:
        target = root / "pack" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PAYLOAD_DIR / relative, target)
    for path in VERIFY_DIR.iterdir():
        if path.is_file():
            shutil.copy2(path, root / path.name)
    script = root / COMMIT_SCRIPT
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(commit_gate_script())
    return root
