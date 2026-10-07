"""python-guardrails@8: @7 plus its safe auto-fixers as ``guardrail-check --autofix``.

Covers the contract (@7's payload byte for byte except the version strings, the
gate's ``--autofix`` flag and one module, ``lib/guardrails/autofix.py``; the
config files are @7's bytes), the install (@7's plus the autofix smoke), the
probe on @8's pin and the smoke, and ``--autofix`` itself with this
environment's Ruff (``host_home``; its behaviour does not depend on the pinned
version): it fixes ``FORMAT`` and ``I001``, never applies an unsafe fix
(``F841``), never touches anything outside the scope's Python files, writes
nothing when a fixer fails, and never writes through a symlink. The pinned tools
run the same canaries in the pack image (``make guardrails-verify``) and
``tests/test_guardrails_v8_trial.py`` runs it under guardrail-gate@4.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from raidar.components import ComponentKind, ComponentRegistry
from raidar.components.builtins import guardrails_v7
from raidar.components.builtins.guardrails_v8 import (
    AUTOFIX_COMMAND,
    AUTOFIX_FIXED,
    AUTOFIX_KEPT,
    AUTOFIX_MODULE,
    CONFIG_FILES,
    PAYLOAD_DIR,
    PAYLOAD_FILES,
    PAYLOAD_PACKAGE_DIR,
    PINNED_VERSION,
    SMOKE_AUTOFIX,
    _autofix_smoke_lines,
    autofix_problems,
    install_script,
    payload_bytes,
)
from raidar.components.integrity import condition_step_flags
from raidar.components.provision_v2 import trial_provisions
from tests.component_support import compile_uses, use
from tests.python_guardrails_support import PACKS, host_home, implemented_rules
from tests.test_component_python_guardrails_v7 import _agent_dir
from tests.test_component_python_guardrails_v7 import _values as v7_values

REF = "python-guardrails@8"
UNSORTED = "import sys\nimport os\n\nprint(os.sep, sys.platform)\n"
UNFORMATTED = "def add(a,b) :\n    return  a+b\n"
UNSAFE = "def f() -> int:\n    unused = 1\n    return 2\n"


# ---- contract -------------------------------------------------------------------------


def test_v8_is_a_python_service_that_fingerprints_its_whole_payload() -> None:
    definition = ComponentRegistry().get(REF)
    manifest = definition.manifest

    assert (manifest.kind, definition.runtimes) == (ComponentKind.SERVICE, ("python",))
    for relative in PAYLOAD_FILES:
        assert f"{PAYLOAD_PACKAGE_DIR}/{relative}" in manifest.fingerprint
    assert "raidar/components/builtins/guardrails_v8.py" in manifest.fingerprint
    shipped = {
        path.relative_to(PAYLOAD_DIR).as_posix()
        for path in PAYLOAD_DIR.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and ".ruff_cache" not in path.parts
    }
    assert shipped - set(PAYLOAD_FILES) == {"requirements.in"}
    assert set(PAYLOAD_FILES) - set(guardrails_v7.PAYLOAD_FILES) == {AUTOFIX_MODULE}
    assert AUTOFIX_COMMAND in manifest.description


def test_only_the_version_strings_and_the_autofix_flag_differ_from_v7() -> None:
    changed = set()
    for relative in guardrails_v7.PAYLOAD_FILES:
        v7, v8 = guardrails_v7.payload_bytes(relative).decode(), payload_bytes(relative).decode()
        if v8 == v7:
            continue
        changed.add(relative)
        if relative == "lib/guardrails/gate.py":
            continue  # the --autofix flag: test_the_gate_differs_from_v7_only_by_autofix
        renamed = v7.replace("python-guardrails@7", "python-guardrails@8")
        assert v8 == renamed, relative
    assert "lib/guardrails/gate.py" in changed
    for relative in CONFIG_FILES:
        assert relative not in changed, f"{relative} must stay @7's bytes (the probe reuses them)"
    assert guardrails_v7.PINNED_VERSION.replace("@7", "@8") == PINNED_VERSION
    assert (PAYLOAD_DIR / "requirements.in").read_bytes() == (
        guardrails_v7.PAYLOAD_DIR / "requirements.in"
    ).read_bytes()


def test_the_gate_differs_from_v7_only_by_autofix() -> None:
    v7 = guardrails_v7.payload_bytes("lib/guardrails/gate.py").decode().splitlines()
    v8 = payload_bytes("lib/guardrails/gate.py").decode().splitlines()
    removed = [line for line in v7 if line not in v8]
    assert all(
        "python-guardrails@7" in line
        or "Scope (python-guardrails@4 to @7)" in line
        or "def _json_path" in line
        or "def _report_path" in line
        or "gate-{index}.json" in line
        or "return _json_path(args)" in line
        or "[--fix]" in line
        for line in removed
    ), removed
    text = "\n".join(v8)
    assert 'parser.add_argument("--autofix", action="store_true")' in text
    assert "from guardrails.autofix import" in text


def test_v8s_canaries_are_v7s() -> None:
    specs = json.loads(PACKS[8].specs_path.read_text())

    assert PACKS[8].specs == PACKS[7].specs
    assert implemented_rules(specs, PACKS[8]) == implemented_rules(specs, PACKS[7])


def test_the_install_is_v7s_plus_the_autofix_smoke() -> None:
    (provision,) = trial_provisions(compile_uses(use(REF, workspace_configs=True)))
    script = install_script()

    assert provision.install == script
    assert provision.check == guardrails_v7.check_script()
    assert set(provision.payload) == set(PAYLOAD_FILES)
    v7_lines = guardrails_v7.install_script().splitlines()
    assert [line for line in v7_lines if line not in script.splitlines()] == []
    smoke = "\n".join(f"  {line}" for line in _autofix_smoke_lines())
    assert smoke in script
    assert script.replace(smoke + "\n", "", 1) == guardrails_v7.install_script()
    assert "--autofix" in smoke
    assert "install_smoke_autofix_rc" in smoke
    assert "set -euo pipefail" in script


# ---- probe and flags ------------------------------------------------------------------


def _values(**overrides: str) -> dict[str, str]:
    values = v7_values(
        install_version=PINNED_VERSION,
        install_smoke_autofix_rc="0",
        install_smoke_autofix_fixed="E203,F401,FORMAT,I001",
        install_smoke_autofix_after=AUTOFIX_KEPT,
    )
    return values | overrides


@pytest.mark.parametrize(
    ("case", "overrides"),
    [
        ("v7 pin", {"install_version": guardrails_v7.PINNED_VERSION}),
        ("autofix failed", {"install_smoke_autofix_rc": "126"}),
        ("format not fixed", {"install_smoke_autofix_fixed": "I001"}),
        ("isort not fixed", {"install_smoke_autofix_fixed": "FORMAT"}),
        ("unsafe fix applied", {"install_smoke_autofix_after": ""}),
        ("unsafe fix counted", {"install_smoke_autofix_fixed": "F841,FORMAT,I001"}),
        ("autofix smoke absent", {"install_smoke_autofix_rc": ""}),
        ("python -m pytest failed", {"install_smoke_agent_python_m_pytest_rc": "1"}),
    ],
)
def test_the_probe_requires_v8s_pin_and_the_autofix_smoke(
    tmp_path: Path, case: str, overrides: dict[str, str]
) -> None:
    compiled = compile_uses(use(REF, workspace_configs=True))

    good = compiled.probe_step(_agent_dir(tmp_path / "ok", _values()), 0)
    assert good.passed, good.to_json()
    assert "autofix fixed E203,F401,FORMAT,I001" in json.dumps(good.to_json())
    report = compiled.probe_step(_agent_dir(tmp_path / "bad", _values(**overrides)), 2)
    assert not report.passed, case


def test_autofix_problems_names_each_failure() -> None:
    assert autofix_problems(_values()) == []
    assert set(AUTOFIX_FIXED) == {"FORMAT", "I001"}
    assert autofix_problems(_values(install_smoke_autofix_after="")) == [
        "autofix smoke applied the unsafe F841 fix (left [''])"
    ]


def test_unmodified_copies_raise_no_flag(tmp_path: Path) -> None:
    compiled = compile_uses(use(REF, workspace_configs=True))
    assert condition_step_flags(compiled, _agent_dir(tmp_path, _values()), 3) == ()


# ---- --autofix with this environment's Ruff ----------------------------------------------


def _gate(home: Path, root: Path, *args: str, env: dict[str, str] | None = None) -> Any:
    return subprocess.run(
        ["bash", str(home / "bin" / "guardrail-check"), "--root", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "GUARDRAILS_HOME": str(home), **(env or {})},
        timeout=120,
    )


def _tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return host_home(tmp_path / "home", tools=("python", "ruff"), pack=PACKS[8])


def _autofix(home: Path, root: Path, tmp_path: Path) -> tuple[int, dict[str, object]]:
    out = tmp_path / "autofix.json"
    done = _gate(home, root, "--autofix", "--json-out", str(out))
    return done.returncode, json.loads(out.read_text())


def test_autofix_fixes_format_and_import_order_and_never_an_unsafe_fix(
    home: Path, tmp_path: Path
) -> None:
    root = tmp_path / "ws"
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_text(UNFORMATTED)
    (root / "main.py").write_text(UNSORTED)
    (root / "tests" / "test_unsafe.py").write_text(UNSAFE)
    (root / "notes.txt").write_text("x  =  1\n")

    rc, report = _autofix(home, root, tmp_path)

    assert rc == 0, report
    assert report["schema"] == "python-guardrails.autofix-report/1"
    assert (report["pack"], report["verdict"], report["error"]) == (
        "python-guardrails@8",
        "fixed",
        None,
    )
    assert report["files_changed"] == ["calc.py", "main.py"]
    fixed = report["codes_fixed"]
    assert isinstance(fixed, dict)
    assert {"FORMAT", "I001"} <= set(fixed)
    assert AUTOFIX_KEPT not in fixed
    assert report["codes_after"] == {AUTOFIX_KEPT: 1}, "the unsafe fix is never applied"
    assert (root / "tests" / "test_unsafe.py").read_text() == UNSAFE
    assert (root / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"
    assert (root / "main.py").read_text().startswith("import os\nimport sys\n")
    assert (root / "notes.txt").read_text() == "x  =  1\n", "only the scope's Python files"
    assert report["lines_changed"] == 6
    assert report["codes_fixed_blocking"] == fixed, "FORMAT and I001 block under the policy"
    tools = report["tools"]
    assert isinstance(tools, list)
    assert {tool["name"] for tool in tools} <= {
        "ruff-check",
        "ruff-format",
        "ruff-fix",
        "ruff-format-recheck",
    }, "never mypy, never the tests"


def test_a_clean_tree_is_unchanged_and_a_second_pass_changes_nothing(
    home: Path, tmp_path: Path
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text(UNFORMATTED)
    _autofix(home, root, tmp_path)
    before = _tree(root)

    rc, report = _autofix(home, root, tmp_path)

    assert (rc, report["verdict"], report["files_changed"], report["codes_fixed"]) == (
        0,
        "unchanged",
        [],
        {},
    )
    assert _tree(root) == before


def test_a_failing_fixer_writes_nothing(home: Path, tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text(UNFORMATTED)
    before = _tree(root)
    ruff = home / "venv" / "bin" / "ruff"
    real = ruff.resolve()
    ruff.unlink()
    ruff.write_text(f'#!/bin/sh\n[ "$1" = format ] && exit 3\nexec {real} "$@"\n')
    ruff.chmod(0o755)

    rc, report = _autofix(home, root, tmp_path)

    assert (rc, report["verdict"], report["error_type"]) == (126, "error", "tool_failed")
    assert _tree(root) == before, "atomic: the workspace is untouched"


def test_a_missing_ruff_is_an_error_not_a_pass(home: Path, tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text(UNFORMATTED)
    (home / "venv" / "bin" / "ruff").unlink()

    rc, report = _autofix(home, root, tmp_path)

    assert (rc, report["verdict"], report["error_type"]) == (126, "error", "tool_missing")
    assert (root / "calc.py").read_text() == UNFORMATTED


def test_autofix_never_writes_through_a_symlink_or_outside_the_root(
    home: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text(UNFORMATTED)
    root = tmp_path / "ws"
    root.mkdir()
    (root / "link.py").symlink_to(outside)
    (root / "calc.py").write_text(UNFORMATTED)

    rc, report = _autofix(home, root, tmp_path)

    assert rc == 0, report
    assert report["files_changed"] == ["calc.py"]
    assert outside.read_text() == UNFORMATTED
    assert (root / "link.py").is_symlink()


def test_autofix_reports_to_the_gate_report_dir_and_refuses_fix_with_it(
    home: Path, tmp_path: Path
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text(UNFORMATTED)
    reports = tmp_path / "reports"

    done = _gate(home, root, "--autofix", env={"RAIDAR_GATE_REPORT_DIR": str(reports)})

    assert done.returncode == 0
    assert "AUTOFIX FIXED (1 file(s) changed" in done.stdout
    assert [path.name for path in reports.iterdir()] == ["autofix-1.json"]
    both = _gate(home, root, "--autofix", "--fix", "--json-out", str(tmp_path / "x.json"))
    assert both.returncode == 126
    assert json.loads((tmp_path / "x.json").read_text())["error_type"] == "usage"


def test_the_smoke_file_shows_each_kind_of_fix(home: Path, tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "one.py").write_text(SMOKE_AUTOFIX)

    rc, report = _autofix(home, root, tmp_path)

    assert rc == 0
    fixed = report["codes_fixed"]
    assert isinstance(fixed, dict)
    assert set(AUTOFIX_FIXED) <= set(fixed)
    assert report["codes_after"] == {AUTOFIX_KEPT: 1}
