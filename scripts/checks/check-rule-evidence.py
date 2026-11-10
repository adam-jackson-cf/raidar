#!/usr/bin/env python3
"""Check the authoritative catalogue and fail-closed ledger decisions."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SNAPSHOT = ".omp/guardrail-rules.json"
LEDGER = ".omp/rule-coverage.json"
REFERENCE = (
    Path.home() / ".omp/agent/skills/setup-repository-guardrails/assets/guardrail-rules.json"
)


def compare_catalogue(snapshot: bytes | None, reference: bytes | None) -> list[str]:
    """Require the tracked snapshot, comparing the installed reference when available."""
    if snapshot is None:
        return [f"{SNAPSHOT}: catalogue snapshot missing; restore the tracked catalogue snapshot"]
    if reference is not None and snapshot != reference:
        return [
            f"{SNAPSHOT}: catalogue snapshot differs; "
            "copy the referenced skill catalogue byte-for-byte"
        ]
    return []


def ledger_completeness(catalogue: dict[str, Any], ledger: dict[str, Any]) -> list[str]:
    """Reject incomplete, ambiguous or unapproved adoption decisions without I/O."""
    errors = []
    rows: dict[str, list[dict[str, Any]]] = {}
    for row in ledger.get("rules", []):
        rows.setdefault(row["id"], []).append(row)
    for rule in catalogue["rules"]:
        rule_id = rule["id"]
        entries = rows.pop(rule_id, [])
        defect = None
        if not entries:
            defect = "missing row; add catalogue evidence"
        elif len(entries) != 1:
            defect = "duplicate row; keep exactly one row"
        else:
            row = entries[0]
            kind = rule.get("evidence_kind", "local_ci")
            if row.get("evidence_kind", "local_ci") != kind:
                defect = f"evidence kind differs; use {kind}"
            elif row.get("status") == "not_applicable":
                if (
                    rule.get("applicability") != "conditional"
                    or row.get("user_approval") != "approved"
                    or not all(
                        row.get(key) for key in ("reason", "evidence_path", "evidence_marker")
                    )
                ):
                    defect = "unapproved exclusion; record approved conditional evidence"
            elif row.get("status") != "implemented":
                defect = "blocked row; implement and prove enforcement"
        if defect:
            errors.append(f"{LEDGER}: {rule_id}: {defect}")
    errors.extend(
        f"{LEDGER}: {rule_id}: unknown row; remove non-catalogue evidence"
        for rule_id in sorted(rows)
    )
    return errors


def main() -> int:
    """Read tracked inputs and report actionable catalogue/ledger defects."""
    root = Path(__file__).resolve().parents[2]
    snapshot = root / SNAPSHOT
    errors = compare_catalogue(
        snapshot.read_bytes() if snapshot.is_file() else None,
        REFERENCE.read_bytes() if REFERENCE.is_file() else None,
    )
    if not errors:
        try:
            errors = ledger_completeness(
                json.loads(snapshot.read_bytes()), json.loads((root / LEDGER).read_bytes())
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors = [f"{LEDGER}: invalid evidence; repair schema: {error}"]
    for diagnostic in errors:
        print(diagnostic, file=sys.stderr)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
