"""code-quality@9 measurement-process seams against the real pinned tool environment.

The rejection decisions (untrusted Ruff/evaluator output, malformed rows, gaps) are fast
tests over an in-memory runner; here only what needs a real interpreter, real Ruff or a
real process boundary: timeouts, a missing interpreter, candidate modules that must not
execute, and the environment handed to the child.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from raidar.scoring.code_quality.contract import EXPECTED_RUFF_VERSION
from raidar.scoring.code_quality.measurement import MeasurementError, run_process
from raidar.scoring.code_quality.quality import _ruff
from tests.code_quality_support import quality_tools

pytestmark = pytest.mark.slow


def test_processes_that_time_out_or_cannot_start_are_environment_mismatches(
    tmp_path: Path,
) -> None:
    with pytest.raises(MeasurementError, match="timed out"):
        run_process(
            [str(quality_tools().python), "-c", "import time; time.sleep(5)"], None, None, 0.2
        )
    with pytest.raises(MeasurementError, match="evaluator unavailable"):
        run_process([str(tmp_path / "missing-python")], None, None, 5)


@pytest.mark.parametrize("candidate_module", ["ruff.py", "ruff/__main__.py"])
def test_candidate_ruff_modules_cannot_execute_during_version_or_lint_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, candidate_module: str
) -> None:
    snapshot = (tmp_path / "snapshot").resolve()
    snapshot.mkdir()
    marker = snapshot / "candidate-executed"
    canary = (
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\nprint('[]')\n"
    )
    candidate = snapshot / candidate_module
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(canary)
    if candidate_module.endswith("__main__.py"):
        (candidate.parent / "__init__.py").write_text(canary)
    (snapshot / "sitecustomize.py").write_text(canary)
    (snapshot / "main.py").write_text("if a:\n    if b:\n        x = 1\n")
    monkeypatch.chdir(snapshot)
    monkeypatch.setenv("PYTHONPATH", str(snapshot))
    version, diagnostics, invocation = _ruff(quality_tools(), snapshot, ["main.py"])
    assert version == f"ruff {EXPECTED_RUFF_VERSION}"
    assert [diagnostic["code"] for diagnostic in diagnostics] == ["SIM102"]
    assert invocation[1:3] == ("-I", "-B")
    assert not marker.exists()
    assert not (snapshot / "__pycache__").exists()


def test_measurement_processes_receive_only_platform_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAIDAR_TEST_SECRET", "synthetic-canary")
    monkeypatch.setenv("LANG", "C")
    result = run_process(
        [
            str(quality_tools().python),
            "-I",
            "-c",
            "import json, os; print(json.dumps({"
            "'canary_present': 'RAIDAR_TEST_SECRET' in os.environ, "
            "'language_preserved': os.environ.get('LANG') == 'C'}))",
        ],
        None,
        None,
        5,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"canary_present": False, "language_preserved": True}
