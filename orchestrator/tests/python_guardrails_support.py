"""Builders for python-guardrails tests: a host gate home and the verification root.

- ``host_home``: the payload's gate with this environment's Python, Ruff and
  mypy (not the pinned versions) as its venv, for behaviour that does not
  depend on a tool version (exit codes, reports, tiers, protection evidence).
- ``assemble_verify_root``: one pack's rule-test layout (``pack/`` is the
  shipped payload; the canary harness ``canary.py``, the runner
  ``run_canaries.py``, the fixture generator and the pack's specs as
  ``specs.json`` at the root; and ``gate/guardrail-gate-commit.sh``, the commit
  script of the guardrails experiment's own guardrail-gate version rendered for
  its enforced gate command). Fixtures are generated in the pack image (``make
  guardrails-verify``), where the lock canary's ``uv.lock`` is made by the uv
  the gate runs.
- ``PACKS``: the packs under verification: python-guardrails@1 (the payload of
  @1 and @2, ``specs.json``), @3 (``specs-3.json``), @4 (``specs-4.json``),
  @5 (@4's rules byte for byte, so ``specs-4.json``; its install adds the
  agent's tool links and their smokes) and @6 (``specs-6.json``: @5's plus the
  test-class canary, ``PLR0904`` being off in test files).
"""

from __future__ import annotations

import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from raidar.components.builtins import (
    guardrails,
    guardrails_v3,
    guardrails_v4,
    guardrails_v5,
    guardrails_v6,
)
from raidar.components.gate_run_v2 import GateModeV2, gate_script
from raidar.components.guardrail_gate_v3 import GuardrailGateOptionsV3
from raidar.components.session_chain import InnerAgentSpec
from tests.experiment_spec_support import GUARDRAILS_EXPERIMENT

VERIFY_DIR = Path(__file__).resolve().parent / "fixtures" / "python_guardrails"
COMMIT_SCRIPT = "gate/guardrail-gate-commit.sh"
CANARY_GATE_DIR = PurePosixPath("/tmp/guardrails-canary-gate")
SPEC_FILES = ("specs.json", "specs-3.json", "specs-4.json", "specs-6.json")


@dataclass(frozen=True)
class PackUnderTest:
    """One python-guardrails payload and the canary specs that prove it."""

    version: int
    payload_dir: Path
    payload_files: tuple[str, ...]
    specs: str
    non_config_rules: frozenset[str]

    @property
    def specs_path(self) -> Path:
        return VERIFY_DIR / self.specs


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
TESTS_CHECK_BEHAVIOUR = frozenset(
    {
        "TEST.no-change-exempt",
        "TEST.pytest-runner",
        "TEST.subprocess-coverage",
        "TEST.isolated-run",
    }
)
"""python-guardrails@3's tests check behaviour (its codes are derived from the library)."""
SCOPE_BEHAVIOUR = frozenset({"SCOPE.shebang-script", "SCOPE.py-symlink"})
"""python-guardrails@4's scope: Python files without a ``.py`` name are checked."""
TEST_CLASS_BEHAVIOUR = frozenset({"RUFF.LINT.per-file-ignores.test-public-methods"})
"""python-guardrails@6: ``PLR0904`` is off under each test-file glob, on everywhere else."""

PACKS = {
    1: PackUnderTest(
        1,
        guardrails.PAYLOAD_DIR,
        guardrails.PAYLOAD_FILES,
        "specs.json",
        NON_CONFIG_RULES,
    ),
    3: PackUnderTest(
        3,
        guardrails_v3.PAYLOAD_DIR,
        guardrails_v3.PAYLOAD_FILES,
        "specs-3.json",
        NON_CONFIG_RULES | TESTS_CHECK_BEHAVIOUR,
    ),
    4: PackUnderTest(
        4,
        guardrails_v4.PAYLOAD_DIR,
        guardrails_v4.PAYLOAD_FILES,
        "specs-4.json",
        NON_CONFIG_RULES | TESTS_CHECK_BEHAVIOUR | SCOPE_BEHAVIOUR,
    ),
    5: PackUnderTest(
        5,
        guardrails_v5.PAYLOAD_DIR,
        guardrails_v5.PAYLOAD_FILES,
        "specs-4.json",
        NON_CONFIG_RULES | TESTS_CHECK_BEHAVIOUR | SCOPE_BEHAVIOUR,
    ),
    6: PackUnderTest(
        6,
        guardrails_v6.PAYLOAD_DIR,
        guardrails_v6.PAYLOAD_FILES,
        "specs-6.json",
        NON_CONFIG_RULES | TESTS_CHECK_BEHAVIOUR | SCOPE_BEHAVIOUR | TEST_CLASS_BEHAVIOUR,
    ),
}
CANARY_PACK = {5: 4}
"""The fixture generator's ``--pack`` for a pack whose rules are another's byte for byte."""
PACK_1 = PACKS[1]


def host_home(
    base: Path,
    *,
    tools: tuple[str, ...] = ("python", "ruff", "mypy"),
    pack: PackUnderTest = PACK_1,
) -> Path:
    """A gate home using the payload and this environment's tools (missing ones omitted)."""
    home = base / "home"
    venv = home / "venv" / "bin"
    venv.mkdir(parents=True)
    for name in ("bin", "lib", "config"):
        shutil.copytree(pack.payload_dir / name, home / name)
    (home / "bin" / "guardrail-check").chmod(0o755)
    here = Path(sys.executable).parent
    for name in tools:
        (venv / name).symlink_to(here / name if name != "python" else Path(sys.executable))
    return home


GATE_PREFIX = "guardrail-gate@"


def experiment_gate_uses() -> dict[str, tuple[str, dict[str, Any]]]:
    """The guardrails experiment's guardrail-gate use (ref, ``with``) per condition."""
    spec = yaml.safe_load(GUARDRAILS_EXPERIMENT.read_text())
    return {
        condition["id"]: (use["use"], dict(use["with"]))
        for condition in spec["conditions"]
        for use in condition["components"]
        if use["use"].startswith(GATE_PREFIX)
    }


def experiment_gate_ref() -> str:
    """The one guardrail-gate version the guardrails experiment uses."""
    refs = {ref for ref, _ in experiment_gate_uses().values()}
    if len(refs) != 1:
        raise AssertionError(f"the guardrails experiment uses guardrail-gate versions {refs}")
    return refs.pop()


def experiment_gate_use(mode: str = "commit") -> dict[str, Any]:
    """The experiment's guardrail-gate ``with`` in ``mode`` (commit: the enforced r2 gate)."""
    for _, gate in experiment_gate_uses().values():
        if gate.get("mode", "commit") == mode:
            return gate
    raise AssertionError(f"the guardrails experiment declares no guardrail-gate in mode {mode}")


def commit_gate_script() -> str:
    """The experiment's guardrail-gate commit-mode script for its enforced gate command."""
    gate = experiment_gate_use()
    options = GuardrailGateOptionsV3(
        inner_agent=InnerAgentSpec(name="nop"),
        command=gate["command"],
        mode=GateModeV2(gate.get("mode", "commit")),
        max_rounds=gate.get("max_rounds", 3),
        timeout_sec=gate.get("timeout_sec", 600),
        work_dir=str(CANARY_GATE_DIR / "work"),
    )
    return gate_script(options, 1, CANARY_GATE_DIR / "logs" / "gate_run_1.log")


def assemble_verify_root(root: Path, pack: PackUnderTest = PACK_1) -> Path:
    """Copy one pack's payload, the harness, its specs and the rendered commit gate."""
    for relative in pack.payload_files:
        target = root / "pack" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pack.payload_dir / relative, target)
    for path in VERIFY_DIR.iterdir():
        if path.is_file() and path.name not in SPEC_FILES:
            shutil.copy2(path, root / path.name)
    shutil.copy2(pack.specs_path, root / "specs.json")
    script = root / COMMIT_SCRIPT
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(commit_gate_script())
    return root


# ---- the rule set the canaries must cover, derived from the shipped pack ---------------

_DIAGNOSTIC_CODE = re.compile(r"\"(PY[QS]\d{3}|FANOUT\.[\w-]+|TEST\.[\w-]+)\"")
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
            entries[f"ruff.toml:lint.{key}"] = (f"RUFF.LINT.{key}",)
    return entries


def pack_rule_entries(pack: PackUnderTest = PACK_1) -> dict[str, tuple[str, ...]]:
    """Every pack config entry and diagnostic code, with the canary ids that would cover it.

    Read from the shipped payload (``ruff.toml``, ``mypy.ini`` and the gate
    library's diagnostic codes), so a rule added to the pack without a canary
    shows up as an uncovered entry instead of being missed by a hard-coded count.
    """
    import configparser
    import tomllib

    config = pack.payload_dir / "config"
    entries = _ruff_entries(tomllib.loads((config / "ruff.toml").read_text()))
    mypy = configparser.ConfigParser()
    mypy.read_string((config / "mypy.ini").read_text())
    entries |= {f"mypy.ini:{key}": (f"MYPY.{key}", f"MYPY.SETTING.{key}") for key in mypy["mypy"]}
    library = pack.payload_dir / "lib" / "guardrails"
    codes = {
        code
        for path in sorted(library.glob("*.py"))
        for code in _DIAGNOSTIC_CODE.findall(path.read_text())
    }
    entries |= {f"code:{code}": (code,) for code in sorted(codes - _NO_COUNTERPART)}
    return entries


def uncovered_pack_entries(specs: dict[str, Any], pack: PackUnderTest = PACK_1) -> list[str]:
    """Pack entries no canary spec covers."""
    return [
        entry
        for entry, ids in pack_rule_entries(pack).items()
        if not any(rule in specs for rule in ids)
    ]


def implemented_rules(specs: dict[str, Any], pack: PackUnderTest = PACK_1) -> frozenset[str]:
    """The canaried rules the pack implements: every covered entry's id, plus gate behaviour."""
    covered = {rule for ids in pack_rule_entries(pack).values() for rule in ids if rule in specs}
    return frozenset(covered) | pack.non_config_rules
