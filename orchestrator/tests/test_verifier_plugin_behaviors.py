"""In-sandbox pytest plugin: outcome precedence, ledger and coverage plumbing.

The plugin normally runs only inside the grader sandbox; here it is loaded
in-process (ledger mode) and its coverage helpers are exercised directly.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from raidar.grading.sandbox import PLUGIN_FILE


@pytest.fixture(scope="module")
def plugin() -> ModuleType:
    spec = importlib.util.spec_from_file_location("raidar_oracle_plugin_under_test", PLUGIN_FILE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_plugin(plugin: ModuleType, tmp_path: Path, *, coverage: bool) -> Any:
    return plugin.OraclePlugin(
        ledger=tmp_path / "out" / "ledger.json",
        root=tmp_path / "app",
        scratch=tmp_path / "scratch",
        coverage=coverage,
        runtime="/runtime",
    )


@pytest.mark.parametrize(
    ("categories", "status"),
    [
        (["", "passed", ""], "passed"),
        (["", "xpassed", ""], "passed"),
        (["error"], "error"),
        (["", "passed", "error"], "error"),
        (["", "failed", ""], "failed"),
        (["skipped"], "skipped"),
        (["", "xfailed", ""], "skipped"),
        ([], "error"),
    ],
)
def test_phase_categories_collapse_with_error_precedence(
    plugin: ModuleType, categories: list[str], status: str
) -> None:
    assert plugin._node_status(categories) == status


def test_ledger_run_records_markers_phases_outcomes_and_process_events(
    plugin: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_checkpoint_1.py").write_text(
        "import subprocess, sys\n"
        "import pytest\n\n"
        "@pytest.mark.error\n"
        "def test_spawns():\n"
        "    subprocess.run([sys.executable, '-c', 'pass'], check=True)\n\n"
        "def test_fails():\n"
        "    assert False\n\n"
        "@pytest.fixture\n"
        "def broken():\n"
        "    raise RuntimeError('setup')\n\n"
        "def test_setup_error(broken):\n"
        "    pass\n"
    )
    instance = make_plugin(plugin, tmp_path, coverage=False)
    monkeypatch.setenv("PYTHONPATH", f"/runtime:{os.environ.get('PYTHONPATH', '')}")
    monkeypatch.setenv(plugin.ENV_RUNTIME, "/runtime")
    original_popen = subprocess.Popen

    exit_code = pytest.main(
        [str(tests), "-q", "-p", "no:cacheprovider", "-p", "no:asyncio", "--rootdir", str(tests)],
        plugins=[instance],
    )

    assert exit_code == 1
    assert subprocess.Popen is original_popen  # process audit restored
    assert "/runtime" not in os.environ.get("PYTHONPATH", "")
    ledger = json.loads((tmp_path / "out" / "ledger.json").read_text())
    nodes = {n["node_id"]: n for n in ledger["nodes"]}
    assert nodes["test_checkpoint_1.py::test_spawns"]["markers"] == ["error"]
    assert nodes["test_checkpoint_1.py::test_spawns"]["status"] == "passed"
    assert nodes["test_checkpoint_1.py::test_fails"]["status"] == "failed"
    assert nodes["test_checkpoint_1.py::test_setup_error"]["status"] == "error"
    assert ledger["exit_status"] == 1
    assert ledger["coverage"] == []
    assert {
        "node_id": "test_checkpoint_1.py::test_fails",
        "phase": "call",
        "outcome": "failed",
    } in (ledger["ledger"])
    assert ledger["process_events"] == [
        {
            "event": "subprocess.Popen",
            "node_id": "test_checkpoint_1.py::test_spawns",
            "phase": "call",
        }
    ]
    assert ledger["environment"]["grader_python"] == sys.executable


def test_subprocess_coverage_injection_only_in_coverage_mode(
    plugin: ModuleType, tmp_path: Path
) -> None:
    ledger_mode = make_plugin(plugin, tmp_path, coverage=False)
    ledger_mode._active = ("test_x.py::test_a", "call")
    assert ledger_mode._subprocess_kwargs({"env": {"A": "1"}}) == {"env": {"A": "1"}}

    coverage_mode = make_plugin(plugin, tmp_path, coverage=True)
    assert coverage_mode._subprocess_kwargs({}) == {}  # no active test: untouched
    coverage_mode._active = ("test_x.py::test_a", "call")
    env = coverage_mode._subprocess_kwargs({"env": {"PYTHONPATH": "/x"}})["env"]
    assert env["PYTHONPATH"].endswith(":/x")
    assert env[plugin.ENV_CONTEXT] == "test_x.py::test_a|call"
    assert env["COVERAGE_PROCESS_START"].endswith("subprocess.coveragerc")


def test_subprocess_coverage_setup_writes_startup_hook_and_rcfile(
    plugin: ModuleType, tmp_path: Path
) -> None:
    instance = make_plugin(plugin, tmp_path, coverage=True)
    instance._prepare_subprocess_coverage()
    startup = tmp_path / "scratch" / "coverage-startup"
    assert "process_startup" in (startup / "sitecustomize.py").read_text()
    assert (startup / "coverage" / "__init__.py").is_file()
    rc = (tmp_path / "scratch" / "subprocess.coveragerc").read_text()
    assert f"source = {tmp_path / 'app'}" in rc
    assert f"${plugin.ENV_CONTEXT}" in rc


def test_coverage_rows_keep_only_submission_files_and_count_symlink_escapes(
    plugin: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "main.py").write_text("x = 1\n")
    outside = tmp_path / "outside.py"
    outside.write_text("y = 1\n")
    os.symlink(outside, app / "escape.py")

    class Data:
        def measured_files(self) -> list[str]:
            return [str(app / "main.py"), str(app / "escape.py"), "/usr/lib/python3/os.py"]

        def contexts_by_lineno(self, path: str) -> dict[int, list[str]]:
            return {1: ["test_a.py::test_x|call", "collection", "test_a.py::test_x|setup"]}

    class Coverage:
        def get_data(self) -> Data:
            return Data()

    monkeypatch.setattr(plugin, "_current_coverage", lambda: Coverage())
    instance = make_plugin(plugin, tmp_path, coverage=True)

    rows = instance._coverage_contexts()

    assert rows == [
        {"node_id": "test_a.py::test_x", "phase": "call", "path": "main.py", "executed_line": 1},
        {"node_id": "test_a.py::test_x", "phase": "setup", "path": "main.py", "executed_line": 1},
    ]
    assert instance._run.invalid_coverage_paths == 1


def test_collection_errors_are_recorded_for_the_host(
    plugin: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_checkpoint_1.py").write_text("import not_a_module_anywhere\n")
    instance = make_plugin(plugin, tmp_path, coverage=False)
    monkeypatch.delenv("PYTHONPATH", raising=False)

    pytest.main(
        [str(tests), "-q", "-p", "no:cacheprovider", "-p", "no:asyncio", "--rootdir", str(tests)],
        plugins=[instance],
    )

    ledger = json.loads((tmp_path / "out" / "ledger.json").read_text())
    assert ledger["collect_errors"] == ["test_checkpoint_1.py"]
    assert ledger["nodes"] == []
    assert "PYTHONPATH" not in os.environ


def test_module_hook_registers_the_plugin_from_the_sandbox_env(
    plugin: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in (
        (plugin.ENV_LEDGER, str(tmp_path / "ledger.json")),
        (plugin.ENV_ROOT, str(tmp_path / "app")),
        (plugin.ENV_SCRATCH, str(tmp_path / "scratch")),
        (plugin.ENV_COVERAGE, "1"),
    ):
        monkeypatch.setenv(name, value)
    registered: list[tuple[Any, str]] = []

    class Manager:
        def register(self, instance: Any, name: str) -> None:
            registered.append((instance, name))

    class Config:
        pluginmanager = Manager()

    plugin.pytest_configure(Config())

    [(instance, name)] = registered
    assert name == "raidar-oracle"
    assert instance._coverage is True
    assert plugin._sha256_file(str(tmp_path / "missing")) == ""
    assert plugin._sha256_file(None) == ""
