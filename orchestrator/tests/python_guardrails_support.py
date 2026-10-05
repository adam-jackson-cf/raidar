"""Builders for python-guardrails tests: a host gate home and the verification root.

- ``host_home``: the payload's gate with this environment's Python, Ruff and
  mypy (not the pinned versions) as its venv, for behaviour that does not
  depend on a tool version (exit codes, reports, tiers, protection evidence).
- ``assemble_verify_root``: the rule tests' layout (``pack/`` is the shipped
  payload; the canary harness ``canary.py``, the runner ``run_canaries.py``,
  the fixture generator and ``specs.json`` at the root; and
  ``gate/guardrail-gate-commit.sh``, guardrail-gate@2's commit script rendered
  for the guardrails experiment's own enforced gate command). Fixtures are
  generated in the pack image (``make guardrails-verify``), where the lock
  canary's ``uv.lock`` is made by the uv the gate runs.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from raidar.components.builtins.guardrails import PAYLOAD_DIR, PAYLOAD_FILES
from raidar.components.gate_run_v2 import GateModeV2, GuardrailGateOptionsV2, gate_script
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


GATE_REF = "guardrail-gate@2"


def experiment_gate_uses() -> dict[str, dict[str, Any]]:
    """The guardrails experiment's guardrail-gate@2 ``with`` per condition."""
    spec = yaml.safe_load(GUARDRAILS_EXPERIMENT.read_text())
    return {
        condition["id"]: dict(use["with"])
        for condition in spec["conditions"]
        for use in condition["components"]
        if use["use"] == GATE_REF
    }


def experiment_gate_use(mode: str = "commit") -> dict[str, Any]:
    """The experiment's guardrail-gate@2 ``with`` in ``mode`` (commit: the enforced r2 gate)."""
    for gate in experiment_gate_uses().values():
        if gate.get("mode", "commit") == mode:
            return gate
    raise AssertionError(f"the guardrails experiment declares no {GATE_REF} use in mode {mode}")


def commit_gate_script() -> str:
    """guardrail-gate@2's commit-mode script for the experiment's enforced gate command."""
    gate = experiment_gate_use()
    options = GuardrailGateOptionsV2(
        inner_agent=InnerAgent(name="nop"),
        command=gate["command"],
        mode=GateModeV2(gate.get("mode", "commit")),
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


# ---- the rule set the canaries must cover, derived from the shipped pack ---------------

NON_CONFIG_RULES = frozenset(
    {
        "MYPY.module-overrides",
        "MYPY.package-coverage",
        "PARITY.lock-version",
        "RUNNER.local-ci-parity",
        "TEST.failure-propagation",
        "TEST.subprocess-timeouts",
        "TEST.tiers",
    }
)
"""Canaried rules that are gate behaviour, not one pack config entry or diagnostic code."""

NO_CANARY = frozenset(
    {
        "ruff.toml:lint.per-file-ignores",
        "mypy.ini:explicit_package_bases",
        "mypy.ini:disable_error_code",
    }
)
"""Pack config entries with no canary of their own (known gaps, listed so a new one fails)."""

_DIAGNOSTIC_CODE = re.compile(r"\"(PY[QS]\d{3}|FANOUT\.[\w-]+)\"")
_NO_COUNTERPART = frozenset({"PYQ100", "PYQ101", "PYQ106"})
_RUFF_SECTIONS = {
    "isort": "RUFF.ISORT.",
    "pylint": "RUFF.LIMIT.",
    "mccabe": "RUFF.SETTING.mccabe.",
    "pydocstyle": "RUFF.PYDOCSTYLE.",
}


def _ruff_entries(config: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    entries: dict[str, tuple[str, ...]] = {}
    lint = config.get("lint", {})
    for key, value in config.items():
        if not isinstance(value, dict):
            entries[f"ruff.toml:{key}"] = (f"RUFF.ROOT.{key}", f"RUFF.SETTING.{key}")
    for key in config.get("format", {}):
        entries[f"ruff.toml:format.{key}"] = (f"RUFF.FORMAT.{key}",)
    for key, value in lint.items():
        if key == "select":
            entries |= {f"ruff.toml:lint.select.{code}": (f"RUFF.SELECT.{code}",) for code in value}
        elif key == "ignore":
            entries["ruff.toml:lint.ignore"] = ("RUFF.IGNORES",)
        elif key in _RUFF_SECTIONS:
            prefix = _RUFF_SECTIONS[key]
            entries |= {f"ruff.toml:lint.{key}.{name}": (f"{prefix}{name}",) for name in value}
        else:
            entries[f"ruff.toml:lint.{key}"] = ()
    return entries


def pack_rule_entries() -> dict[str, tuple[str, ...]]:
    """Every pack config entry and diagnostic code, with the canary ids that would cover it.

    Read from the shipped payload (``ruff.toml``, ``mypy.ini`` and the gate
    library's diagnostic codes), so a rule added to the pack without a canary
    shows up as an uncovered entry instead of being missed by a hard-coded count.
    """
    import configparser
    import tomllib

    config = PAYLOAD_DIR / "config"
    entries = _ruff_entries(tomllib.loads((config / "ruff.toml").read_text()))
    mypy = configparser.ConfigParser()
    mypy.read_string((config / "mypy.ini").read_text())
    entries |= {f"mypy.ini:{key}": (f"MYPY.{key}", f"MYPY.SETTING.{key}") for key in mypy["mypy"]}
    library = PAYLOAD_DIR / "lib" / "guardrails"
    codes = {
        code
        for path in sorted(library.glob("*.py"))
        for code in _DIAGNOSTIC_CODE.findall(path.read_text())
    }
    entries |= {f"code:{code}": (code,) for code in sorted(codes - _NO_COUNTERPART)}
    return entries


def uncovered_pack_entries(specs: dict[str, Any]) -> list[str]:
    """Pack entries no canary spec covers."""
    return [
        entry
        for entry, ids in pack_rule_entries().items()
        if not any(rule in specs for rule in ids)
    ]


def implemented_rules(specs: dict[str, Any]) -> frozenset[str]:
    """The canaried rules the pack implements: every covered entry's id, plus gate behaviour."""
    covered = {rule for ids in pack_rule_entries().values() for rule in ids if rule in specs}
    return frozenset(covered) | NON_CONFIG_RULES
