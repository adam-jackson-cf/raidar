"""python-guardrails@7: @6 whose pinned pytest and coverage the image's own Python imports.

Covers the contract (@6's payload byte for byte except the version strings, plus
``lib/agent_tools/expose-test-tools``), the canary specs (@6's: the rules are
@6's), the install (@6's plus the agent-site, the ``.pth`` file and the agent's
``python -m`` smokes) and per-step check (@6's plus the ``.pth`` and agent-site
listing), the probe on @7's pin, the config-copy flags against @7's own bytes,
and ``expose-test-tools`` itself against a fake site-packages. The install runs
in the pack image in ``make guardrails-verify`` and in
``tests/test_guardrails_v7_trial.py``.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

from raidar.components import ComponentKind, ComponentRegistry
from raidar.components.builtins import guardrails_v5, guardrails_v6
from raidar.components.builtins._common import sha256_bytes
from raidar.components.builtins.guardrails_v7 import (
    AGENT_SITE,
    ENTRYPOINT,
    EXPOSE_TEST_TOOLS,
    ISOLATION_SMOKE,
    PAYLOAD_DIR,
    PAYLOAD_FILES,
    PAYLOAD_PACKAGE_DIR,
    PINNED_VERSION,
    PROTECTED_CONFIGS,
    PTH_NAME,
    PYTHON_MODULE_SMOKES,
    PYTHON_MODULE_VERSIONS,
    TOOL_HOME,
    check_script,
    config_flags,
    install_script,
    payload_bytes,
    protection_flags,
)
from raidar.components.integrity import condition_step_flags
from raidar.components.provision import PROVISION_CHANNEL
from raidar.components.provision_v2 import trial_provisions
from tests.component_support import compile_uses, use, write_jsonl, write_step_reports
from tests.python_guardrails_support import PACKS, implemented_rules, uncovered_pack_entries

REF = "python-guardrails@7"
SLUG = "00-python-guardrails"
WORKSPACE_CONFIGS = ("ruff.toml", "mypy.ini")


def _sha(relative: str) -> str:
    return sha256_bytes(payload_bytes(relative))


# ---- contract -------------------------------------------------------------------------


def test_v7_is_a_python_service_that_fingerprints_its_whole_payload() -> None:
    definition = ComponentRegistry().get(REF)
    manifest = definition.manifest

    assert (manifest.kind, definition.runtimes) == (ComponentKind.SERVICE, ("python",))
    for relative in PAYLOAD_FILES:
        assert f"{PAYLOAD_PACKAGE_DIR}/{relative}" in manifest.fingerprint
    assert "raidar/components/builtins/guardrails_v7.py" in manifest.fingerprint
    shipped = {
        path.relative_to(PAYLOAD_DIR).as_posix()
        for path in PAYLOAD_DIR.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and ".ruff_cache" not in path.parts
    }
    assert shipped - set(PAYLOAD_FILES) == {"requirements.in"}
    assert set(PAYLOAD_FILES) - set(guardrails_v6.PAYLOAD_FILES) == {EXPOSE_TEST_TOOLS}


def test_only_the_version_strings_differ_from_v6() -> None:
    changed = set()
    for relative in guardrails_v6.PAYLOAD_FILES:
        v6, v7 = guardrails_v6.payload_bytes(relative).decode(), payload_bytes(relative).decode()
        if v7 == v6:
            continue
        changed.add(relative)
        renamed = v6.replace("python-guardrails@6", "python-guardrails@7").replace(
            "Scope (python-guardrails@4 to @6)", "Scope (python-guardrails@4 to @7)"
        )
        assert v7 == renamed, relative
    assert {"config/policy.toml", "lib/guardrails/gate.py"} <= changed
    assert "config/ruff.toml" not in changed
    assert "config/mypy.ini" not in changed
    assert guardrails_v6.PINNED_VERSION.replace("@6", "@7") == PINNED_VERSION
    assert (PAYLOAD_DIR / "requirements.in").read_bytes() == (
        guardrails_v6.PAYLOAD_DIR / "requirements.in"
    ).read_bytes()


def test_v7s_canaries_are_v6s() -> None:
    specs = json.loads(PACKS[7].specs_path.read_text())

    assert PACKS[7].specs == PACKS[6].specs
    assert uncovered_pack_entries(specs, PACKS[7]) == []
    assert implemented_rules(specs, PACKS[7]) == implemented_rules(specs, PACKS[6])


def test_the_install_adds_the_agent_site_the_pth_file_and_the_python_m_smokes() -> None:
    (provision,) = trial_provisions(compile_uses(use(REF, workspace_configs=True)))
    script = install_script()

    assert provision.install == script
    assert provision.check == check_script()
    assert set(provision.payload) == set(PAYLOAD_FILES)
    for relative in ("bin/pytest", "bin/coverage", "lib/agent_tools/run-module", ENTRYPOINT):
        assert provision.payload[relative].executable
    for line in guardrails_v5.install_script().splitlines():
        if not line.startswith('rm -rf "$home" "$record"'):
            assert line in script.splitlines(), line
    assert f'"$home/venv/bin/python" "$home/{EXPOSE_TEST_TOOLS}" {AGENT_SITE}' in script
    assert f'pth="$purelib/{PTH_NAME}"' in script
    for smoke in (*PYTHON_MODULE_SMOKES, ISOLATION_SMOKE):
        assert f"install_smoke_agent_{smoke}_rc" in script
    # The test tools are exposed after the venv exists and before the tree is protected.
    order = [
        script.index("uv pip install"),
        script.index(EXPOSE_TEST_TOOLS),
        script.index('chown -R root:root "$home"'),
    ]
    assert order == sorted(order)
    assert check_script().startswith(guardrails_v5.check_script())
    assert "agent_python_pth=" in check_script()
    assert "agent_site_digest=" in check_script()


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
        "install_agent_python_module_versions": PYTHON_MODULE_VERSIONS,
        **{f"install_smoke_agent_{smoke}_rc": "0" for smoke in PYTHON_MODULE_SMOKES},
        f"install_smoke_agent_{ISOLATION_SMOKE}_rc": "1",
        "install_agent_site_digest": "s" * 64,
        "agent_site_digest": "s" * 64,
        "agent_python_pth": str(AGENT_SITE),
        "agent_python_pth_owner": "root",
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


@pytest.mark.parametrize(
    ("case", "overrides"),
    [
        ("v6 pin", {"install_version": guardrails_v6.PINNED_VERSION}),
        ("python -m pytest failed", {"install_smoke_agent_python_m_pytest_rc": "1"}),
        ("python3 -m coverage failed", {"install_smoke_agent_python3_m_coverage_rc": "1"}),
        ("mypy importable", {f"install_smoke_agent_{ISOLATION_SMOKE}_rc": "0"}),
        ("isolation not run", {f"install_smoke_agent_{ISOLATION_SMOKE}_rc": ""}),
        ("module versions", {"install_agent_python_module_versions": "pytest 8.0.0, coverage"}),
        ("pth missing", {"agent_python_pth": "missing"}),
        ("pth not root", {"agent_python_pth_owner": "agent"}),
        ("agent-site changed", {"agent_site_digest": "x" * 64}),
        ("ruff gone", {"agent_tool_ruff": "missing", "agent_tool_ruff_target": "missing"}),
    ],
)
def test_the_probe_requires_v7s_pin_and_the_image_python_running_the_tools(
    tmp_path: Path, case: str, overrides: dict[str, str]
) -> None:
    compiled = compile_uses(use(REF, workspace_configs=True))

    assert compiled.probe_step(_agent_dir(tmp_path / "ok", _values()), 0).passed
    report = compiled.probe_step(_agent_dir(tmp_path / "bad", _values(**overrides)), 2)
    assert not report.passed, case


def test_unmodified_copies_and_protected_configs_raise_no_flag_against_v7s_bytes(
    tmp_path: Path,
) -> None:
    """@6's comparison would call @7's protected policy.toml modified at every later step."""
    compiled = compile_uses(use(REF, workspace_configs=True))

    assert condition_step_flags(compiled, _agent_dir(tmp_path / "same", _values()), 3) == ()
    edited = _values(workspace_ruff_toml="e" * 64)
    assert config_flags(edited, [], 1) == [
        "workspace-config-modified: ruff.toml at step start (modified)"
    ]
    assert protection_flags(_values(), 2) == []
    assert guardrails_v6.protection_flags(_values(), 2) == [
        "protected-config-modified: config/policy.toml"
    ]


# ---- expose-test-tools against a fake site-packages -----------------------------------


def _expose_module() -> ModuleType:
    spec = importlib.util.spec_from_loader(
        "expose_test_tools",
        importlib.machinery.SourceFileLoader(
            "expose_test_tools", str(PAYLOAD_DIR / EXPOSE_TEST_TOOLS)
        ),
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dist(site: Path, name: str, version: str, files: list[str], requires: list[str]) -> None:
    info = site / f"{name}-{version}.dist-info"
    info.mkdir(parents=True)
    metadata = [f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}"]
    metadata += [f"Requires-Dist: {line}" for line in requires]
    (info / "METADATA").write_text("\n".join(metadata) + "\n")
    record = [*files, f"{info.name}/METADATA", f"{info.name}/RECORD"]
    (info / "RECORD").write_text("".join(f"{item},,\n" for item in record))
    for item in files:
        if item.startswith(".."):
            continue
        path = site / item
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")


def _fake_site(site: Path, *, pytest_requires: list[str]) -> None:
    _dist(
        site,
        "pytest",
        "9.1.1",
        ["pytest/__init__.py", "_pytest/main.py", "py.py", "__pycache__/py.cpython-312.pyc"]
        + ["../../../bin/pytest"],
        pytest_requires,
    )
    for name in ("iniconfig", "packaging", "pluggy", "pygments"):
        _dist(site, name, "1.0", [f"{name}/__init__.py"], [])
    _dist(site, "coverage", "7.16.2", ["coverage/__init__.py", "a1_coverage.pth"], [])
    _dist(site, "mypy", "2.3.1", ["mypy/__init__.py"], [])


@pytest.fixture
def expose(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[ModuleType, Path]:
    site = tmp_path / "site-packages"
    monkeypatch.setattr(sys, "path", [str(site), *sys.path])
    importlib.invalidate_caches()
    return _expose_module(), site


def test_expose_links_the_test_tools_and_nothing_else(
    expose: tuple[ModuleType, Path], tmp_path: Path
) -> None:
    module, site = expose
    _fake_site(
        site,
        pytest_requires=[
            "iniconfig>=1",
            "packaging",
            "pluggy<2,>=1.5",
            "pygments>=2.7.2",
            'colorama>=0.4; sys_platform == "win32"',
            'exceptiongroup>=1; python_version < "3.11"',
            'requests; extra == "dev"',
        ],
    )
    target = tmp_path / "agent-site"

    assert module.main([str(target)]) == 0

    links = {path.name: os.readlink(path) for path in target.iterdir()}
    assert set(links) == {
        "pytest",
        "_pytest",
        "py.py",
        "pytest-9.1.1.dist-info",
        "coverage",
        "coverage-7.16.2.dist-info",
        *(
            f"{name}{suffix}"
            for name in ("iniconfig", "packaging", "pluggy", "pygments")
            for suffix in ("", "-1.0.dist-info")
        ),
    }
    assert all(Path(target_path).parent == site for target_path in links.values())
    assert "mypy" not in links
    assert "a1_coverage.pth" not in links


def test_expose_refuses_an_unexposed_runtime_requirement(
    expose: tuple[ModuleType, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module, site = expose
    _fake_site(site, pytest_requires=["pluggy", "tomli-w>=1"])

    assert module.main([str(tmp_path / "agent-site")]) == 1
    assert "tomli-w" in capsys.readouterr().err
    assert not (tmp_path / "agent-site").exists()


def test_expose_takes_exactly_one_target(expose: tuple[ModuleType, Path]) -> None:
    module, _ = expose
    assert module.main([]) == 2


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_scripts_are_valid_bash(tmp_path: Path) -> None:
    import subprocess

    for name, text in (("install.sh", install_script()), ("check.sh", check_script())):
        (tmp_path / name).write_text(text)
        done = subprocess.run(
            ["bash", "-n", str(tmp_path / name)], capture_output=True, text=True, check=False
        )
        assert done.returncode == 0, done.stderr
