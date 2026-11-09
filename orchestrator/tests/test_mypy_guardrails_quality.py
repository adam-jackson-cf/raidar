"""Real mypy canaries using the repository configuration and baseline gate."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import click
import pytest

from raidar.cli import quality

pytestmark = pytest.mark.quality
ROOT = Path(__file__).resolve().parents[2]


def _run(argv: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=root, capture_output=True, text=True, check=False, timeout=60)


def _workspace(root: Path) -> Path:
    orchestrator = root / "orchestrator"
    orchestrator.mkdir()
    shutil.copyfile(ROOT / "orchestrator/pyproject.toml", orchestrator / "pyproject.toml")
    sources = {
        "orchestrator/src/raidar/__init__.py": "",
        "orchestrator/src/raidar/canary.py": "VALUE: int = 1\n",
        "orchestrator/tests/test_canary.py": "def test_canary() -> None:\n    assert True\n",
        "contract/src/raidar_contract/__init__.py": "",
        "scripts/canary.py": "VALUE: int = 1\n",
    }
    for relative, source in sources.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    return orchestrator


def _mypy(orchestrator: Path) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            "pyproject.toml",
            "--no-incremental",
            "--follow-imports=skip",
            "src/raidar/canary.py",
        ],
        orchestrator,
    )


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("def untyped(value):\n    return value\n", "no-untyped-def"),
        ("VALUE: int = 1  # type: ignore\n", "unused-ignore"),
        ("import copy\n\nVALUE = copy.replace(object())\n", "attr-defined"),
    ],
    ids=["strict-untyped-def", "unused-ignore", "python313-stdlib"],
)
def test_repository_mypy_policy_accepts_typed_code_and_refuses_canary(
    tmp_path: Path, source: str, code: str
) -> None:
    orchestrator = _workspace(tmp_path)
    accepted = _mypy(orchestrator)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    (orchestrator / "src/raidar/canary.py").write_text(source)
    refused = _mypy(orchestrator)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "src/raidar/canary.py:" in refused.stdout
    assert f"[{code}]" in refused.stdout, refused.stdout + refused.stderr


def test_mypy_baseline_gate_refuses_a_new_real_type_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    orchestrator = _workspace(tmp_path)
    baseline = tmp_path / ".omp/baselines/mypy.json"
    baseline.parent.mkdir(parents=True)
    baseline.write_text(json.dumps({"schemaVersion": 1, "tool": "mypy", "findings": []}))
    for argv in [
        ["git", "init", "-q"],
        ["git", "add", ".omp/baselines/mypy.json"],
        [
            "git",
            "-c",
            "user.name=Canary",
            "-c",
            "user.email=canary@example.invalid",
            "commit",
            "-qm",
            "Empty mypy baseline",
        ],
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
    ]:
        result = _run(argv, tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
    monkeypatch.setattr(quality, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(quality, "ORCHESTRATOR_ROOT", orchestrator)
    monkeypatch.setattr(quality, "PYPROJECT", orchestrator / "pyproject.toml")
    quality.run_mypy_quality_gates()
    capfd.readouterr()
    (orchestrator / "src/raidar/canary.py").write_text('VALUE: int = "new error"\n')
    with pytest.raises(click.ClickException) as error:
        quality.run_mypy_quality_gates()
    captured = capfd.readouterr()
    diagnostic = captured.out + captured.err + str(error.value)
    assert "BASELINE.new" in diagnostic
    assert "orchestrator/src/raidar/canary.py" in diagnostic
    assert "assignment" in diagnostic
    assert json.loads(baseline.read_text())["findings"] == []
