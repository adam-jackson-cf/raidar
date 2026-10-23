"""python-guardrails@4: @3 whose gate also checks extensionless python scripts and ``.py`` symlinks.

Covers the contract (only the scope, its consumers and the version strings
differ from @3's payload), the probe on @4's pinned version, and the gate's
scope on the host (this environment's Python, Ruff and coverage, not the
pins): the ``smoke-03`` r2 layout (an extensionless ``forge`` script and a
``forge.py`` symlink to it) that @3 checked as empty. The rule canaries,
``SCOPE.*`` included, run in the pack image (``make guardrails-verify``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from raidar.components import ComponentKind, ComponentRegistry
from raidar.components.catalog import guardrails_v3
from raidar.components.catalog._common import sha256_bytes
from raidar.components.catalog.guardrails_v4 import (
    ENTRYPOINT,
    PAYLOAD_DIR,
    PAYLOAD_FILES,
    PAYLOAD_PACKAGE_DIR,
    PINNED_VERSION,
    PROTECTED_CONFIGS,
    TOOL_HOME,
    payload_bytes,
)
from raidar.components.definition.integrity import condition_step_flags
from raidar.components.runtime.provision import PROVISION_CHANNEL
from raidar.components.runtime.provision_v2 import trial_provisions
from tests.component_support import compile_uses, use, write_jsonl, write_step_reports
from tests.python_guardrails_support import (
    PACKS,
    host_home,
    implemented_rules,
    uncovered_pack_entries,
)

REF = "python-guardrails@4"
SLUG = "00-python-guardrails"
WORKSPACE_CONFIGS = ("ruff.toml", "mypy.ini")
CHANGED = {"lib/guardrails/scope.py", "lib/guardrails/tests_check.py", "lib/guardrails/gate.py"}
VERSION_ONLY = {
    "bin/guardrail-check",
    "config/policy.toml",
    "lib/guardrails/__init__.py",
    "lib/guardrails/testrun.py",
    "lib/guardrails_site/sitecustomize.py",
}
SCRIPT = (
    "#!/usr/bin/env python3\n"
    '"""A command-line tool."""\n\n'
    "import os\nimport sys\n\n\n"
    "def main() -> int:\n"
    '    """Print the argument count."""\n'
    "    print(len(sys.argv) - 1)\n"
    "    return 0\n\n\n"
    'if __name__ == "__main__":\n'
    "    raise SystemExit(main())\n"
)
CLEAN_SCRIPT = SCRIPT.replace("import os\n", "")
SCRIPT_TEST = (
    "import subprocess\nimport sys\nimport unittest\n\n\n"
    "class ToolTest(unittest.TestCase):\n"
    "    def test_counts(self) -> None:\n"
    '        done = subprocess.run([sys.executable, "forge", "a", "b"], capture_output=True, '
    "text=True, check=True, timeout=60)\n"
    '        self.assertEqual(done.stdout.strip(), "2")\n'
)


def _sha(relative: str) -> str:
    return sha256_bytes(payload_bytes(relative))


# ---- contract -------------------------------------------------------------------------


def test_v4_is_a_python_service_that_fingerprints_its_whole_payload() -> None:
    definition = ComponentRegistry().get(REF)
    manifest = definition.manifest

    assert (manifest.kind, definition.runtimes) == (ComponentKind.SERVICE, ("python",))
    for relative in PAYLOAD_FILES:
        assert f"{PAYLOAD_PACKAGE_DIR}/{relative}" in manifest.fingerprint
    assert "raidar/components/catalog/guardrails_v4.py" in manifest.fingerprint
    shipped = {
        path.relative_to(PAYLOAD_DIR).as_posix()
        for path in PAYLOAD_DIR.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and ".ruff_cache" not in path.parts
    }
    assert shipped - set(PAYLOAD_FILES) == {"requirements.in"}


def test_only_the_scope_and_its_consumers_differ_from_v3_beside_the_version_strings() -> None:
    differs = {
        relative
        for relative in PAYLOAD_FILES
        if payload_bytes(relative) != guardrails_v3.payload_bytes(relative)
    }

    assert differs == CHANGED | VERSION_ONLY
    for relative in VERSION_ONLY:
        v3 = guardrails_v3.payload_bytes(relative).decode()
        assert payload_bytes(relative).decode() == v3.replace(
            "python-guardrails@3", "python-guardrails@4"
        )
    assert 'PACK = "python-guardrails@4"' in payload_bytes("lib/guardrails/gate.py").decode()
    assert PINNED_VERSION.startswith("python-guardrails@4: ")


def test_v4s_canaries_are_v3s_plus_the_scope_canaries() -> None:
    v3 = json.loads(PACKS[3].specs_path.read_text())
    v4 = json.loads(PACKS[4].specs_path.read_text())

    assert set(v4) - set(v3) == {"SCOPE.shebang-script", "SCOPE.py-symlink"}
    assert {rule: v4[rule] for rule in v3} == v3
    assert uncovered_pack_entries(v4, PACKS[4]) == []
    assert implemented_rules(v4, PACKS[4]) == set(v4)


def test_the_install_and_per_step_check_are_v3s() -> None:
    (provision,) = trial_provisions(compile_uses(use(REF, workspace_configs=True)))

    assert provision.install == guardrails_v3.install_script()
    assert provision.check == guardrails_v3.check_script()
    assert set(provision.payload) == set(PAYLOAD_FILES)
    assert provision.payload[ENTRYPOINT].executable


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
    }
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


def test_the_probe_requires_v4s_pinned_version(tmp_path: Path) -> None:
    compiled = compile_uses(use(REF, workspace_configs=True))

    assert compiled.probe_step(_agent_dir(tmp_path / "ok", _values()), 0).passed
    v3 = _values(install_version=guardrails_v3.PINNED_VERSION)
    report = compiled.probe_step(_agent_dir(tmp_path / "v3", v3), 2)
    assert not report.passed
    assert "not 'python-guardrails@4" in json.dumps(report.to_json())


def test_tree_and_config_changes_after_step_one_are_flagged(tmp_path: Path) -> None:
    compiled = compile_uses(use(REF, workspace_configs=False))
    changed = _values(tree_digest="y" * 64, protected_policy_toml="0" * 64)

    flags = condition_step_flags(compiled, _agent_dir(tmp_path, changed), 2)

    assert any("protected-tree-modified" in flag for flag in flags)
    assert any("protected-config-modified: config/policy.toml" in flag for flag in flags)


# ---- the gate's scope on the host -------------------------------------------------------

PYTHON_TOOLS = ("python", "ruff", "mypy", "coverage")


def _home(tmp_path: Path, version: int) -> Path:
    home = host_home(tmp_path / f"home{version}", tools=PYTHON_TOOLS, pack=PACKS[version])
    (home / "venv" / "lib").symlink_to(Path(sys.executable).parent.parent / "lib")
    return home


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return _home(tmp_path, 4)


def _workspace(root: Path, files: dict[str, str], links: dict[str, str] | None = None) -> Path:
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text)
    for link, target in (links or {}).items():
        (root / link).symlink_to(target)
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").symlink_to(sys.executable)
    return root


def _gate(home: Path, root: Path) -> tuple[int, dict[str, Any]]:
    report = root.parent / f"{root.name}-report.json"
    env = {**os.environ, "GUARDRAILS_HOME": str(home)}
    env.pop("RAIDAR_GATE_REPORT_DIR", None)
    done = subprocess.run(
        [str(home / "bin" / "guardrail-check"), "--root", str(root), "--json-out", str(report)]
        + ["--no-mypy"],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
        env=env,
    )
    assert done.returncode in (0, 1), done.stdout + done.stderr
    return done.returncode, json.loads(report.read_text())


def _codes(report: dict[str, Any]) -> list[tuple[str, str]]:
    return sorted((f["code"], f["relpath"]) for f in report["findings"] if f["tier"] == "block")


def test_the_smoke_03_layout_is_checked_once_at_the_scripts_path(tmp_path: Path) -> None:
    files, links = {"forge": SCRIPT}, {"forge.py": "forge"}
    v3_rc, v3 = _gate(_home(tmp_path, 3), _workspace(tmp_path / "v3", files, links))
    rc, report = _gate(_home(tmp_path, 4), _workspace(tmp_path / "v4", files, links))

    assert (v3_rc, v3["files_checked"], v3["findings"]) == (0, 0, []), "the @3 hole"
    assert (rc, report["files_checked"], report["production_files"]) == (1, 1, 1)
    assert _codes(report) == [("F401", "forge"), ("TEST.missing", ".")]


def test_a_tested_script_is_measured_through_its_subprocess_and_passes(
    tmp_path: Path, home: Path
) -> None:
    files = {"forge": CLEAN_SCRIPT, "tests/unit/test_forge.py": SCRIPT_TEST}
    root = _workspace(tmp_path / "app", files, {"forge.py": "forge"})

    rc, report = _gate(home, root)

    assert (rc, _codes(report)) == (0, [])
    assert report["tests"]["tests_ran"] == 1
    assert report["tests"]["owned_files"] == 1
    assert report["tests"]["coverage_percent"] >= 85.0


def test_a_py_symlink_counts_as_its_in_root_target_whatever_its_name(
    tmp_path: Path, home: Path
) -> None:
    root = _workspace(tmp_path / "app", {"impl": "import os\n"}, {"tool.py": "impl"})

    _, report = _gate(home, root)

    assert report["files_checked"] == 1
    assert ("F401", "impl") in _codes(report)


def test_links_out_of_the_root_and_non_python_files_stay_out_of_scope(
    tmp_path: Path, home: Path
) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("VALUE: int = 2\n")
    files = {"notes": "#!/bin/sh\necho hi\n", "data": "import os\n", "ok.py": "VALUE: int = 1\n"}
    root = _workspace(tmp_path / "app", files, {"far.py": str(outside)})

    _, report = _gate(home, root)

    assert report["files_checked"] == 1, "only ok.py: no link target outside the root"
    assert all(code != "F401" for code, _ in _codes(report)), "data is not a python script"


def test_a_change_to_an_extensionless_script_since_the_last_commit_needs_tests(
    tmp_path: Path, home: Path
) -> None:
    root = _workspace(tmp_path / "app", {"forge": CLEAN_SCRIPT})
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    for argv in (["init", "-q"], ["add", "forge"], ["commit", "-q", "-m", "base"]):
        subprocess.run([*git, *argv], cwd=root, check=True, capture_output=True)

    rc, report = _gate(home, root)
    assert (rc, report["tests"]["required"]) == (0, False)
    (root / "forge").write_text(CLEAN_SCRIPT.replace("- 1", "- 0"))
    rc, report = _gate(home, root)
    assert (rc, report["tests"]["required"]) == (1, True)
    assert ("TEST.missing", ".") in _codes(report)
