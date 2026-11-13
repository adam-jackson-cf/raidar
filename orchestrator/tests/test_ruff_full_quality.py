"""Exercise Full Ruff lint and finding-baseline refusal in isolated workspaces."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.quality

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "orchestrator/src/raidar/probe.py"


@pytest.fixture
def lint_tree(tmp_path: Path) -> Path:
    """Copy only the real Ruff config and baseline checker, never released code."""
    config = tmp_path / "orchestrator/pyproject.toml"
    config.parent.mkdir()
    shutil.copyfile(ROOT / "orchestrator/pyproject.toml", config)
    checker = tmp_path / "scripts/checks/check-finding-baseline.py"
    checker.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/checks/check-finding-baseline.py", checker)
    baseline = tmp_path / "scripts/checks/baselines/ruff.json"
    baseline.parent.mkdir(parents=True)
    baseline.write_text(json.dumps({"schemaVersion": 1, "tool": "ruff", "findings": []}))
    source = tmp_path / SOURCE
    source.parent.mkdir(parents=True)
    return tmp_path


def _lint_and_baseline(root: Path, source: str, *options: str):
    """Run the gate's check-only lint step and the real baseline CLI."""
    (root / SOURCE).write_text(source)
    lint = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-fix",
            "--config",
            "orchestrator/pyproject.toml",
            "--force-exclude",
            "--output-format=json",
            *options,
            SOURCE,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert lint.returncode in (0, 1), lint.stderr
    findings = json.loads(lint.stdout)
    (root / "ruff-report.json").write_text(lint.stdout)
    baseline = subprocess.run(
        [
            sys.executable,
            "scripts/checks/check-finding-baseline.py",
            "--tool",
            "ruff",
            "--root",
            str(root),
            "--report",
            "ruff-report.json",
            "--baseline",
            "scripts/checks/baselines/ruff.json",
            "--base-baseline",
            "scripts/checks/baselines/ruff.json",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert baseline.returncode in (0, 1), baseline.stderr
    return lint, findings, baseline


@pytest.mark.parametrize(
    ("source", "code"),
    [
        (
            '"""Expose a nested public class."""\n\n\n'
            'class Envelope:\n    """Hold nested declarations."""\n\n'
            "    class Payload:\n        pass\n",
            "D106",
        ),
        (
            '"""Expose an oversized function signature."""\n\n\n'
            "def combine(\n    *, first: int, second: int, third: int,\n"
            "    fourth: int, fifth: int, sixth: int,\n) -> int:\n"
            '    """Combine the inputs."""\n'
            "    return first + second + third + fourth + fifth + sixth\n",
            "PLR0913",
        ),
    ],
    ids=["missing-nested-class-docstring", "six-arguments"],
)
def test_new_full_rule_violation_fails_ruff_and_baseline(
    lint_tree: Path, source: str, code: str
) -> None:
    lint, findings, baseline = _lint_and_baseline(lint_tree, source)
    assert code in {finding["code"] for finding in findings}, lint.stdout
    assert lint.returncode == 1
    assert baseline.returncode == 1
    assert any(
        "BASELINE.new" in line and SOURCE in line and code in line
        for line in baseline.stderr.splitlines()
    ), baseline.stderr


def test_d401_only_docstring_is_an_accepted_deviation(lint_tree: Path) -> None:
    source = (
        '"""Expose a small answer function."""\n\n\n'
        'def answer() -> int:\n    """Returns the answer."""\n    return 42\n'
    )
    lint, findings, baseline = _lint_and_baseline(lint_tree, source)
    assert findings == [], lint.stdout
    assert lint.returncode == baseline.returncode == 0


@pytest.mark.parametrize(("length", "accepted"), [(95, True), (105, False)])
def test_line_length_deviation_keeps_the_hundred_character_boundary(
    lint_tree: Path, length: int, accepted: bool
) -> None:
    prefix = 'MESSAGE = "'
    content_length = length - len(prefix) - 1
    content = ("word " * length)[:content_length]
    line = prefix + content + '"'
    assert len(line) == length
    lint, findings, baseline = _lint_and_baseline(
        lint_tree, '"""Expose a message."""\n\n' + line + "\n"
    )
    if accepted:
        assert findings == [], lint.stdout
        assert lint.returncode == baseline.returncode == 0
    else:
        assert "E501" in {finding["code"] for finding in findings}, lint.stdout
        assert lint.returncode == baseline.returncode == 1
        assert "BASELINE.new" in baseline.stderr
        assert "E501" in baseline.stderr


def test_py313_generator_defaults_are_not_required_for_py312(lint_tree: Path) -> None:
    source = (
        '"""Expose a generator compatible with Python 3.12."""\n\n'
        "from collections.abc import Generator\n\n\n"
        'def samples() -> Generator[int, None, None]:\n    """Yield a sample."""\n'
        "    yield 1\n"
    )
    lint, findings, baseline = _lint_and_baseline(lint_tree, source)
    assert findings == [], lint.stdout
    assert lint.returncode == baseline.returncode == 0
    _, newer_findings, newer_baseline = _lint_and_baseline(
        lint_tree, source, "--target-version=py313"
    )
    assert "UP043" in {finding["code"] for finding in newer_findings}
    assert newer_baseline.returncode == 1
