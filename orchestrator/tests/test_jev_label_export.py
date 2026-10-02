"""K4: review-surface labels export as Jev calibration rows the H2 import accepts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.jev.audit import AuditLog
from raidar.jev.calibration import LabelImportError, import_labels, promotion_readiness
from raidar.jev.label_export import (
    LabelExportError,
    ReviewLabel,
    export_labels,
    load_review_labels,
)
from raidar.jev.rubric import RUBRIC_POLICY
from raidar.review_quality.synthetic import generate_quality_fixture

RUN = "synthetic-quality.synthetic-run-1.b00-p0-baseline"


@pytest.fixture
def store(tmp_path: Path) -> Path:
    return generate_quality_fixture(tmp_path / "fx") / "store"


def _decision_ids(store: Path) -> list[str]:
    return sorted(
        record.request_id for record in AuditLog.for_store(store).decisions(RUBRIC_POLICY)
    )


def _surface_label(
    request_id: str, label: str, created_at: int, **extra: object
) -> dict[str, object]:
    """A label as review-surface/quality-api.mjs writes it."""
    return {
        "id": f"jevlabel-{request_id}-{created_at}",
        "run_id": RUN,
        "request_id": request_id,
        "policy": extra.pop("policy", RUBRIC_POLICY),
        "label": label,
        "note": extra.pop("note", None),
        "source": extra.pop("source", "user"),
        "category": "jev_label",
        "created_at": created_at,
        **extra,
    }


def _write(path: Path, labels: list[dict[str, object]]) -> Path:
    path.write_text(json.dumps(labels), encoding="utf-8")
    return path


def test_exported_labels_load_into_the_calibration_workflow(store: Path, tmp_path: Path) -> None:
    first, second, third = _decision_ids(store)[:3]
    labels = load_review_labels(
        _write(
            tmp_path / "jev-labels.json",
            [
                _surface_label(first, "false_positive", 1),
                _surface_label(first, "correct", 2, note="checked the span"),
                _surface_label(second, "false_negative", 1),
                _surface_label(third, "correct", 1, policy="process-compliance.residue"),
            ],
        )
    )
    audit = AuditLog.for_store(store)
    exported = export_labels(labels, RUBRIC_POLICY, audit=audit)
    assert exported.rows == (
        {"request_id": first, "label": "correct", "reviewer": "human", "note": "checked the span"},
        {"request_id": second, "label": "false_negative", "reviewer": "human"},
    )
    assert exported.superseded == 1
    assert import_labels(audit, RUBRIC_POLICY, exported.rows) == 2
    assert {key: record.label for key, record in audit.labels(RUBRIC_POLICY).items()} == {
        first: "correct",
        second: "false_negative",
    }
    readiness = promotion_readiness(audit, RUBRIC_POLICY)
    assert readiness.labelled == 2
    assert not readiness.ready


def test_label_provenance_carries_reviewer_kind_and_model_into_readiness(
    store: Path, tmp_path: Path
) -> None:
    first, second, third = _decision_ids(store)[:3]
    model = {"reviewer": "model", "reviewer_model": "anthropic/claude-haiku-4-5", "source": "model"}
    legacy = _surface_label(third, "correct", 1)
    del legacy["source"]  # written before the surface recorded provenance
    labels = load_review_labels(
        _write(
            tmp_path / "jev-labels.json",
            [
                _surface_label(first, "correct", 1),
                _surface_label(second, "correct", 1, **model),
                legacy,
            ],
        )
    )
    audit = AuditLog.for_store(store)
    exported = export_labels(labels, RUBRIC_POLICY, audit=audit)
    # Rows are in request-id order, and the ids were taken sorted.
    assert [(row["reviewer"], row.get("reviewer_model")) for row in exported.rows] == [
        ("human", None),
        ("model", "anthropic/claude-haiku-4-5"),
        ("human", None),
    ]
    assert exported.summary()["by_reviewer"] == {"human": 2, "model": 1}
    import_labels(audit, RUBRIC_POLICY, exported.rows)
    stored = audit.labels(RUBRIC_POLICY)
    assert stored[second].reviewer_ref == "model:anthropic/claude-haiku-4-5"
    assert stored[first].reviewer_ref == "human"
    readiness = promotion_readiness(audit, RUBRIC_POLICY)
    assert sum(readiness.by_reviewer.values()) == readiness.labelled
    assert set(readiness.reviewer_kinds) <= {"human", "model"}
    assert readiness.summary()["labelled_by_reviewer_kind"] == readiness.reviewer_kinds

    unnamed = _surface_label(first, "correct", 3, reviewer="model")
    with pytest.raises(LabelExportError, match="reviewer_model"):
        load_review_labels(_write(tmp_path / "bad.json", [unnamed]))
    with pytest.raises(LabelImportError, match="reviewer_model"):
        import_labels(
            audit,
            RUBRIC_POLICY,
            [{"request_id": _decision_ids(store)[3], "label": "correct", "reviewer": "model"}],
        )


def test_already_imported_decisions_are_left_out_so_a_reexport_imports_cleanly(
    store: Path,
) -> None:
    first, second = _decision_ids(store)[:2]
    audit = AuditLog.for_store(store)
    import_labels(audit, RUBRIC_POLICY, [{"request_id": first, "label": "correct"}])
    labels = [
        ReviewLabel.model_validate(_surface_label(first, "false_positive", 5)),
        ReviewLabel.model_validate(_surface_label(second, "uncertain", 5)),
    ]
    exported = export_labels(labels, RUBRIC_POLICY, audit=audit)
    assert exported.already_labelled == (first,)
    assert import_labels(audit, RUBRIC_POLICY, exported.rows) == 1
    with pytest.raises(LabelImportError):
        import_labels(audit, RUBRIC_POLICY, export_labels(labels, RUBRIC_POLICY).rows)


def test_labels_for_decisions_the_audit_does_not_hold_are_refused(store: Path) -> None:
    labels = [ReviewLabel.model_validate(_surface_label("jevp_missing", "correct", 1))]
    with pytest.raises(LabelExportError, match="jevp_missing"):
        export_labels(labels, RUBRIC_POLICY, audit=AuditLog.for_store(store))


def test_malformed_label_files_are_refused(tmp_path: Path) -> None:
    with pytest.raises(LabelExportError, match="not one of"):
        load_review_labels(_write(tmp_path / "bad.json", [_surface_label("x", "ok", 1)]))
    (tmp_path / "object.json").write_text("{}")
    with pytest.raises(LabelExportError, match="JSON list"):
        load_review_labels(tmp_path / "object.json")
    with pytest.raises(LabelExportError, match="cannot read"):
        load_review_labels(tmp_path / "absent.json")


def test_cli_export_then_import_round_trip(store: Path, tmp_path: Path) -> None:
    ids = _decision_ids(store)
    labels = _write(
        tmp_path / "jev-labels.json",
        [_surface_label(ids[0], "correct", 1), _surface_label(ids[1], "false_positive", 1)],
    )
    rows = tmp_path / "out" / "labels.jsonl"
    args = ["jev", "export-labels", "--labels", str(labels), "--policy", RUBRIC_POLICY]
    exported = CliRunner().invoke(main, [*args, "--store", str(store), "--output", str(rows)])
    assert exported.exit_code == 0, exported.output
    assert json.loads(exported.output)["exported"] == 2
    imported = CliRunner().invoke(
        main, ["jev", "import-labels", "--store", str(store), "--policy", RUBRIC_POLICY, str(rows)]
    )
    assert imported.exit_code == 0, imported.output
    assert json.loads(imported.output)["imported"] == 2
    refused = CliRunner().invoke(
        main,
        [*args[:3], str(_write(tmp_path / "x.json", [_surface_label("jevp_x", "correct", 1)])),
         *args[4:], "--store", str(store), "--output", str(rows)],
    )  # fmt: skip
    assert refused.exit_code == 1
    assert "jevp_x" in refused.output
