"""Verifier sensitivities: loading rules, and no spec-determined case is ever an erratum (18.11)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from raidar.design.problem.models import ProblemId
from raidar.design.problem.sensitivities import (
    SCHEMA,
    SENSITIVITIES_SUFFIX,
    SensitivityError,
    SensitivityKind,
    known_sensitivities,
    load_sensitivities,
    sensitivities_path,
)

REPO = Path(__file__).resolve().parents[2]
REF = "scb/forge@38d627ecf668"
CASE = "test_checkpoint_1.py::test_tiebreak"


def _document(**changes: Any) -> dict[str, Any]:
    note = {
        "case": CASE,
        "checkpoint": "checkpoint_1",
        "kind": "randomness",
        "spec_determined": True,
        "spec": "checkpoint_1.md: ties",
        "mechanism": "uuid4 order on a tie",
        "recorded": "2026-10-02 audit",
    }
    return {"schema": SCHEMA, "problem": REF, "sensitivities": [note], **changes}


def _write(root: Path, document: Any) -> Path:
    path = sensitivities_path(root, ProblemId.parse(REF))
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(document))
    return path


def test_sensitivities_load_per_problem_and_a_missing_file_means_none(tmp_path: Path) -> None:
    assert known_sensitivities([REF], store=tmp_path) == {}
    path = _write(tmp_path, _document())
    assert path.name == f"38d627ecf668{SENSITIVITIES_SUFFIX}"
    (note,) = known_sensitivities([REF, REF, "not-a-registry-reference"], store=tmp_path)[REF]
    assert (note.case, note.kind, note.spec_determined) == (CASE, SensitivityKind.RANDOMNESS, True)
    assert load_sensitivities(tmp_path / "absent.yaml", problem=REF) == ()


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (_document(schema="other/1"), "schema 'other/1'"),
        (_document(problem="scb/other@38d627ecf668"), "names problem"),
        (_document(sensitivities=[]), "sensitivities"),
        ({"schema": SCHEMA}, "problem"),
    ],
)
def test_an_invalid_sensitivities_file_is_refused(
    tmp_path: Path, document: dict[str, Any], message: str
) -> None:
    path = _write(tmp_path, document)
    with pytest.raises(SensitivityError, match=message):
        load_sensitivities(path, problem=REF)


def test_unparseable_sensitivities_are_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, {})
    path.write_text("sensitivities: [unclosed\n")
    with pytest.raises(SensitivityError):
        load_sensitivities(path, problem=REF)


def test_no_committed_errata_excludes_a_case_recorded_as_spec_determined() -> None:
    """A spec-determined sensitivity is chance, not an erratum: errata never excludes it."""
    store = REPO / "lab" / "problems"
    checked = 0
    for notes_path in sorted((store / "registry").rglob(f"*{SENSITIVITIES_SUFFIX}")):
        ref = f"{notes_path.parent.relative_to(store / 'registry').as_posix()}@"
        ref += notes_path.name.removesuffix(SENSITIVITIES_SUFFIX)
        determined = {
            note.case
            for note in known_sensitivities([ref], store=store)[ref]
            if note.spec_determined
        }
        errata_name = notes_path.name.replace(SENSITIVITIES_SUFFIX, ".errata.yaml")
        errata_path = notes_path.with_name(errata_name)
        recorded = yaml.safe_load(errata_path.read_text()) if errata_path.is_file() else {}
        excluded = {case["id"] for case in (recorded or {}).get("cases", [])}
        assert not determined & excluded, notes_path
        checked += 1
    assert checked, "the committed store records at least one sensitivity file"
