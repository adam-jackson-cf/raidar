"""The python-guardrails gate on the host (this environment's tools, not the pins).

Exit codes, the report, tiers and protection evidence, the tests check, the
scope (extensionless scripts, ``.py`` symlinks), the test-class rule and
``--autofix`` run with this environment's Python, Ruff, mypy, pytest and
coverage through ``host_home``; none of it depends on a tool version. The
pinned tools run the same canaries in the pack image
(``make guardrails-verify``); ``test_guardrails_trial.py`` runs the gate under
guardrail-gate in a real trial.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from raidar.subject.treatment.catalog.guardrails import (
    AUTOFIX_FIXED,
    AUTOFIX_KEPT,
    PAYLOAD_DIR,
    SMOKE_AUTOFIX,
    WORKSPACE_CONFIGS,
    payload_bytes,
)
from tests.python_guardrails_support import host_home

REF = "python-guardrails@8"
PYTHON_TOOLS = ("python", "ruff", "mypy", "coverage")
MODULE = 'def double(x: int) -> int:\n    """Double x."""\n    return 2 * x\n'
TRIPLE = '\n\ndef triple(x: int) -> int:\n    """Triple x."""\n    return 3 * x\n'
UNIT_TEST = (
    "import unittest\n\nfrom mod import double\n\n\n"
    "class DoubleTest(unittest.TestCase):\n"
    "    def test_double(self) -> None:\n"
    "        self.assertEqual(double(2), {expected})\n"
)
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
UNSORTED = "import sys\nimport os\n\nprint(os.sep, sys.platform)\n"
UNFORMATTED = "def add(a,b) :\n    return  a+b\n"
UNSAFE = "def f() -> int:\n    unused = 1\n    return 2\n"


def _gate(
    home: Path, workspace: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    environment = {k: v for k, v in os.environ.items() if k != "RAIDAR_GATE_REPORT_DIR"}
    environment |= {"GUARDRAILS_HOME": str(home), **(env or {})}
    return subprocess.run(
        ["sh", str(home / "bin" / "guardrail-check"), "--root", str(workspace), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env=environment,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    for name in WORKSPACE_CONFIGS:
        (root / name).write_bytes(payload_bytes(f"config/{name}"))
    return root


# ---- exit codes, the report, tiers and protection evidence ---------------------------------


def test_a_clean_workspace_passes_and_reports_identical_config_copies(
    tmp_path: Path, workspace: Path
) -> None:
    (workspace / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
    report = tmp_path / "report.json"

    done = _gate(host_home(tmp_path), workspace, "--no-tests", "--json-out", str(report))

    assert done.returncode == 0, done.stdout
    assert done.stdout.startswith("guardrail-check: PASSED (0 blocking")
    data = json.loads(report.read_text())
    assert (data["schema"], data["pack"], data["verdict"]) == (
        "python-guardrails.gate-report/1",
        REF,
        "passed",
    )
    assert {name: entry["state"] for name, entry in data["workspace_configs"].items()} == {
        "ruff.toml": "identical",
        "mypy.ini": "identical",
    }
    assert not any(path.name.endswith("_cache") for path in workspace.iterdir())


def test_a_refusal_lists_findings_and_an_edited_copy_cannot_loosen_the_gate(
    tmp_path: Path, workspace: Path
) -> None:
    (workspace / "bad.py").write_text("import os\n\n\ndef f(x):\n    return x\n")
    with (workspace / "ruff.toml").open("a") as handle:
        handle.write('extend-ignore = ["F401"]\n')
    (workspace / "mypy.ini").unlink()
    report = tmp_path / "report.json"
    before = (workspace / "bad.py").read_bytes()

    done = _gate(host_home(tmp_path), workspace, "--no-tests", "--json-out", str(report))

    assert done.returncode == 1
    assert "gate-check ruff fail" in done.stdout
    assert "gate-check mypy fail" in done.stdout
    assert "bad.py:1:8: F401" in done.stdout
    assert "MYPY.no-untyped-def" in done.stdout
    data = json.loads(report.read_text())
    assert data["counts"]["block"] == {"F401": 1, "MYPY.no-untyped-def": 1}
    states = {name: entry["state"] for name, entry in data["workspace_configs"].items()}
    assert states == {"ruff.toml": "modified", "mypy.ini": "missing"}
    assert "workspace_configs" not in done.stdout, "the report is never printed"
    assert (workspace / "bad.py").read_bytes() == before, "check-only by default"


def test_tiers_warn_and_off_never_refuse(tmp_path: Path, workspace: Path) -> None:
    (workspace / "tests").mkdir()
    (workspace / "tests" / "test_calc.py").write_text(
        "import unittest\n\n\nclass T(unittest.TestCase):\n"
        "    def test_one(self) -> None:\n        self.assertEqual(1, 1)\n"
    )
    (workspace / "calc.py").write_text('def add(a: int) -> int:\n    """ add"""\n    return a\n')
    report = tmp_path / "report.json"

    done = _gate(host_home(tmp_path), workspace, "--no-tests", "--json-out", str(report))

    assert done.returncode == 0, done.stdout
    data = json.loads(report.read_text())
    assert not data["counts"]["block"]
    assert any(code.startswith("D") for code in data["counts"]["warn"])
    assert "PT009" not in json.dumps(data), "PT009 is off: unittest is the image's runner"


def test_the_report_goes_to_the_hook_runners_report_dir_without_a_flag(
    tmp_path: Path, workspace: Path
) -> None:
    reports = tmp_path / "reports"
    home = host_home(tmp_path)
    for _ in range(2):
        done = _gate(home, workspace, "--no-tests", env={"RAIDAR_GATE_REPORT_DIR": str(reports)})
        assert done.returncode == 0
    assert sorted(path.name for path in reports.iterdir()) == ["gate-1.json", "gate-2.json"]


def test_an_unwritable_report_never_changes_the_verdict(tmp_path: Path, workspace: Path) -> None:
    (workspace / "bad.py").write_text("import os\n")
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory")

    done = _gate(
        host_home(tmp_path), workspace, "--no-tests", "--json-dir", str(blocker / "reports")
    )

    assert done.returncode == 1
    assert "could not create the report dir" in done.stderr


def test_a_missing_tool_or_a_crash_is_a_gate_error_never_a_verdict(
    tmp_path: Path, workspace: Path
) -> None:
    (workspace / "calc.py").write_text("def one() -> int:\n    return 1\n")
    missing = _gate(host_home(tmp_path / "a", tools=("python", "ruff")), workspace, "--no-tests")
    crashed_home = host_home(tmp_path / "b")
    (crashed_home / "config" / "policy.toml").write_text("default = [unterminated\n")
    crashed = _gate(crashed_home, workspace, "--no-tests")
    no_python = _gate(host_home(tmp_path / "c", tools=()), workspace, "--no-tests")

    for done in (missing, crashed, no_python):
        assert done.returncode == 126, done.stdout
        assert done.stdout.startswith("guardrail-check: ERROR, the gate could not run")
    assert "mypy is not installed" in missing.stdout


def test_version_names_the_pack_and_its_tools(tmp_path: Path, workspace: Path) -> None:
    home = host_home(tmp_path, tools=("ruff", "mypy"))
    # The isolated version probe imports pytest and coverage, which this interpreter may lack.
    stub = home / "venv" / "bin" / "python"
    stub.write_text(
        '#!/bin/sh\n[ "$1" = -I ] && [ "$2" = -c ] && '
        '{ echo "pytest 9.1.1, coverage 7.16.2"; exit 0; }\n'
        f'exec {sys.executable} "$@"\n'
    )
    stub.chmod(0o755)

    done = _gate(home, workspace, "--version")

    assert done.returncode == 0, done.stdout
    assert re.fullmatch(
        rf"{REF}: ruff [\d.]+, mypy [\d.]+, pytest 9\.1\.1, coverage 7\.16\.2\n", done.stdout
    )


def test_fix_is_explicit_and_rewrites_only_what_ruff_can_fix(
    tmp_path: Path, workspace: Path
) -> None:
    (workspace / "calc.py").write_text("import sys\nimport os\n\nprint(os, sys)\n")

    done = _gate(host_home(tmp_path), workspace, "--no-tests", "--fix")

    assert done.returncode == 0, done.stdout
    assert (workspace / "calc.py").read_text() == "import os\nimport sys\n\nprint(os, sys)\n"


# ---- the tests check --------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A gate home on this environment's tools, its site-packages included."""
    home = host_home(tmp_path, tools=PYTHON_TOOLS)
    (home / "venv" / "lib").symlink_to(Path(sys.executable).parent.parent / "lib")
    return home


def _workspace(root: Path, files: dict[str, str], links: dict[str, str] | None = None) -> Path:
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text)
    for link, target in (links or {}).items():
        (root / link).symlink_to(target)
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").symlink_to(sys.executable)
    return root


def _run(
    home: Path, root: Path, extra_env: dict[str, str] | None = None
) -> tuple[int, dict[str, Any]]:
    report = root.parent / f"{root.name}-report.json"
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


def _codes(report: dict[str, Any]) -> list[tuple[str, str]]:
    return sorted((f["code"], f["relpath"]) for f in report["findings"] if f["tier"] == "block")


def test_a_change_without_tests_is_refused_and_tests_that_cover_it_pass(
    tmp_path: Path, home: Path
) -> None:
    rc, report = _run(home, _workspace(tmp_path / "bare", {"mod.py": MODULE}))
    assert (rc, _block(report)) == (1, {"TEST.missing"})
    assert report["tests"]["required"] is True

    tested = {"mod.py": MODULE, "test_mod.py": UNIT_TEST.format(expected=4)}
    rc, report = _run(home, _workspace(tmp_path / "tested", tested))
    assert (rc, _block(report)) == (0, set())
    assert report["tests"]["tests_ran"] == 1
    assert report["tests"]["coverage_percent"] == 100.0


def test_failing_tests_of_either_runner_and_low_coverage_are_refused(
    tmp_path: Path, home: Path
) -> None:
    failing = {"mod.py": MODULE, "test_mod.py": UNIT_TEST.format(expected=5)}
    assert _block(_run(home, _workspace(tmp_path / "u", failing))[1]) == {"TEST.failed"}
    pytest_style = {
        "mod.py": MODULE,
        "test_mod.py": (
            "from mod import double\n\n\ndef test_double() -> None:\n    assert double(2) == 5\n"
        ),
    }
    _, report = _run(home, _workspace(tmp_path / "p", pytest_style))
    assert _block(report) == {"TEST.failed"}
    assert report["tests"]["runners"]["pytest"]["failed"] == 1
    uncovered = {"mod.py": MODULE + TRIPLE, "test_mod.py": UNIT_TEST.format(expected=4)}
    _, report = _run(home, _workspace(tmp_path / "c", uncovered))
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

    rc, report = _run(home, _workspace(tmp_path / "app", tested), outer)

    assert (rc, report["tests"]["coverage_percent"]) == (0, 100.0)
    assert not (tmp_path / "outer").exists()


def test_the_test_run_never_writes_the_workspace(tmp_path: Path, home: Path) -> None:
    writer = UNIT_TEST.format(expected=4).replace(
        "    def test_double(self) -> None:\n",
        '    def test_double(self) -> None:\n        open("written.txt", "w").close()\n',
    )
    root = _workspace(tmp_path / "app", {"mod.py": MODULE, "test_mod.py": writer})
    before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))

    _run(home, root)

    assert sorted(path.relative_to(root).as_posix() for path in root.rglob("*")) == before


def _commit_all(root: Path, *paths: str) -> None:
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    for argv in (["init", "-q"], ["add", *paths], ["commit", "-q", "-m", "base"]):
        subprocess.run([*git, *argv], cwd=root, check=True, capture_output=True)


def test_no_python_change_since_the_last_commit_needs_no_tests(tmp_path: Path, home: Path) -> None:
    root = _workspace(tmp_path / "app", {"mod.py": MODULE})
    _commit_all(root, "mod.py")

    rc, report = _run(home, root)
    assert (rc, report["tests"]["required"]) == (0, False)
    (root / "mod.py").write_text(MODULE + TRIPLE)
    assert _block(_run(home, root)[1]) == {"TEST.missing"}


def test_an_outer_repository_never_exempts_a_workspace_inside_it(
    tmp_path: Path, home: Path
) -> None:
    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / ".gitignore").write_text("app/\n")
    _commit_all(outer, ".gitignore")
    root = _workspace(outer / "app", {"mod.py": MODULE})

    rc, report = _run(home, root)

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


# ---- the scope ------------------------------------------------------------------------------


def test_an_extensionless_script_behind_a_py_link_is_checked_once_at_the_scripts_path(
    tmp_path: Path, home: Path
) -> None:
    files, links = {"forge": SCRIPT}, {"forge.py": "forge"}

    rc, report = _run(home, _workspace(tmp_path / "app", files, links))

    assert (rc, report["files_checked"], report["production_files"]) == (1, 1, 1)
    assert _codes(report) == [("F401", "forge"), ("TEST.missing", ".")]


def test_a_tested_script_is_measured_through_its_subprocess_and_passes(
    tmp_path: Path, home: Path
) -> None:
    files = {"forge": CLEAN_SCRIPT, "tests/unit/test_forge.py": SCRIPT_TEST}
    root = _workspace(tmp_path / "app", files, {"forge.py": "forge"})

    rc, report = _run(home, root)

    assert (rc, _codes(report)) == (0, [])
    assert report["tests"]["tests_ran"] == 1
    assert report["tests"]["owned_files"] == 1
    assert report["tests"]["coverage_percent"] >= 85.0


def test_a_py_symlink_counts_as_its_in_root_target_whatever_its_name(
    tmp_path: Path, home: Path
) -> None:
    root = _workspace(tmp_path / "app", {"impl": "import os\n"}, {"tool.py": "impl"})

    _, report = _run(home, root)

    assert report["files_checked"] == 1
    assert ("F401", "impl") in _codes(report)


def test_links_out_of_the_root_and_non_python_files_stay_out_of_scope(
    tmp_path: Path, home: Path
) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("VALUE: int = 2\n")
    files = {"notes": "#!/bin/sh\necho hi\n", "data": "import os\n", "ok.py": "VALUE: int = 1\n"}
    root = _workspace(tmp_path / "app", files, {"far.py": str(outside)})

    _, report = _run(home, root)

    assert report["files_checked"] == 1, "only ok.py: no link target outside the root"
    assert all(code != "F401" for code, _ in _codes(report)), "data is not a python script"


def test_a_change_to_an_extensionless_script_since_the_last_commit_needs_tests(
    tmp_path: Path, home: Path
) -> None:
    root = _workspace(tmp_path / "app", {"forge": CLEAN_SCRIPT})
    _commit_all(root, "forge")

    rc, report = _run(home, root)
    assert (rc, report["tests"]["required"]) == (0, False)
    (root / "forge").write_text(CLEAN_SCRIPT.replace("- 1", "- 0"))
    rc, report = _run(home, root)
    assert (rc, report["tests"]["required"]) == (1, True)
    assert ("TEST.missing", ".") in _codes(report)


# ---- the test-class rule, with this environment's Ruff --------------------------------------


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
    ("relative", "test_case", "refused"),
    [
        ("tests/unit/many_cases.py", True, False),
        ("test_top.py", True, False),
        ("pkg/many_test.py", True, False),
        ("pkg/test/cases.py", True, False),
        ("pkg/service.py", False, True),
        ("pkg/checks.py", True, True),
    ],
)
def test_a_21_method_class_is_refused_only_outside_test_files(
    tmp_path: Path, relative: str, test_case: bool, refused: bool
) -> None:
    ruff = _ruff()
    assert ruff is not None
    (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / relative).write_text(_many_methods(test_case))

    done = subprocess.run(
        [str(ruff), "check", "--no-cache", "--config", str(PAYLOAD_DIR / "config" / "ruff.toml")]
        + ["--select", "PLR0904", "--output-format", "concise", "."],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert ("PLR0904" in done.stdout) is refused


# ---- --autofix with this environment's Ruff ----------------------------------------------------


def _tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


@pytest.fixture
def ruff_home(tmp_path: Path) -> Path:
    return host_home(tmp_path / "home", tools=("python", "ruff"))


def _autofix(home: Path, root: Path, tmp_path: Path) -> tuple[int, dict[str, object]]:
    out = tmp_path / "autofix.json"
    done = _gate(home, root, "--autofix", "--json-out", str(out))
    return done.returncode, json.loads(out.read_text())


def test_autofix_fixes_format_and_import_order_and_never_an_unsafe_fix(
    ruff_home: Path, tmp_path: Path
) -> None:
    root = tmp_path / "ws"
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_text(UNFORMATTED)
    (root / "main.py").write_text(UNSORTED)
    (root / "tests" / "test_unsafe.py").write_text(UNSAFE)
    (root / "notes.txt").write_text("x  =  1\n")

    rc, report = _autofix(ruff_home, root, tmp_path)

    assert rc == 0, report
    assert report["schema"] == "python-guardrails.autofix-report/1"
    assert (report["pack"], report["verdict"], report["error"]) == (REF, "fixed", None)
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
    ruff_home: Path, tmp_path: Path
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text(UNFORMATTED)
    _autofix(ruff_home, root, tmp_path)
    before = _tree(root)

    rc, report = _autofix(ruff_home, root, tmp_path)

    assert (rc, report["verdict"], report["files_changed"], report["codes_fixed"]) == (
        0,
        "unchanged",
        [],
        {},
    )
    assert _tree(root) == before


def test_a_failing_fixer_writes_nothing(ruff_home: Path, tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text(UNFORMATTED)
    before = _tree(root)
    ruff = ruff_home / "venv" / "bin" / "ruff"
    real = ruff.resolve()
    ruff.unlink()
    ruff.write_text(f'#!/bin/sh\n[ "$1" = format ] && exit 3\nexec {real} "$@"\n')
    ruff.chmod(0o755)

    rc, report = _autofix(ruff_home, root, tmp_path)

    assert (rc, report["verdict"], report["error_type"]) == (126, "error", "tool_failed")
    assert _tree(root) == before, "atomic: the workspace is untouched"


def test_a_missing_ruff_is_an_error_not_a_pass(ruff_home: Path, tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text(UNFORMATTED)
    (ruff_home / "venv" / "bin" / "ruff").unlink()

    rc, report = _autofix(ruff_home, root, tmp_path)

    assert (rc, report["verdict"], report["error_type"]) == (126, "error", "tool_missing")
    assert (root / "calc.py").read_text() == UNFORMATTED


def test_autofix_never_writes_through_a_symlink_or_outside_the_root(
    ruff_home: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text(UNFORMATTED)
    root = tmp_path / "ws"
    root.mkdir()
    (root / "link.py").symlink_to(outside)
    (root / "calc.py").write_text(UNFORMATTED)

    rc, report = _autofix(ruff_home, root, tmp_path)

    assert rc == 0, report
    assert report["files_changed"] == ["calc.py"]
    assert outside.read_text() == UNFORMATTED
    assert (root / "link.py").is_symlink()


def test_autofix_reports_to_the_gate_report_dir_and_refuses_fix_with_it(
    ruff_home: Path, tmp_path: Path
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text(UNFORMATTED)
    reports = tmp_path / "reports"

    done = _gate(ruff_home, root, "--autofix", env={"RAIDAR_GATE_REPORT_DIR": str(reports)})

    assert done.returncode == 0
    assert "AUTOFIX FIXED (1 file(s) changed" in done.stdout
    assert [path.name for path in reports.iterdir()] == ["autofix-1.json"]
    both = _gate(ruff_home, root, "--autofix", "--fix", "--json-out", str(tmp_path / "x.json"))
    assert both.returncode == 126
    assert json.loads((tmp_path / "x.json").read_text())["error_type"] == "usage"


def test_the_smoke_file_shows_each_kind_of_fix(ruff_home: Path, tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "one.py").write_text(SMOKE_AUTOFIX)

    rc, report = _autofix(ruff_home, root, tmp_path)

    assert rc == 0
    fixed = report["codes_fixed"]
    assert isinstance(fixed, dict)
    assert set(AUTOFIX_FIXED) <= set(fixed)
    assert report["codes_after"] == {AUTOFIX_KEPT: 1}
