from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from pydantic import ValidationError

from raidar.cli.ledger import ledger_group
from raidar.evaluation.ledger.check import check_ledger, load_ledger, record_problems
from raidar.evaluation.ledger.index import README_NAME, render_index
from raidar.evaluation.ledger.records import FaultRecord, RemedyRecord


def _history(*steps: tuple[str, str]) -> list[dict[str, object]]:
    return [
        {"status": s, "date": d, "evidence": ["jobs/x/y.log:3"], "reason": "because"}
        for s, d in steps
    ]


def _fault(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "FCR-001",
        "title": "A fault",
        "category": "Process",
        "owner": "raidar:verifier",
        "status": "open",
        "evidence": ["jobs/a/b.log:1-2"],
        "configurations": ["cfg"],
        "impact": "bad",
        "hypothesis": "maybe",
        "status_history": _history(("open", "2026-10-01")),
    }
    return base | over


def _remedy(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "M-001",
        "title": "A remedy",
        "faults": ["FCR-001"],
        "lever": "lever",
        "placement": "place",
        "home": "raidar",
        "rationale": "why",
        "proof_plan": "how",
        "status": "proposed",
        "status_history": _history(("proposed", "2026-10-02")),
    }
    return base | over


def _write(root: Path, sub: str, name: str, data: dict[str, object]) -> None:
    path = root / sub / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{yaml.safe_dump(data, sort_keys=False)}---\nbody\n", encoding="utf-8")


def _index(root: Path) -> None:
    faults, remedies, problems = load_ledger(root)
    assert problems == []
    (root / README_NAME).write_text(render_index(faults, remedies), encoding="utf-8")


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    _write(tmp_path, "faults", "FCR-001", _fault())
    _write(tmp_path, "remedies", "M-001", _remedy())
    _index(tmp_path)
    return tmp_path


def _problems(root: Path) -> str:
    return "\n".join(check_ledger(root))


def test_clean_ledger_passes(ledger: Path) -> None:
    assert check_ledger(ledger) == []


def test_unknown_key_refused(ledger: Path) -> None:
    _write(ledger, "faults", "FCR-001", _fault(surprise=1))
    assert "surprise" in _problems(ledger)


def test_missing_required_field_refused(ledger: Path) -> None:
    data = _fault()
    del data["impact"]
    _write(ledger, "faults", "FCR-001", data)
    assert "impact" in _problems(ledger)


@pytest.mark.parametrize(
    "text",
    ["no frontmatter\n", "---\nid: FCR-001\n", "---\n- item\n---\n", "---\nkey: [\n---\n"],
)
def test_malformed_frontmatter_refuses_indexing_without_overwriting(
    ledger: Path, text: str
) -> None:
    index = ledger / README_NAME
    before = index.read_bytes()
    (ledger / "faults" / "FCR-001.md").write_text(text, encoding="utf-8")
    assert "frontmatter" in _problems(ledger)
    result = CliRunner().invoke(ledger_group, ["index", "--root", str(ledger)])
    assert result.exit_code == 1
    assert "faults/FCR-001.md" in result.output
    assert index.read_bytes() == before


def test_id_must_match_filename(ledger: Path) -> None:
    _write(ledger, "faults", "FCR-002", _fault())
    assert "does not match the filename" in _problems(ledger)


def test_remedy_links_resolve_in_memory() -> None:
    fault = FaultRecord.model_validate(_fault())
    remedy = RemedyRecord.model_validate(_remedy(faults=["FCR-009"]))
    assert record_problems({fault.id: fault}, {remedy.id: remedy}) == [
        "M-001: fault `FCR-009` does not exist"
    ]
    linked = remedy.model_copy(update={"faults": [fault.id]})
    assert record_problems({fault.id: fault}, {linked.id: linked}) == []


@pytest.mark.parametrize(
    ("kind", "steps"),
    [
        ("fault", ["open", "targeted", "resolved", "reopened", "targeted"]),
        ("fault", ["open", "targeted", "mitigated", "resolved"]),
        ("fault", ["open", "not-reproducible"]),
        ("fault", ["open", "routed"]),
        ("remedy", ["proposed", "locked", "executed", "measured", "adopted"]),
        ("remedy", ["proposed", "locked", "rejected"]),
    ],
)
def test_legal_status_chains_pass_without_reading_files(kind: str, steps: list[str]) -> None:
    history = _history(*((status, f"2026-10-{index + 1:02}") for index, status in enumerate(steps)))
    if kind == "fault":
        fault = FaultRecord.model_validate(_fault(status=steps[-1], status_history=history))
        assert record_problems({fault.id: fault}, {}) == []
    else:
        fault = FaultRecord.model_validate(_fault())
        remedy = RemedyRecord.model_validate(_remedy(status=steps[-1], status_history=history))
        assert record_problems({fault.id: fault}, {remedy.id: remedy}) == []


@pytest.mark.parametrize(
    ("status", "steps", "problem"),
    [
        ("resolved", [("open", "2026-10-01"), ("resolved", "2026-10-02")], "illegal transition"),
        ("targeted", [("open", "2026-10-05"), ("targeted", "2026-10-02")], "precedes"),
        ("targeted", [("open", "2026-10-01")], "differs from last history status"),
        ("targeted", [("targeted", "2026-10-01")], "first history status"),
        ("open", [("open", "2026-10-01"), ("unknown", "2026-10-02")], "unknown history status"),
    ],
)
def test_history_integrity_reports_the_broken_rule(status: str, steps: list, problem: str) -> None:
    fault = FaultRecord.model_validate(_fault(status=status, status_history=_history(*steps)))
    assert problem in "\n".join(record_problems({fault.id: fault}, {}))


@pytest.mark.parametrize("ref", ["/abs/path.log", "../escape.log", "has space.log"])
def test_malformed_evidence_ref_refused(ledger: Path, ref: str) -> None:
    _write(ledger, "faults", "FCR-001", _fault(evidence=[ref]))
    assert "evidence ref" in _problems(ledger)


@pytest.mark.parametrize(
    "ref",
    ["factory-x-loops@lab-final-2026-10-31:docs/x.md:3-9", "FXL-E-011", "FXL-Q-009"],
)
def test_fxl_source_and_journal_refs_accepted(ledger: Path, ref: str) -> None:
    _write(ledger, "faults", "FCR-001", _fault(evidence=[ref]))
    _index(ledger)
    assert check_ledger(ledger) == []


def test_fxl_prefixed_records_accepted(ledger: Path) -> None:
    _write(ledger, "faults", "FXL-FCR-007", _fault(id="FXL-FCR-007"))
    _write(ledger, "remedies", "FXL-M-002", _remedy(id="FXL-M-002", faults=["FXL-FCR-007"]))
    _index(ledger)
    assert check_ledger(ledger) == []


def test_jobs_refs_need_not_exist(ledger: Path) -> None:
    assert not (ledger / "jobs").exists()
    assert check_ledger(ledger) == []


def test_stale_readme_refused(ledger: Path) -> None:
    (ledger / README_NAME).write_text("stale\n", encoding="utf-8")
    assert "out of date" in _problems(ledger)


def test_index_is_deterministic_and_groups_by_status(ledger: Path) -> None:
    faults, remedies, _ = load_ledger(ledger)
    first = render_index(faults, remedies)
    assert first == render_index(dict(reversed(list(faults.items()))), remedies)
    for needle in (
        "### `open`",
        "[FCR-001](faults/FCR-001.md)",
        "### `proposed`",
        "[M-001](remedies/M-001.md)",
    ):
        assert needle in first


def test_cli_index_then_check(tmp_path: Path) -> None:
    _write(tmp_path, "faults", "FCR-001", _fault())
    runner = CliRunner()
    assert runner.invoke(ledger_group, ["check", "--root", str(tmp_path)]).exit_code == 1
    assert runner.invoke(ledger_group, ["index", "--root", str(tmp_path)]).exit_code == 0
    assert runner.invoke(ledger_group, ["check", "--root", str(tmp_path)]).exit_code == 0
    _write(tmp_path, "faults", "FCR-001", _fault(target="workspace | state\nnext line"))
    assert runner.invoke(ledger_group, ["index", "--root", str(tmp_path)]).exit_code == 0
    rendered = (tmp_path / README_NAME).read_text()
    assert "workspace \\| state next line" in rendered
    assert runner.invoke(ledger_group, ["check", "--root", str(tmp_path)]).exit_code == 0


@pytest.mark.parametrize("faults", [["M-001"], ["FCR-001", "FCR-invalid"]])
def test_remedy_cannot_link_non_fault_identifiers(faults: list[str]) -> None:
    with pytest.raises(ValidationError, match="not fault ids"):
        RemedyRecord.model_validate(_remedy(faults=faults))


@pytest.mark.parametrize("experiment", [None, "lab/experiments/ab-test/trial/experiment.yaml"])
def test_remedy_experiment_is_optional_but_must_be_a_relative_reference(
    experiment: str | None,
) -> None:
    remedy = RemedyRecord.model_validate(_remedy(experiment=experiment))

    fault = FaultRecord.model_validate(_fault())
    assert record_problems({fault.id: fault}, {remedy.id: remedy}) == []
    assert remedy.model_dump()["experiment"] == experiment


@pytest.mark.parametrize("experiment", ["/outside/experiment.yaml", "../escape.yaml", "bad path"])
def test_remedy_refuses_unsafe_experiment_references(experiment: str) -> None:
    with pytest.raises(ValidationError, match="experiment path"):
        RemedyRecord.model_validate(_remedy(experiment=experiment))
