"""Fast proofs for finding identity, scan parsing and shrink-only baselines."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "finding_baseline_proofs", ROOT / "scripts/checks/check-finding-baseline.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


baseline = _load_script()
PATH = "orchestrator/src/raidar/example.py"
MYPY_BASELINE = "scripts/checks/baselines/mypy.json"
SOURCE = "def build():\n    unused = 1\n    return 0\n"


def _finding(*, path: str = PATH, code: str = "F841", fingerprint: str = "abc", count: int = 1):
    return {"path": path, "code": code, "fingerprint": fingerprint, "count": count}


def _baseline(*findings: dict[str, object], tool: str = "ruff") -> dict[str, object]:
    return {"schemaVersion": 1, "tool": tool, "findings": list(findings)}


def _ruff(*, row=2, code="F841", filename=PATH):
    return {
        "filename": filename,
        "code": code,
        "message": "Local variable is assigned to but never used",
        "location": {"row": row, "column": 5},
        "end_location": {"row": row, "column": 11},
        "fix": None,
        "noqa_row": row,
        "url": "https://docs.astral.sh/ruff/rules/unused-variable/",
    }


def test_fingerprint_is_stable_under_whitespace_but_names_code_and_symbol() -> None:
    fingerprint = baseline.fingerprint("F841", "Builder.build", "    unused = 1")
    assert fingerprint
    assert fingerprint == baseline.fingerprint("F841", "Builder.build", "\tunused   =   1 ")
    assert fingerprint != baseline.fingerprint("F821", "Builder.build", "    unused = 1")
    assert fingerprint != baseline.fingerprint("F841", "Other.build", "    unused = 1")
    assert fingerprint != baseline.fingerprint("F841", "Builder.build", "    other = 1")


@pytest.mark.parametrize("tool", ["ruff", "mypy"])
def test_parser_fingerprint_survives_line_shifts_and_message_changes(tool: str) -> None:
    code = "F841" if tool == "ruff" else "assignment"
    parser = baseline.parse_ruff if tool == "ruff" else baseline.parse_mypy
    if tool == "ruff":
        original = json.dumps([_ruff(filename=f"/repo/{PATH}")])
        moved_item = _ruff(row=4, filename=PATH)
        moved_item["message"] = "Changed wording, identical finding"
        moved = json.dumps([moved_item])
    else:
        original = f"{PATH}:2:5: error: Incompatible assignment [{code}]\n"
        moved = f"{PATH}:4: error: Changed wording [{code}]\n"
    expected = _finding(
        code=code, fingerprint=baseline.fingerprint(code, "build", "    unused = 1")
    )
    assert parser(original, root="/repo", sources={PATH: SOURCE}) == [expected]
    assert parser(moved, root="/repo", sources={PATH: "\n\n" + SOURCE}) == [expected]
    renamed = SOURCE.replace("build", "other")
    assert parser(original, root="/repo", sources={PATH: renamed}) != [expected]


def test_ruff_parser_aggregates_counts_and_sorts_finding_keys() -> None:
    other_path = "contract/src/raidar_contract/example.py"
    report = json.dumps([_ruff(), _ruff(filename=other_path), _ruff()])
    fingerprint = baseline.fingerprint("F841", "build", "    unused = 1")
    assert baseline.parse_ruff(
        report, root="/repo", sources={PATH: SOURCE, other_path: SOURCE}
    ) == [
        _finding(path=other_path, fingerprint=fingerprint),
        _finding(fingerprint=fingerprint, count=2),
    ]


def test_mypy_parser_accepts_notes_and_summary_without_inventing_findings() -> None:
    report = (
        f"{PATH}:2: error: Incompatible assignment [assignment]\n"
        f"{PATH}:2: note: See the documentation\n"
        "Found 1 error in 1 file (checked 1 source file)\n"
    )
    assert baseline.parse_mypy(report, root="/repo", sources={PATH: SOURCE}) == [
        _finding(
            code="assignment",
            fingerprint=baseline.fingerprint("assignment", "build", "    unused = 1"),
        )
    ]
    assert (
        baseline.parse_mypy("Success: no issues found in 1 source file\n", root="/repo", sources={})
        == []
    )
    assert baseline.parse_ruff("[]", root="/repo", sources={}) == []


@pytest.mark.parametrize("tool", ["ruff", "mypy"])
@pytest.mark.parametrize("text", [None, "", " \n\t", "truncated report", "{", "null"])
def test_parsers_refuse_missing_empty_or_malformed_scans(tool: str, text: str | None) -> None:
    parser = baseline.parse_ruff if tool == "ruff" else baseline.parse_mypy
    with pytest.raises(baseline.ScanInputError):
        parser(text, root="/repo", sources={PATH: SOURCE})


@pytest.mark.parametrize(
    "report",
    [
        "{}",
        "[{}]",
        json.dumps([_ruff(row=0)]),
        json.dumps([_ruff(row=999)]),
        json.dumps([_ruff(code=None)]),
        json.dumps([_ruff(filename="../outside.py")]),
        json.dumps([_ruff(filename="/outside/example.py")]),
    ],
)
def test_ruff_parser_refuses_invalid_diagnostic_or_source_location(report: str) -> None:
    with pytest.raises(baseline.ScanInputError):
        baseline.parse_ruff(report, root="/repo", sources={PATH: SOURCE})


@pytest.mark.parametrize(
    "report",
    [
        f"{PATH}:2: error: Missing rule code\n",
        f"{PATH}:999: error: Outside source [assignment]\n",
        "../outside.py:2: error: Outside repo [assignment]\n",
        f"{PATH}:2: note: Notes alone do not prove a completed scan\n",
        f"{PATH}:2: error: Valid [assignment]\ntruncated garbage\n",
    ],
)
def test_mypy_parser_refuses_incomplete_or_unidentified_findings(report: str) -> None:
    with pytest.raises(baseline.ScanInputError):
        baseline.parse_mypy(report, root="/repo", sources={PATH: SOURCE})


@pytest.mark.parametrize("tool", ["ruff", "mypy"])
def test_parser_refuses_missing_source_instead_of_dropping_finding(tool: str) -> None:
    parser = baseline.parse_ruff if tool == "ruff" else baseline.parse_mypy
    report = json.dumps([_ruff()]) if tool == "ruff" else f"{PATH}:2: error: Bad [assignment]\n"
    with pytest.raises(baseline.ScanInputError):
        parser(report, root="/repo", sources={})


@pytest.mark.parametrize("tool", ["ruff", "mypy"])
def test_exact_baseline_equality_is_silent_and_successful(tool: str) -> None:
    finding = _finding()
    record = _baseline(finding, tool=tool)
    assert baseline.evaluate_baseline(record, [finding], record) == (0, [])
    empty = _baseline(tool=tool)
    assert baseline.evaluate_baseline(empty, [], empty) == (0, [])


@pytest.mark.parametrize(
    ("record", "findings", "base_record", "rule", "fix"),
    [
        (_baseline(), [_finding()], _baseline(), "BASELINE.new", "fix"),
        (
            _baseline(_finding()),
            [_finding(count=2)],
            _baseline(_finding()),
            "BASELINE.new",
            "fix",
        ),
        (_baseline(_finding()), [], _baseline(_finding()), "BASELINE.stale", "remove"),
        (
            _baseline(_finding(count=2)),
            [_finding()],
            _baseline(_finding(count=2)),
            "BASELINE.stale",
            "reduce",
        ),
        (
            _baseline(_finding()),
            [_finding()],
            _baseline(),
            "BASELINE.refresh",
            "revert",
        ),
        (
            _baseline(_finding(count=2)),
            [_finding(count=2)],
            _baseline(_finding()),
            "BASELINE.refresh",
            "revert",
        ),
    ],
    ids=["new", "count-above", "fixed-or-file-gone", "count-below", "entry-added", "count-raised"],
)
def test_baseline_failures_are_finding_specific_and_actionable(
    record: dict, findings: list[dict], base_record: dict, rule: str, fix: str
) -> None:
    status, lines = baseline.evaluate_baseline(record, findings, base_record)
    assert status == 1
    assert len(lines) == 1
    line = lines[0]
    assert "\n" not in line
    assert rule in line
    assert PATH in line
    assert "F841" in line
    assert "abc" in line
    assert fix in line.lower()


def test_baseline_may_only_shrink_and_matches_each_finding_not_rule_totals() -> None:
    other = _finding(fingerprint="other")
    assert baseline.evaluate_baseline(
        _baseline(_finding()), [_finding()], _baseline(_finding(count=2), other)
    ) == (0, [])
    status, lines = baseline.evaluate_baseline(
        _baseline(_finding()), [other], _baseline(_finding())
    )
    assert status == 1
    assert len(lines) == 2
    assert any("BASELINE.new" in line and "other" in line for line in lines)
    assert any("BASELINE.stale" in line and "abc" in line for line in lines)


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {"schemaVersion": 2, "tool": "ruff", "findings": []},
        _baseline(tool="invalid_tool"),
        _baseline(_finding(count=0)),
        _baseline(_finding(count=-1)),
        _baseline(_finding(count=True)),
        _baseline(_finding(path="../outside.py")),
        _baseline(_finding(fingerprint="")),
        _baseline(_finding(), _finding()),
    ],
)
def test_baseline_refuses_invalid_records(invalid: dict) -> None:
    with pytest.raises(baseline.ScanInputError):
        baseline.evaluate_baseline(invalid, [], _baseline())
    with pytest.raises(baseline.ScanInputError):
        baseline.evaluate_baseline(_baseline(), [], invalid)


def test_baseline_refuses_comparison_between_different_tools() -> None:
    with pytest.raises(baseline.ScanInputError):
        baseline.evaluate_baseline(_baseline(), [], _baseline(tool="mypy"))


@pytest.mark.parametrize(
    "message",
    [
        f"fatal: path '{MYPY_BASELINE}' does not exist in 'origin/main'",
        f"fatal: path '{MYPY_BASELINE}' exists on disk, but not in 'origin/main'",
    ],
)
def test_first_baseline_adoption_still_enforces_current_finding_equality(message: str) -> None:
    record = _baseline(_finding(), tool="mypy")
    result = subprocess.CompletedProcess([], 128, "", message + "\n")
    base = baseline.read_base_baseline(result, record, MYPY_BASELINE, "origin/main")
    assert baseline.evaluate_baseline(record, [_finding()], base) == (0, [])
    assert baseline.evaluate_baseline(record, [], base)[0] == 1
    assert baseline.evaluate_baseline(record, [_finding(count=2)], base)[0] == 1


@pytest.mark.parametrize(
    ("status", "stdout", "stderr"),
    [
        (128, "", "fatal: invalid object name 'origin/main'."),
        (128, "", "fatal: path 'other.json' does not exist in 'origin/main'"),
        (128, "", f"fatal: path '{MYPY_BASELINE}' does not exist in 'other'"),
        (128, "", "permission denied"),
        (0, "{", ""),
    ],
)
def test_first_adoption_does_not_hide_other_base_read_failures(
    status: int, stdout: str, stderr: str
) -> None:
    result = subprocess.CompletedProcess([], status, stdout, stderr)
    with pytest.raises(ValueError, match="cannot read base baseline|Expecting"):
        baseline.read_base_baseline(result, _baseline(), MYPY_BASELINE, "origin/main")


def test_existing_base_baseline_is_read_instead_of_refreshing_it() -> None:
    result = subprocess.CompletedProcess([], 0, json.dumps(_baseline()), "")
    assert baseline.read_base_baseline(result, _baseline(_finding()), "baseline.json", "main") == (
        _baseline()
    )


def test_generic_reports_keep_finding_identity_and_ratchet_custom_tools() -> None:
    item = {"path": PATH, "code": "PYS250", "symbol": "build", "source_line": "for x in rows:"}
    findings = baseline.parse_findings_json(json.dumps([item, item]), root="/repo")
    expected = _finding(
        code="PYS250",
        fingerprint=baseline.fingerprint("PYS250", "build", "for x in rows:"),
        count=2,
    )
    assert findings == [expected]
    record = _baseline(expected, tool="python-policy")
    assert baseline.evaluate_baseline(record, findings, record) == (0, [])
    shrunk = _finding(code="PYS250", fingerprint=expected["fingerprint"])
    assert baseline.evaluate_baseline(
        _baseline(shrunk, tool="python-policy"), [shrunk], record
    ) == (0, [])
    status, lines = baseline.evaluate_baseline(
        record, findings, _baseline(shrunk, tool="python-policy")
    )
    assert status == 1
    assert any("BASELINE.refresh" in line for line in lines)
    assert baseline.parse_findings_json("[]", root="/repo") == []


@pytest.mark.parametrize(
    "item",
    [
        {},
        {"path": "../outside.py", "code": "X", "symbol": "", "source_line": "x"},
        {"path": PATH, "code": "", "symbol": "", "source_line": "x"},
        {"path": PATH, "code": "X", "symbol": None, "source_line": "x"},
        {"path": PATH, "code": "X", "symbol": "", "source_line": "x\ny"},
        {"path": PATH, "code": "X", "symbol": "", "source_line": None},
    ],
)
def test_generic_reports_refuse_incomplete_or_unsafe_findings(item: dict[str, object]) -> None:
    with pytest.raises(baseline.ScanInputError):
        baseline.parse_findings_json(json.dumps([item]), root="/repo")


@pytest.mark.parametrize(
    "scenario",
    [
        ("python-policy", "for x in rows:", 0),
        ("python-policy", "for y in rows:", 1),
        ("invalid_tool", "for x in rows:", 2),
    ],
)
def test_generic_cli_checks_reports_and_rejects_invalid_tool_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    scenario: tuple[str, str, int],
) -> None:
    tool, source_line, expected = scenario
    original = {"path": PATH, "code": "PYS250", "symbol": "build", "source_line": "for x in rows:"}
    record = {
        "schemaVersion": 1,
        "tool": "python-policy",
        "findings": baseline.parse_findings_json(json.dumps([original]), root=str(tmp_path)),
    }
    (tmp_path / "baseline.json").write_text(json.dumps(record), encoding="utf-8")
    (tmp_path / "base.json").write_text(json.dumps(record), encoding="utf-8")
    (tmp_path / "report.json").write_text(
        json.dumps([original | {"source_line": source_line}]), encoding="utf-8"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check-finding-baseline.py",
            "--tool",
            tool,
            "--format",
            "findings-json",
            "--root",
            str(tmp_path),
            "--baseline",
            "baseline.json",
            "--base-baseline",
            "base.json",
            "--report",
            "report.json",
        ],
    )
    assert baseline.main() == expected
    captured = capsys.readouterr()
    assert captured.out == ""
    if expected == 0:
        assert captured.err == ""
    elif expected == 1:
        assert PATH in captured.err
        assert "PYS250" in captured.err
        assert "fix the new finding" in captured.err
    else:
        assert "--tool" in captured.err
