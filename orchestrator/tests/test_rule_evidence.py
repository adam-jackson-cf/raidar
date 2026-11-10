"""Fast decisions: catalogue bytes and complete, approved rule evidence fail closed."""

from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ".omp/rule-coverage.json"
SNAPSHOT = ".omp/guardrail-rules.json"


@pytest.fixture
def evidence() -> ModuleType:
    """Load the production decision seam without starting a checker process."""
    path = ROOT / "scripts/checks/check-rule-evidence.py"
    assert path.is_file(), f"missing production evidence decision seam: {path}"
    spec = importlib.util.spec_from_file_location("rule_evidence_proofs", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def catalogue() -> dict[str, Any]:
    """Use every authoritative catalogue ID, not a reduced test-only catalogue."""
    path = ROOT / SNAPSHOT
    assert path.is_file(), f"missing tracked catalogue snapshot: {path}"
    catalog: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    assert len(catalog["rules"]) == 104
    return catalog


def test_identical_catalogue_bytes_pass(evidence: ModuleType) -> None:
    snapshot = b'{"schema_version":1,"rules":[]}\n'
    assert evidence.compare_catalogue(snapshot, snapshot) == []


@pytest.mark.parametrize("reference", [None, b"catalogue\n"])
def test_missing_repository_snapshot_fails(evidence: ModuleType, reference: bytes | None) -> None:
    assert evidence.compare_catalogue(None, reference) == [
        f"{SNAPSHOT}: catalogue snapshot missing; restore the tracked catalogue snapshot"
    ]


def test_offline_skill_absence_keeps_repository_snapshot_authoritative(
    evidence: ModuleType,
) -> None:
    assert evidence.compare_catalogue(b"catalogue\n", None) == []


@pytest.mark.parametrize("offset", range(len(b"catalogue\n")))
def test_any_catalogue_byte_change_fails(evidence: ModuleType, offset: int) -> None:
    reference = b"catalogue\n"
    changed = bytearray(reference)
    changed[offset] ^= 1
    assert evidence.compare_catalogue(bytes(changed), reference) == [
        f"{SNAPSHOT}: catalogue snapshot differs; copy the referenced skill catalogue byte-for-byte"
    ]


@pytest.mark.parametrize("snapshot", [b"catalogue", b"catalogue\n\n"])
def test_catalogue_length_drift_fails(evidence: ModuleType, snapshot: bytes) -> None:
    assert evidence.compare_catalogue(snapshot, b"catalogue\n") == [
        f"{SNAPSHOT}: catalogue snapshot differs; copy the referenced skill catalogue byte-for-byte"
    ]


def _complete_ledger(catalogue: dict[str, Any]) -> dict[str, Any]:
    """Completeness does not pretend to validate filesystem or executed evidence."""
    return {
        "schema_version": 1,
        "scope": {"mode": "full", "paths": []},
        "rules": [
            {
                "id": rule["id"],
                "evidence_kind": rule.get("evidence_kind", "local_ci"),
                "status": "implemented",
            }
            for rule in catalogue["rules"]
        ],
    }


def test_all_catalogue_ids_with_matching_evidence_kinds_pass(
    evidence: ModuleType, catalogue: dict[str, Any]
) -> None:
    ledger = _complete_ledger(catalogue)
    ledger["rules"].reverse()
    assert evidence.ledger_completeness(catalogue, ledger) == []


@pytest.mark.parametrize("defect", ["missing", "duplicate", "kind", "exclusion", "blocked"])
def test_each_catalogue_row_defect_has_one_actionable_line(
    evidence: ModuleType, catalogue: dict[str, Any], defect: str
) -> None:
    ledger = _complete_ledger(catalogue)
    row = next(row for row in ledger["rules"] if row["id"] == "PYQ210")
    expected = {
        "missing": "missing row; add catalogue evidence",
        "duplicate": "duplicate row; keep exactly one row",
        "kind": "evidence kind differs; use local_ci",
        "exclusion": "unapproved exclusion; record approved conditional evidence",
        "blocked": "blocked row; implement and prove enforcement",
    }
    if defect == "missing":
        ledger["rules"].remove(row)
    elif defect == "duplicate":
        ledger["rules"].append(deepcopy(row))
    elif defect == "kind":
        row["evidence_kind"] = "host_query"
    elif defect == "exclusion":
        row.update(status="not_applicable", reason="No nominal analyzer hierarchy")
    else:
        row["status"] = "blocked"
    assert evidence.ledger_completeness(catalogue, ledger) == [
        f"{LEDGER}: PYQ210: {expected[defect]}"
    ]


def test_approved_conditional_exclusion_passes_completeness(
    evidence: ModuleType, catalogue: dict[str, Any]
) -> None:
    ledger = _complete_ledger(catalogue)
    row = next(row for row in ledger["rules"] if row["id"] == "PYQ210")
    row.update(
        status="not_applicable",
        reason="Structural scorer protocols do not require nominal analyzer inheritance",
        user_approval="approved",
        evidence_path="docs/reboot/evidence/decide-analyzer-sol.md",
        evidence_marker="PYQ210/211 currently not applicable",
    )
    assert evidence.ledger_completeness(catalogue, ledger) == []


def test_owner_approval_cannot_exclude_an_always_applicable_rule(
    evidence: ModuleType, catalogue: dict[str, Any]
) -> None:
    ledger = _complete_ledger(catalogue)
    row = ledger["rules"][0]
    assert catalogue["rules"][0]["applicability"] == "always"
    row.update(status="not_applicable", user_approval="approved", reason="Owner requested")
    assert evidence.ledger_completeness(catalogue, ledger) == [
        f"{LEDGER}: {row['id']}: unapproved exclusion; record approved conditional evidence"
    ]
