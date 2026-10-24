from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from raidar.cli.ledger import ledger_group
from raidar.ledger.check import check_ledger, load_ledger
from raidar.ledger.index import README_NAME, render_index


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


def test_missing_frontmatter_refused(ledger: Path) -> None:
    (ledger / "faults" / "FCR-001.md").write_text("no frontmatter\n", encoding="utf-8")
    assert "frontmatter" in _problems(ledger)


def test_id_must_match_filename(ledger: Path) -> None:
    _write(ledger, "faults", "FCR-002", _fault())
    assert "does not match the filename" in _problems(ledger)


def test_remedy_link_must_resolve(ledger: Path) -> None:
    _write(ledger, "remedies", "M-001", _remedy(faults=["FCR-009"]))
    _index(ledger)
    assert "fault `FCR-009` does not exist" in _problems(ledger)


def test_illegal_transition_refused(ledger: Path) -> None:
    history = _history(("open", "2026-10-01"), ("resolved", "2026-10-02"))
    _write(ledger, "faults", "FCR-001", _fault(status="resolved", status_history=history))
    _index(ledger)
    assert "illegal transition `open` -> `resolved`" in _problems(ledger)


def test_legal_chain_passes(ledger: Path) -> None:
    steps = [("open", "2026-10-01"), ("targeted", "2026-10-02"), ("resolved", "2026-10-03")]
    steps += [("reopened", "2026-10-03"), ("targeted", "2026-10-05")]
    _write(ledger, "faults", "FCR-001", _fault(status="targeted", status_history=_history(*steps)))
    _index(ledger)
    assert check_ledger(ledger) == []


def test_dates_must_not_go_backwards(ledger: Path) -> None:
    history = _history(("open", "2026-10-05"), ("targeted", "2026-10-02"))
    _write(ledger, "faults", "FCR-001", _fault(status="targeted", status_history=history))
    _index(ledger)
    assert "precedes" in _problems(ledger)


def test_status_must_equal_last_history_entry(ledger: Path) -> None:
    _write(ledger, "faults", "FCR-001", _fault(status="targeted"))
    _index(ledger)
    assert "differs from last history status" in _problems(ledger)


def test_first_history_status_must_be_initial(ledger: Path) -> None:
    history = _history(("targeted", "2026-10-01"))
    _write(ledger, "faults", "FCR-001", _fault(status="targeted", status_history=history))
    _index(ledger)
    assert "first history status must be `open`" in _problems(ledger)


@pytest.mark.parametrize("ref", ["/abs/path.log", "../escape.log", "has space.log"])
def test_malformed_evidence_ref_refused(ledger: Path, ref: str) -> None:
    _write(ledger, "faults", "FCR-001", _fault(evidence=[ref]))
    assert "evidence ref" in _problems(ledger)


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
