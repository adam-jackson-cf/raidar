"""python-guardrails@6: @5 whose ``PLR0904`` limit no longer applies to test classes.

Covers the contract (@5's payload byte for byte except the version strings and
the four test-file per-file-ignores entries of ``config/ruff.toml``), the
canary specs (@4's plus the test-class canary), the install and per-step check
(@5's), the probe on @6's pin, the config-copy flags against @6's own bytes,
and the rule change itself with this environment's Ruff (not the pin): a
21-method test class is quiet under each test-file glob and refused at a
production path. The canaries run against the pinned Ruff in the pack image
(``make guardrails-verify``); the real trial is
``tests/test_guardrails_v6_trial.py``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from raidar.components import ComponentKind, ComponentRegistry
from raidar.components.builtins import guardrails_v5
from raidar.components.builtins._common import sha256_bytes
from raidar.components.builtins.guardrails_v6 import (
    ENTRYPOINT,
    PAYLOAD_DIR,
    PAYLOAD_FILES,
    PAYLOAD_PACKAGE_DIR,
    PINNED_VERSION,
    PROTECTED_CONFIGS,
    TEST_FILE_GLOBS,
    TOOL_HOME,
    check_script,
    config_flags,
    install_script,
    payload_bytes,
)
from raidar.components.integrity import condition_step_flags
from raidar.components.provision import PROVISION_CHANNEL
from raidar.components.provision_v2 import trial_provisions
from tests.component_support import compile_uses, use, write_jsonl, write_step_reports
from tests.python_guardrails_support import PACKS, implemented_rules, uncovered_pack_entries

REF = "python-guardrails@6"
SLUG = "00-python-guardrails"
WORKSPACE_CONFIGS = ("ruff.toml", "mypy.ini")
CANARY = "RUFF.LINT.per-file-ignores.test-public-methods"


def _sha(relative: str) -> str:
    return sha256_bytes(payload_bytes(relative))


# ---- contract -------------------------------------------------------------------------


def test_v6_is_a_python_service_that_fingerprints_its_whole_payload() -> None:
    definition = ComponentRegistry().get(REF)
    manifest = definition.manifest

    assert (manifest.kind, definition.runtimes) == (ComponentKind.SERVICE, ("python",))
    for relative in PAYLOAD_FILES:
        assert f"{PAYLOAD_PACKAGE_DIR}/{relative}" in manifest.fingerprint
    assert "raidar/components/builtins/guardrails_v6.py" in manifest.fingerprint
    shipped = {
        path.relative_to(PAYLOAD_DIR).as_posix()
        for path in PAYLOAD_DIR.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and ".ruff_cache" not in path.parts
    }
    assert shipped - set(PAYLOAD_FILES) == {"requirements.in"}
    assert set(PAYLOAD_FILES) == set(guardrails_v5.PAYLOAD_FILES)


def test_only_the_version_strings_and_the_test_ignores_differ_from_v5() -> None:
    changed = set()
    for relative in PAYLOAD_FILES:
        v5, v6 = guardrails_v5.payload_bytes(relative).decode(), payload_bytes(relative).decode()
        if v6 == v5:
            continue
        changed.add(relative)
        renamed = v5.replace("python-guardrails@5", "python-guardrails@6").replace(
            "Scope (python-guardrails@4 and @5)", "Scope (python-guardrails@4 to @6)"
        )
        if relative == "config/ruff.toml":
            for glob in TEST_FILE_GLOBS:
                renamed = renamed.replace(
                    f'"{glob}" = ["D", "S101", "PLR2004"]',
                    f'"{glob}" = ["D", "S101", "PLR2004", "PLR0904"]',
                )
        assert v6 == renamed, relative
    assert {"config/ruff.toml", "lib/guardrails/gate.py"} <= changed
    assert guardrails_v5.PINNED_VERSION.replace("@5", "@6") == PINNED_VERSION


def test_plr0904_is_ignored_in_exactly_the_test_file_globs() -> None:
    v5 = tomllib.loads(guardrails_v5.payload_bytes("config/ruff.toml").decode())
    v6 = tomllib.loads(payload_bytes("config/ruff.toml").decode())
    ignores5, ignores6 = v5["lint"]["per-file-ignores"], v6["lint"]["per-file-ignores"]

    assert {glob for glob, codes in ignores6.items() if "PLR0904" in codes} == set(TEST_FILE_GLOBS)
    assert {glob: [c for c in codes if c != "PLR0904"] for glob, codes in ignores6.items()} == (
        ignores5
    )
    assert "PLR0904" in v6["lint"]["select"]
    assert v6["lint"]["pylint"]["max-public-methods"] == 20
    assert {k: v for k, v in v6.items() if k != "lint"} == {
        k: v for k, v in v5.items() if k != "lint"
    }


def test_v6s_canaries_are_v4s_plus_the_test_class_canary() -> None:
    v4 = json.loads(PACKS[4].specs_path.read_text())
    v6 = json.loads(PACKS[6].specs_path.read_text())

    assert set(v6) - set(v4) == {CANARY}
    assert v6[CANARY] == {"codes": "PLR0904", "mode": "rule"}
    assert {rule: v6[rule] for rule in v4} == v4
    assert uncovered_pack_entries(v6, PACKS[6]) == []
    assert implemented_rules(v6, PACKS[6]) == set(v6)


def test_the_install_and_per_step_check_are_v5s() -> None:
    (provision,) = trial_provisions(compile_uses(use(REF, workspace_configs=True)))

    assert provision.install == install_script() == guardrails_v5.install_script()
    assert provision.check == check_script() == guardrails_v5.check_script()
    assert set(provision.payload) == set(PAYLOAD_FILES)
    for relative in ("bin/pytest", "bin/coverage", "lib/agent_tools/run-module", ENTRYPOINT):
        assert provision.payload[relative].executable


# ---- probe and flags ------------------------------------------------------------------


def _values(**overrides: str) -> dict[str, str]:
    values = {
        "workspace": "/app",
        "link_target": f"{TOOL_HOME}/{ENTRYPOINT}",
        "home_owner": "root",
        "home_writable": "no",
        "record_owner": "root",
        "install_version": PINNED_VERSION,
        "install_smoke_clean_rc": "0",
        "install_smoke_refused_rc": "1",
        "install_smoke_refused_codes": "F401,TEST.missing",
        "install_tree_digest": "t" * 64,
        "tree_digest": "t" * 64,
        "install_agent_tool_versions": guardrails_v5.AGENT_TOOL_VERSIONS,
        "install_smoke_agent_pytest_rc": "0",
        "install_smoke_agent_coverage_rc": "0",
        "install_smoke_agent_ruff_format_rc": "0",
    }
    for name, target in guardrails_v5.AGENT_TOOLS.items():
        values[f"agent_tool_{name}"] = str(guardrails_v5.BIN_DIR / name)
        values[f"agent_tool_{name}_target"] = str(target)
    for name in PROTECTED_CONFIGS:
        values[f"protected_{name.replace('.', '_')}"] = _sha(f"config/{name}")
    for name in WORKSPACE_CONFIGS:
        values[f"workspace_{name.replace('.', '_')}"] = _sha(f"config/{name}")
    return values | overrides


def _agent_dir(tmp_path: Path, values: dict[str, str]) -> Path:
    agent_dir = tmp_path / "agent"
    write_step_reports(agent_dir, {name: _sha(f"config/{name}") for name in WORKSPACE_CONFIGS})
    record = {
        "kind": "raidar-provision",
        "slug": SLUG,
        "install": {"ok": True, "phase": "environment_start", "output": ""},
        "check": {"ok": True, "values": values},
    }
    write_jsonl(agent_dir / "raidar-components" / f"{SLUG}.{PROVISION_CHANNEL}.jsonl", [record])
    return agent_dir


def test_the_probe_requires_v6s_pin_and_the_agents_tools(tmp_path: Path) -> None:
    compiled = compile_uses(use(REF, workspace_configs=True))

    assert compiled.probe_step(_agent_dir(tmp_path / "ok", _values()), 0).passed
    v5 = _values(install_version=guardrails_v5.PINNED_VERSION)
    assert not compiled.probe_step(_agent_dir(tmp_path / "v5", v5), 2).passed
    gone = _values(agent_tool_ruff="missing", agent_tool_ruff_target="missing")
    assert not compiled.probe_step(_agent_dir(tmp_path / "gone", gone), 1).passed


def test_unmodified_copies_raise_no_flag_against_v6s_own_bytes(tmp_path: Path) -> None:
    """@1's comparison would call @6's unmodified ruff.toml copy modified at every step."""
    compiled = compile_uses(use(REF, workspace_configs=True))

    assert condition_step_flags(compiled, _agent_dir(tmp_path / "same", _values()), 3) == ()
    edited = _values(workspace_ruff_toml="e" * 64)
    flags = condition_step_flags(compiled, _agent_dir(tmp_path / "edited", edited), 3)
    assert flags == (
        f"{REF}/{SLUG}: workspace-config-modified: ruff.toml at step start (modified)",
    )
    v5_bytes = _values(
        workspace_ruff_toml=sha256_bytes(guardrails_v5.payload_bytes("config/ruff.toml"))
    )
    assert config_flags(v5_bytes, [], 1) == [
        "workspace-config-modified: ruff.toml at step start (modified)"
    ]


# ---- the rule, with this environment's Ruff --------------------------------------------


def _ruff() -> Path | None:
    found = shutil.which("ruff", path=str(Path(sys.executable).parent))
    return Path(found) if found else None


def _many_methods(test_case: bool) -> str:
    if not test_case:
        methods = "".join(f"    def m{i}(self) -> int:\n        return {i}\n\n" for i in range(21))
        return f"class Service:\n{methods}"
    methods = "".join(
        f"    def test_{i}(self) -> None:\n        self.assertEqual({i}, {i})\n\n"
        for i in range(21)
    )
    return f"import unittest\n\n\nclass ManyTest(unittest.TestCase):\n{methods}"


@pytest.mark.skipif(_ruff() is None, reason="no ruff beside this interpreter")
@pytest.mark.parametrize(
    ("relative", "test_case", "refused_by"),
    [
        ("tests/unit/many_cases.py", True, {5}),
        ("test_top.py", True, {5}),
        ("pkg/many_test.py", True, {5}),
        ("pkg/test/cases.py", True, {5}),
        ("pkg/service.py", False, {5, 6}),
        ("pkg/checks.py", True, {5, 6}),
    ],
)
def test_a_21_method_class_is_refused_only_outside_test_files(
    tmp_path: Path, relative: str, test_case: bool, refused_by: set[int]
) -> None:
    ruff = _ruff()
    assert ruff is not None
    (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / relative).write_text(_many_methods(test_case))
    refused = set()
    for version, config in (
        (5, guardrails_v5.PAYLOAD_DIR / "config" / "ruff.toml"),
        (6, PAYLOAD_DIR / "config" / "ruff.toml"),
    ):
        done = subprocess.run(
            [str(ruff), "check", "--no-cache", "--config", str(config), "--select", "PLR0904"]
            + ["--output-format", "concise", "."],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if "PLR0904" in done.stdout:
            refused.add(version)
    assert refused == refused_by
