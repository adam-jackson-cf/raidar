"""Prove the suppression ratchet accepts history but refuses new exceptions."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "orchestrator/src/raidar/probe.py"
CONFIG = "orchestrator/pyproject.toml"


@pytest.fixture
def detector():
    """Load the production check without starting its command-line shell."""
    spec = importlib.util.spec_from_file_location(
        "ruff_suppression_proofs", ROOT / "scripts/checks/check-ruff-suppressions.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.added_suppression_findings


def _assert_findings(lines: list[str], path: str, rows: list[int]) -> None:
    assert len(lines) == len(rows)
    for line, row in zip(lines, rows, strict=True):
        assert re.fullmatch(rf"{re.escape(path)}:{row}: RUFF\.IGNORES[^\r\n;]*; [^\r\n]+", line)


@pytest.mark.parametrize("suppression", ["# noqa", "# noqa: F401", "# ruff: noqa: F401"])
def test_added_inline_or_file_suppression_is_actionable(detector, suppression: str) -> None:
    before = "import os\n"
    after = f"import os  {suppression}\n"
    _assert_findings(detector({SOURCE: before}, {SOURCE: after}, [SOURCE]), SOURCE, [1])


def test_each_added_suppression_line_has_its_own_diagnostic(detector) -> None:
    after = "# ruff: noqa\nimport os  # noqa: F401\n"
    _assert_findings(detector({}, {SOURCE: after}, [SOURCE]), SOURCE, [1, 2])


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("import os  # noqa: F401\n", "\nimport os  # noqa: F401\n"),
        ("# ruff: noqa\nimport os\n", "import os\n"),
        ("import os  # noqa: F401\n", ""),
    ],
)
def test_existing_moved_removed_or_deleted_suppressions_are_accepted(
    detector, before: str, after: str
) -> None:
    assert detector({SOURCE: before}, {SOURCE: after}, [SOURCE]) == []
    assert detector({SOURCE: before}, {}, [SOURCE]) == []


def test_unchanged_paths_and_string_literals_do_not_add_suppressions(detector) -> None:
    head = {SOURCE: "# noqa: F401\n", "scripts/probe.py": 'TEXT = "# noqa: F401"\n'}
    assert detector({}, head, ["scripts/probe.py"]) == []
    assert detector({}, head, []) == []


@pytest.mark.parametrize(
    ("path", "prefix"),
    [(CONFIG, "tool.ruff.lint"), ("ruff.toml", "lint"), (".ruff.toml", "lint")],
)
@pytest.mark.parametrize("setting", ["per-file-ignores", "extend-ignore"])
def test_config_additions_report_each_new_rule_entry(
    detector, path: str, prefix: str, setting: str
) -> None:
    if setting == "per-file-ignores":
        before = f'[{prefix}.per-file-ignores]\n"probe.py" = ["F401"]\n'
        after = f'[{prefix}.per-file-ignores]\n"probe.py" = [\n  "F401",\n  "B008",\n  "D106",\n]\n'
        rows = [4, 5]
    else:
        before = f'[{prefix}]\nextend-ignore = ["F401"]\n'
        after = f'[{prefix}]\nextend-ignore = [\n  "F401",\n  "B008",\n  "D106",\n]\n'
        rows = [4, 5]
    _assert_findings(detector({path: before}, {path: after}, [path]), path, rows)
    assert detector({path: after}, {path: before}, [path]) == []
    assert detector({path: after}, {path: after}, [path]) == []


def test_config_reformatting_and_rule_reordering_are_not_additions(detector) -> None:
    before = '[tool.ruff.lint]\nextend-ignore = ["F401", "B008"]\n'
    after = '[tool.ruff.lint]\nextend-ignore = [\n  "B008",\n  "F401",\n]\n'
    assert detector({CONFIG: before}, {CONFIG: after}, [CONFIG]) == []


def test_new_code_on_existing_suppression_line_is_not_grandfathered(detector) -> None:
    before = "import os  # noqa: F401\n"
    after = "import os  # noqa: F401, B008\n"
    _assert_findings(detector({SOURCE: before}, {SOURCE: after}, [SOURCE]), SOURCE, [1])
