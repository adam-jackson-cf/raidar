"""python-guardrails@5: @4 whose pinned pytest, coverage, ruff and mypy the agent can run too.

Covers the contract (@4's payload byte for byte except the version strings,
plus the agent's two wrappers and their runner), the install (@4's, then the
links and an agent-user smoke of each tool), the per-step check (where each
tool resolves on the agent's PATH) and the probe that requires them, and the
wrappers on the host: the project's interpreter runs the pack's pytest and
coverage, its own packages first. The trial in the pack image is
``tests/test_guardrails_v5_trial.py``; the rule canaries are @4's
(``make guardrails-verify``).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from raidar.components import ComponentKind, ComponentRegistry
from raidar.components.builtins import guardrails_v4
from raidar.components.builtins._common import sha256_bytes
from raidar.components.builtins.guardrails_v5 import (
    AGENT_TOOL_VERSIONS,
    AGENT_TOOLS,
    BIN_DIR,
    ENTRYPOINT,
    PAYLOAD_DIR,
    PAYLOAD_FILES,
    PAYLOAD_PACKAGE_DIR,
    PINNED_VERSION,
    PROTECTED_CONFIGS,
    TOOL_HOME,
    agent_tool_problems,
    check_script,
    install_script,
    payload_bytes,
)
from raidar.components.provision import PROVISION_CHANNEL
from raidar.components.provision_v2 import trial_provisions
from tests.component_support import compile_uses, use, write_jsonl, write_step_reports
from tests.python_guardrails_support import PACKS, host_home

REF = "python-guardrails@5"
SLUG = "00-python-guardrails"
WORKSPACE_CONFIGS = ("ruff.toml", "mypy.ini")
NEW_FILES = {"bin/pytest", "bin/coverage", "lib/agent_tools/run-module"}


def _sha(relative: str) -> str:
    return sha256_bytes(payload_bytes(relative))


# ---- contract -------------------------------------------------------------------------


def test_v5_is_a_python_service_that_fingerprints_its_whole_payload() -> None:
    definition = ComponentRegistry().get(REF)
    manifest = definition.manifest

    assert (manifest.kind, definition.runtimes) == (ComponentKind.SERVICE, ("python",))
    for relative in PAYLOAD_FILES:
        assert f"{PAYLOAD_PACKAGE_DIR}/{relative}" in manifest.fingerprint
    assert "raidar/components/builtins/guardrails_v5.py" in manifest.fingerprint
    shipped = {
        path.relative_to(PAYLOAD_DIR).as_posix()
        for path in PAYLOAD_DIR.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and ".ruff_cache" not in path.parts
    }
    assert shipped - set(PAYLOAD_FILES) == {"requirements.in"}


def test_only_the_version_strings_differ_from_v4_beside_the_agents_tools() -> None:
    assert set(PAYLOAD_FILES) - set(guardrails_v4.PAYLOAD_FILES) == NEW_FILES
    changed = set()
    for relative in guardrails_v4.PAYLOAD_FILES:
        v4, v5 = guardrails_v4.payload_bytes(relative).decode(), payload_bytes(relative).decode()
        if v5 == v4:
            continue
        changed.add(relative)
        renamed = v4.replace("python-guardrails@4", "python-guardrails@5")
        assert v5 == renamed.replace(
            "Scope (python-guardrails@5)", "Scope (python-guardrails@4 and @5)"
        ), relative
    assert "lib/guardrails/gate.py" in changed
    assert guardrails_v4.PINNED_VERSION.replace("@4", "@5") == PINNED_VERSION


def test_the_install_is_v4s_plus_the_agents_tool_links_and_smokes() -> None:
    (provision,) = trial_provisions(compile_uses(use(REF, workspace_configs=True)))
    script = install_script()

    assert provision.install == script
    assert provision.check == check_script()
    for name, target in AGENT_TOOLS.items():
        assert f"ln -sfn {target} {BIN_DIR / name}" in script
    assert 'runuser -u "$RAIDAR_AGENT_USER"' in script
    for line in ("install_smoke_agent_pytest_rc", "install_smoke_agent_ruff_format_rc"):
        assert line in script
    for executable in NEW_FILES:
        assert provision.payload[executable].executable
    assert provision.payload[ENTRYPOINT].executable
    assert guardrails_v4.check_script() in check_script()


# ---- the probe ------------------------------------------------------------------------


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
        "install_agent_tool_versions": AGENT_TOOL_VERSIONS,
        "install_smoke_agent_pytest_rc": "0",
        "install_smoke_agent_coverage_rc": "0",
        "install_smoke_agent_ruff_format_rc": "0",
    }
    for name, target in AGENT_TOOLS.items():
        values[f"agent_tool_{name}"] = str(BIN_DIR / name)
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


def test_the_probe_requires_the_agents_tools_at_every_step(tmp_path: Path) -> None:
    compiled = compile_uses(use(REF, workspace_configs=True))

    assert compiled.probe_step(_agent_dir(tmp_path / "ok", _values()), 0).passed
    missing = _values(agent_tool_pytest="missing", agent_tool_pytest_target="missing")
    report = compiled.probe_step(_agent_dir(tmp_path / "gone", missing), 3)
    assert not report.passed
    assert "pytest on the agent's PATH is 'missing'" in json.dumps(report.to_json())
    failed = _values(install_smoke_agent_ruff_format_rc="1")
    assert not compiled.probe_step(_agent_dir(tmp_path / "smoke", failed), 0).passed


def test_agent_tool_problems_name_each_gap() -> None:
    assert agent_tool_problems(_values()) == []
    problems = agent_tool_problems(
        _values(install_agent_tool_versions="pytest 8.0.0", install_smoke_agent_coverage_rc="2")
    )
    assert any("answered 'pytest 8.0.0'" in problem for problem in problems)
    assert "agent smoke coverage exits '2'" in problems


# ---- the wrappers on the host ---------------------------------------------------------


def _home(tmp_path: Path) -> Path:
    home = host_home(tmp_path / "home", tools=("python", "ruff", "coverage"), pack=PACKS[5])
    (home / "venv" / "lib").symlink_to(Path(sys.executable).parent.parent / "lib")
    return home


def _project(root: Path) -> Path:
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
    (root / "tests" / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add() -> None:\n    assert add(2, 3) == 5\n"
    )
    return root


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False, timeout=120)


def test_the_wrappers_run_the_packs_tools_with_the_projects_interpreter(tmp_path: Path) -> None:
    home = _home(tmp_path)
    project = _project(tmp_path / "app")
    (project / ".venv" / "bin").mkdir(parents=True)
    (project / ".venv" / "bin" / "python").symlink_to(sys.executable)

    tested = _run([str(home / "bin" / "pytest"), "-q", "-p", "no:cacheprovider"], project)
    covered = _run(
        [str(home / "bin" / "coverage"), "run", "--data-file", str(tmp_path / ".cov")]
        + ["-m", "pytest", "-q", "-p", "no:cacheprovider"],
        project,
    )

    assert tested.returncode == 0, tested.stdout + tested.stderr
    assert "1 passed" in tested.stdout
    assert covered.returncode == 0, covered.stdout + covered.stderr
    assert (tmp_path / ".cov").is_file()


def test_a_wrapper_without_the_packs_site_packages_says_so(tmp_path: Path) -> None:
    home = host_home(tmp_path / "home", tools=("python",), pack=PACKS[5])

    done = _run([str(home / "bin" / "pytest"), "--version"], tmp_path)

    assert done.returncode == 126
    assert "site-packages are missing" in done.stderr
