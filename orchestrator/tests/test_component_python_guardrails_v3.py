"""python-guardrails@3: the pack whose gate also runs the agent's own tests, installed at start.

Covers the contract (manifest, payload and locks, the install at environment
start and the tool-free per-step check), the discovery probe and the integrity
flags over recorded evidence, and the gate's tests check on the host (this
environment's Python, Ruff, mypy, pytest and coverage, not the pins). The
container proof is ``test_guardrails_v3_trial.py``; the rule canaries run in
the pack image (``make guardrails-verify``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from raidar.components import ComponentKind, ComponentRegistry, ComponentRejectedError
from raidar.components.catalog import guardrails as guardrails_v1
from raidar.components.catalog._common import sha256_bytes
from raidar.components.catalog.guardrails_v3 import (
    ENTRYPOINT,
    INSTALL_RECORD,
    LINK_PATH,
    PAYLOAD_DIR,
    PAYLOAD_FILES,
    PAYLOAD_PACKAGE_DIR,
    PINNED_VERSION,
    PROTECTED_CONFIGS,
    TOOL_HOME,
    check_script,
    install_script,
    payload_bytes,
)
from raidar.components.definition.integrity import condition_step_flags
from raidar.components.runtime.provision import PROVISION_CHANNEL
from raidar.components.runtime.provision_v2 import (
    AGENT_USER_ENV,
    PAYLOAD_ENV,
    TrialProvision,
    trial_provisions,
)
from tests.component_support import compile_uses, use, write_jsonl, write_step_reports
from tests.python_guardrails_support import PACKS, host_home

REF = "python-guardrails@3"
SLUG = "00-python-guardrails"
WORKSPACE_CONFIGS = ("ruff.toml", "mypy.ini")
MODULE = 'def double(x: int) -> int:\n    """Double x."""\n    return 2 * x\n'
TRIPLE = '\n\ndef triple(x: int) -> int:\n    """Triple x."""\n    return 3 * x\n'
UNIT_TEST = (
    "import unittest\n\nfrom mod import double\n\n\n"
    "class DoubleTest(unittest.TestCase):\n"
    "    def test_double(self) -> None:\n"
    "        self.assertEqual(double(2), {expected})\n"
)


def _sha(relative: str) -> str:
    return sha256_bytes(payload_bytes(relative))


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


def _agent_dir(
    tmp_path: Path,
    values: dict[str, str] | None,
    *,
    install_ok: bool = True,
    workspace_files: dict[str, str | None] | None = None,
) -> Path:
    agent_dir = tmp_path / "agent"
    files = workspace_files
    if files is None:
        files = {name: _sha(f"config/{name}") for name in WORKSPACE_CONFIGS}
    write_step_reports(agent_dir, files)
    if values is not None:
        record = {
            "kind": "raidar-provision",
            "slug": SLUG,
            "install": {"ok": install_ok, "phase": "environment_start", "output": ""},
            "check": {"ok": True, "values": values} if install_ok else None,
        }
        if not install_ok:
            record["install"]["output"] = "no network"
        write_jsonl(agent_dir / "raidar-components" / f"{SLUG}.{PROVISION_CHANNEL}.jsonl", [record])
    return agent_dir


def _compiled(copies: bool = True) -> Any:
    return compile_uses(use(REF, workspace_configs=copies))


# ---- contract -------------------------------------------------------------------------


def test_v3_is_a_python_service_that_fingerprints_its_whole_payload_and_installer() -> None:
    definition = ComponentRegistry().get(REF)
    manifest = definition.manifest

    assert (manifest.kind, definition.runtimes) == (ComponentKind.SERVICE, ("python",))
    assert set(manifest.network.egress_hosts) == {"files.pythonhosted.org", "pypi.org"}
    for relative in PAYLOAD_FILES:
        assert f"{PAYLOAD_PACKAGE_DIR}/{relative}" in manifest.fingerprint
    assert "raidar/components/runtime/provision_v2.py" in manifest.fingerprint
    shipped = {
        path.relative_to(PAYLOAD_DIR).as_posix()
        for path in PAYLOAD_DIR.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and ".ruff_cache" not in path.parts
    }
    assert shipped - set(PAYLOAD_FILES) == {"requirements.in"}, (
        "every shipped file is fingerprinted"
    )


def test_the_rules_and_configs_are_v1s_byte_for_byte_and_only_the_tests_check_is_new() -> None:
    for name in ("ruff.toml", "mypy.ini"):
        assert payload_bytes(f"config/{name}") == guardrails_v1.payload_bytes(f"config/{name}")
    for name in ("diagnostics.py", "fanout.py", "scope.py", "source_rules.py", "structure.py"):
        relative = f"lib/guardrails/{name}"
        assert payload_bytes(relative) == guardrails_v1.payload_bytes(relative)
    policy = payload_bytes("config/policy.toml").decode()
    assert 'default = "block"' in policy
    assert "TEST." not in policy.split("off = [")[1]
    gate = payload_bytes("lib/guardrails/gate.py").decode()
    assert 'PACK = "python-guardrails@3"' in gate
    tests = payload_bytes("lib/guardrails/tests_check.py").decode()
    assert "COVERAGE_MINIMUM_PERCENT = 85.0" in tests, "the rule catalogue's threshold (f61d81b)"


@pytest.mark.parametrize("platform", ["aarch64", "x86_64"])
def test_each_lock_hash_pins_the_tools_and_adds_pytest_and_coverage(platform: str) -> None:
    lines = payload_bytes(f"requirements-{platform}-unknown-linux-gnu.lock").decode().splitlines()
    heads = [i for i, line in enumerate(lines) if line and not line.startswith((" ", "#"))]
    pinned = {lines[i].split(" ")[0] for i in heads}

    assert {"ruff==0.8.6", "mypy==2.3.1", "pytest==9.1.1", "coverage==7.16.2"} <= pinned
    assert all(lines[i + 1].strip().startswith("--hash=sha256:") for i in heads), "hash-pinned"
    assert PINNED_VERSION.endswith("pytest 9.1.1, coverage 7.16.2")


def test_the_install_runs_once_at_start_hash_pinned_root_owned_and_smokes_as_the_agent() -> None:
    script = install_script()

    assert f'src="${PAYLOAD_ENV}"' in script
    assert "--require-hashes --no-deps --only-binary :all:" in script
    assert "--no-cache" in script
    assert "--no-config" in script
    assert 'chown -R root:root "$home"' in script
    assert "go-w" in script
    assert f'"$home/{ENTRYPOINT}" {LINK_PATH}' in script
    assert f'runuser -u "${AGENT_USER_ENV}"' in script
    assert 'rm -rf "$src"' in script, "the staged payload does not outlive the install"
    assert str(INSTALL_RECORD) in script
    assert 'chmod 644 "$record.tmp"' in script
    assert "/app" not in script


def test_the_per_step_check_runs_no_tool_only_hashes_and_the_install_record() -> None:
    script = check_script()

    assert "--version" not in script, "no tool runs per step"
    assert "--root" not in script, "no gate run per step"
    assert f"cat {INSTALL_RECORD}" in script
    assert "tree_digest=" in script
    assert "sha256sum" in script
    assert "home_writable=" in script
    assert "record_owner=" in script


def test_contribute_puts_only_the_config_copies_in_the_workspace_and_no_support_files() -> None:
    with_copies, without = _compiled(True), _compiled(False)

    assert set(with_copies.overlay) == set(WORKSPACE_CONFIGS)
    assert with_copies.support_files == {}
    assert without.support_files == {}
    assert without.overlay == {}
    (provision,) = trial_provisions(with_copies)
    assert isinstance(provision, TrialProvision)
    assert set(provision.payload) == set(PAYLOAD_FILES)
    assert provision.payload[ENTRYPOINT].executable
    assert (provision.component, provision.slug) == (REF, SLUG)


def test_invalid_uses_reject_the_arm() -> None:
    for bad in ({"workspace_configs": "yes"}, {"other": 1}):
        with pytest.raises(ComponentRejectedError):
            compile_uses(use(REF, **bad))


# ---- probe and flags -------------------------------------------------------------------


def test_the_probe_passes_every_step_on_recorded_install_values(tmp_path: Path) -> None:
    compiled = _compiled()
    report = compiled.probe_step(_agent_dir(tmp_path, _values()), 0)

    assert report.passed, report.to_json()
    assert PINNED_VERSION in json.dumps(report.to_json())


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"install_version": "python-guardrails@1: ruff 0.8.6, mypy 2.3.1"}, "not 'python"),
        ({"tree_digest": "x" * 64}, "differs from the install's"),
        ({"install_smoke_clean_rc": "1"}, "clean file with its test"),
        ({"install_smoke_refused_codes": "TEST.missing"}, "not 1 with F401"),
        ({"record_owner": "agent"}, "install record is owned"),
        ({"link_target": "/tmp/fake"}, "resolves to"),
    ],
)
def test_the_probe_fails_at_any_step_when_the_installed_gate_is_not_the_pinned_one(
    tmp_path: Path, override: dict[str, str], fragment: str
) -> None:
    report = _compiled().probe_step(_agent_dir(tmp_path, _values(**override)), 3)

    assert not report.passed
    assert fragment in json.dumps(report.to_json())


def test_step_one_requires_protection_and_the_copies_or_their_absence(tmp_path: Path) -> None:
    writable = _agent_dir(tmp_path / "w", _values(home_writable="/opt/raidar-tools/x"))
    assert not _compiled().probe_step(writable, 0).passed
    assert _compiled().probe_step(writable, 1).passed, "after step 1 it is a flag, not a failure"
    absent = dict.fromkeys(WORKSPACE_CONFIGS)
    bare = _values(**{f"workspace_{n.replace('.', '_')}": "missing" for n in WORKSPACE_CONFIGS})
    assert (
        _compiled(False)
        .probe_step(_agent_dir(tmp_path / "a", bare, workspace_files=absent), 0)
        .passed
    )
    assert (
        not _compiled(True)
        .probe_step(_agent_dir(tmp_path / "b", bare, workspace_files=absent), 0)
        .passed
    )
    assert not _compiled(False).probe_step(_agent_dir(tmp_path / "c", _values()), 0).passed


def test_a_failed_install_or_no_record_fails_the_probe(tmp_path: Path) -> None:
    assert (
        not _compiled()
        .probe_step(_agent_dir(tmp_path / "f", _values(), install_ok=False), 0)
        .passed
    )
    assert not _compiled().probe_step(_agent_dir(tmp_path / "n", None), 0).passed


def test_a_tree_or_protection_change_after_step_one_is_flagged_never_failed(tmp_path: Path) -> None:
    changed = _values(tree_digest="y" * 64, protected_policy_toml="0" * 64)
    agent_dir = _agent_dir(tmp_path, changed)

    flags = condition_step_flags(_compiled(False), agent_dir, 2)

    assert any("protected-tree-modified" in flag for flag in flags)
    assert any("protected-config-modified: config/policy.toml" in flag for flag in flags)
    assert condition_step_flags(_compiled(False), _agent_dir(tmp_path / "ok", _values()), 2) == ()


# ---- the gate's tests check on the host -------------------------------------------------

PYTHON_TOOLS = ("python", "ruff", "mypy", "coverage")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A v3 gate home on this environment's tools, its site-packages included."""
    home = host_home(tmp_path, tools=PYTHON_TOOLS, pack=PACKS[3])
    (home / "venv" / "lib").symlink_to(Path(sys.executable).parent.parent / "lib")
    return home


def _workspace(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "app"
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text)
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").symlink_to(sys.executable)
    return root


def _gate(
    home: Path, root: Path, extra_env: dict[str, str] | None = None
) -> tuple[int, dict[str, Any]]:
    report = root.parent / "report.json"
    env = {**os.environ, "GUARDRAILS_HOME": str(home), **(extra_env or {})}
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


def _block(report: dict[str, Any]) -> set[str]:
    return set(report["counts"]["block"])


def test_a_change_without_tests_is_refused_and_tests_that_cover_it_pass(
    tmp_path: Path, home: Path
) -> None:
    rc, report = _gate(home, _workspace(tmp_path / "bare", {"mod.py": MODULE}))
    assert (rc, _block(report)) == (1, {"TEST.missing"})
    assert report["tests"]["required"] is True

    tested = {"mod.py": MODULE, "test_mod.py": UNIT_TEST.format(expected=4)}
    rc, report = _gate(home, _workspace(tmp_path / "tested", tested))
    assert (rc, _block(report)) == (0, set())
    assert report["tests"]["tests_ran"] == 1
    assert report["tests"]["coverage_percent"] == 100.0


def test_failing_tests_of_either_runner_and_low_coverage_are_refused(
    tmp_path: Path, home: Path
) -> None:
    failing = {"mod.py": MODULE, "test_mod.py": UNIT_TEST.format(expected=5)}
    assert _block(_gate(home, _workspace(tmp_path / "u", failing))[1]) == {"TEST.failed"}
    pytest_style = {
        "mod.py": MODULE,
        "test_mod.py": (
            "from mod import double\n\n\ndef test_double() -> None:\n    assert double(2) == 5\n"
        ),
    }
    _, report = _gate(home, _workspace(tmp_path / "p", pytest_style))
    assert _block(report) == {"TEST.failed"}
    assert report["tests"]["runners"]["pytest"]["failed"] == 1
    uncovered = {"mod.py": MODULE + TRIPLE, "test_mod.py": UNIT_TEST.format(expected=4)}
    _, report = _gate(home, _workspace(tmp_path / "c", uncovered))
    assert _block(report) == {"TEST.coverage-minimum"}
    assert report["tests"]["coverage_percent"] == 75.0


def test_a_callers_own_coverage_settings_never_reach_the_gates_measurement(
    tmp_path: Path, home: Path
) -> None:
    tested = {"mod.py": MODULE, "test_mod.py": UNIT_TEST.format(expected=4)}
    outer = {
        "COVERAGE_FILE": str(tmp_path / "outer" / ".coverage"),
        "COVERAGE_PROCESS_START": str(tmp_path / "outer.rc"),
        "COV_CORE_SOURCE": str(tmp_path),
    }

    rc, report = _gate(home, _workspace(tmp_path, tested), outer)

    assert (rc, report["tests"]["coverage_percent"]) == (0, 100.0)
    assert not (tmp_path / "outer").exists()


def test_the_test_run_never_writes_the_workspace(tmp_path: Path, home: Path) -> None:
    writer = UNIT_TEST.format(expected=4).replace(
        "    def test_double(self) -> None:\n",
        '    def test_double(self) -> None:\n        open("written.txt", "w").close()\n',
    )
    root = _workspace(tmp_path, {"mod.py": MODULE, "test_mod.py": writer})
    before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))

    _gate(home, root)

    assert sorted(path.relative_to(root).as_posix() for path in root.rglob("*")) == before


def test_no_python_change_since_the_last_commit_needs_no_tests(tmp_path: Path, home: Path) -> None:
    root = _workspace(tmp_path, {"mod.py": MODULE})
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    for argv in (["init", "-q"], ["add", "mod.py"], ["commit", "-q", "-m", "base"]):
        subprocess.run([*git, *argv], cwd=root, check=True, capture_output=True)

    rc, report = _gate(home, root)
    assert (rc, report["tests"]["required"]) == (0, False)
    (root / "mod.py").write_text(MODULE + TRIPLE)
    assert _block(_gate(home, root)[1]) == {"TEST.missing"}


def test_an_outer_repository_never_exempts_a_workspace_inside_it(
    tmp_path: Path, home: Path
) -> None:
    outer = tmp_path / "outer"
    outer.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    (outer / ".gitignore").write_text("app/\n")
    for argv in (["init", "-q"], ["add", ".gitignore"], ["commit", "-q", "-m", "base"]):
        subprocess.run([*git, *argv], cwd=outer, check=True, capture_output=True)
    root = _workspace(outer, {"mod.py": MODULE})

    rc, report = _gate(home, root)

    assert (rc, _block(report)) == (1, {"TEST.missing"})
    assert report["tests"]["reason"].startswith("not its own git work tree")


def test_the_tests_check_refuses_a_root_under_a_verifier_path(tmp_path: Path) -> None:
    lib = PAYLOAD_DIR / "lib"
    code = (
        "import sys, pathlib; sys.path.insert(0, sys.argv[1]);"
        "from guardrails.tests_check import TestsGateError, _check_root\n"
        "for root in ('/tests', '/tests/x', '/logs/verifier'):\n"
        "    try:\n        _check_root(pathlib.Path(root))\n"
        "    except TestsGateError:\n        continue\n"
        "    raise SystemExit(f'{root} was accepted')\n"
        "_check_root(pathlib.Path(sys.argv[2]))\n"
    )
    done = subprocess.run([sys.executable, "-c", code, str(lib), str(tmp_path)], check=False)
    assert done.returncode == 0
