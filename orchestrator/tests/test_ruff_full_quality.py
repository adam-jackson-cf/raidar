"""Exercise Full Ruff lint and the Ruff gate's refusal in isolated workspaces."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

pytestmark = pytest.mark.quality

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "orchestrator/src/raidar/probe.py"


@pytest.fixture
def lint_tree(tmp_path: Path) -> Path:
    """Copy only the real Ruff config and gate script, never released code."""
    config = tmp_path / "orchestrator/pyproject.toml"
    config.parent.mkdir()
    shutil.copyfile(ROOT / "orchestrator/pyproject.toml", config)
    checker = tmp_path / "scripts/checks/check-ruff.py"
    checker.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/checks/check-ruff.py", checker)
    shutil.copyfile(ROOT / "scripts/checks/git_inventory.py", checker.parent / "git_inventory.py")
    for root in ("orchestrator/tests", "contract/src"):
        (tmp_path / root).mkdir(parents=True)
    source = tmp_path / SOURCE
    source.parent.mkdir(parents=True)
    return tmp_path


def _lint_and_gate(
    root: Path, source: str, *options: str
) -> tuple[CompletedProcess[str], list[dict[str, object]], CompletedProcess[str]]:
    """Run the check-only lint step and the real Ruff gate script."""
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
    findings: list[dict[str, object]] = json.loads(lint.stdout)
    assert isinstance(findings, list), lint.stdout
    gate = subprocess.run(
        [sys.executable, "scripts/checks/check-ruff.py"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert gate.returncode in (0, 1), gate.stderr
    return lint, findings, gate


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
def test_new_full_rule_violation_fails_ruff_and_the_gate(
    lint_tree: Path, source: str, code: str
) -> None:
    lint, findings, gate = _lint_and_gate(lint_tree, source)
    assert code in {finding["code"] for finding in findings}, lint.stdout
    assert lint.returncode == 1
    assert gate.returncode == 1
    assert any(SOURCE in line and code in line for line in gate.stdout.splitlines()), gate.stdout


def test_d401_only_docstring_is_an_accepted_deviation(lint_tree: Path) -> None:
    source = (
        '"""Expose a small answer function."""\n\n\n'
        'def answer() -> int:\n    """Returns the answer."""\n    return 42\n'
    )
    lint, findings, gate = _lint_and_gate(lint_tree, source)
    assert findings == [], lint.stdout
    assert lint.returncode == gate.returncode == 0


@pytest.mark.parametrize(("length", "accepted"), [(95, True), (105, False)])
def test_line_length_deviation_keeps_the_hundred_character_boundary(
    lint_tree: Path, length: int, accepted: bool
) -> None:
    prefix = 'MESSAGE = "'
    content_length = length - len(prefix) - 1
    content = ("word " * length)[:content_length]
    line = prefix + content + '"'
    assert len(line) == length
    lint, findings, gate = _lint_and_gate(lint_tree, '"""Expose a message."""\n\n' + line + "\n")
    if accepted:
        assert findings == [], lint.stdout
        assert lint.returncode == gate.returncode == 0
    else:
        assert "E501" in {finding["code"] for finding in findings}, lint.stdout
        assert lint.returncode == gate.returncode == 1
        assert "E501" in gate.stdout


def test_py313_generator_defaults_are_not_required_for_py312(lint_tree: Path) -> None:
    source = (
        '"""Expose a generator compatible with Python 3.12."""\n\n'
        "from collections.abc import Generator\n\n\n"
        'def samples() -> Generator[int, None, None]:\n    """Yield a sample."""\n'
        "    yield 1\n"
    )
    lint, findings, gate = _lint_and_gate(lint_tree, source)
    assert findings == [], lint.stdout
    assert lint.returncode == gate.returncode == 0
    newer, newer_findings, _ = _lint_and_gate(lint_tree, source, "--target-version=py313")
    assert "UP043" in {finding["code"] for finding in newer_findings}
    assert newer.returncode == 1


def test_the_gate_skips_fixture_programs_but_lints_the_stage_tool_beside_them(
    lint_tree: Path,
) -> None:
    package = lint_tree / "orchestrator/tests/fixtures/stages/acme/probe"
    program = package / "fixtures/refuse/state/bad.py"
    program.parent.mkdir(parents=True)
    program.write_text("import os\n")
    gate = [sys.executable, "scripts/checks/check-ruff.py"]
    (lint_tree / SOURCE).write_text('"""Expose nothing."""\n')
    skipped = subprocess.run(gate, cwd=lint_tree, capture_output=True, text=True, timeout=30)
    assert skipped.returncode == 0, skipped.stdout
    tool = package / "tool/src/probe/bad.py"
    tool.parent.mkdir(parents=True)
    tool.write_text("import os\n")
    linted = subprocess.run(gate, cwd=lint_tree, capture_output=True, text=True, timeout=30)
    assert linted.returncode == 1
    assert "tool/src/probe/bad.py" in linted.stdout
    assert "fixtures/refuse" not in linted.stdout
