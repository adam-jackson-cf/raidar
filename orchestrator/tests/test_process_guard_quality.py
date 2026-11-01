"""Real pytest canaries for fast-tier launch refusals, including fixture lifecycle."""

from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from raidar.contract.paths import ORCHESTRATOR_ROOT

pytestmark = pytest.mark.quality


def _run_pytest(
    workspace: Path, *, extra_env: dict[str, str] | None = None, collect_only: bool = False
) -> subprocess.CompletedProcess[str]:
    (workspace / "pytest.ini").write_text(
        "[pytest]\naddopts = --strict-markers\nmarkers =\n"
        "    slow: local processes\n    docker: container seams\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ORCHESTRATOR_ROOT)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("AGENT", None)
    env.update(extra_env or {})
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "tests.process_guard",
            "-q",
            "--tb=short",
            "--junitxml=result.xml",
            *(["--collect-only"] if collect_only else []),
        ],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
def test_fast_launch_refused_through_fixture_lifecycle_and_scope_cleared(
    tmp_path: Path, phase: str
) -> None:
    launch = (
        "subprocess.run([sys.executable, '-c', "
        "\"from pathlib import Path; Path('forbidden').touch()\"], check=True)"
    )
    setup = launch if phase == "setup" else "pass"
    call = launch if phase == "call" else "pass"
    teardown = launch if phase == "teardown" else "pass"
    (tmp_path / "test_fast.py").write_text(
        "import subprocess, sys\nimport pytest\n"
        "@pytest.fixture\ndef lifecycle():\n"
        f"    {setup}\n    yield\n    {teardown}\n"
        f"def test_launch(lifecycle):\n    {call}\n"
    )
    (tmp_path / "test_slow.py").write_text(
        "import subprocess, sys\nimport pytest\npytestmark = pytest.mark.slow\n"
        "def test_launch():\n"
        "    subprocess.run([sys.executable, '-c', "
        "\"from pathlib import Path; Path('allowed').touch()\"], check=True)\n"
    )

    result = _run_pytest(tmp_path)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "test_fast.py::test_launch: subprocess.Popen" in result.stdout
    assert not (tmp_path / "forbidden").exists()
    assert (tmp_path / "allowed").exists()


def test_injected_runner_stays_allowed_in_fast_scope(tmp_path: Path) -> None:
    (tmp_path / "test_fast.py").write_text(
        "import subprocess\n"
        "def test_injected(monkeypatch):\n"
        "    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: "
        "subprocess.CompletedProcess(a, 0))\n"
        "    subprocess.run(['not-a-real-command'])\n"
    )

    result = _run_pytest(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    suite = ET.parse(tmp_path / "result.xml").getroot().find("testsuite")
    assert suite is not None
    assert (suite.attrib["tests"], suite.attrib["failures"], suite.attrib["errors"]) == (
        "1",
        "0",
        "0",
    )


def test_missing_required_docker_fails_at_setup_not_collection(tmp_path: Path) -> None:
    (tmp_path / "test_container.py").write_text(
        "from pathlib import Path\nimport pytest\npytestmark = pytest.mark.docker\n"
        "def test_container():\n    Path('ran').touch()\n"
    )
    env = {
        "RAIDAR_REQUIRE_DOCKER": "1",
        "RAIDAR_TARGET": "missing",
        "RAIDAR_TARGETS_FILE": str(tmp_path / "missing-targets.toml"),
    }

    collection = _run_pytest(tmp_path, extra_env=env, collect_only=True)
    assert collection.returncode == 0, collection.stdout + collection.stderr
    execution = _run_pytest(tmp_path, extra_env=env)

    assert execution.returncode == 1, execution.stdout + execution.stderr
    suite = ET.parse(tmp_path / "result.xml").getroot().find("testsuite")
    assert suite is not None
    assert (suite.attrib["errors"], suite.attrib["skipped"]) == ("1", "0")
    assert "RAIDAR_REQUIRE_DOCKER=1" in execution.stdout
    assert not (tmp_path / "ran").exists()
