"""Slow-tier behaviours split from :mod:`tests.test_verifier_plugin_behaviors`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.test_verifier_plugin_behaviors import (
    make_plugin as make_plugin,
)
from tests.test_verifier_plugin_behaviors import (
    plugin_fixture as plugin_fixture,
)

pytestmark = pytest.mark.slow


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
