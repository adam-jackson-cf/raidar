"""forge's ordering cases are spec-determined randomness sensitivities, not errata (18.11)."""

from __future__ import annotations

from pathlib import Path

from raidar.design.problem.sensitivities import SensitivityKind, known_sensitivities

REPO = Path(__file__).resolve().parents[2]
FORGE = "scb/forge@38d627ecf668"


def test_forges_uuid_tie_break_cases_are_spec_determined_randomness_sensitivities() -> None:
    found = {note.case: note for note in known_sensitivities([FORGE], start=REPO)[FORGE]}
    errata = (REPO / "lab/problems/registry/scb/forge/38d627ecf668.errata.yaml").read_text()

    for case, spec in (
        (
            "test_checkpoint_1.py::"
            "test_blueprint_list_uuid_tiebreak_when_created_at_and_name_collide",
            "checkpoint_1/instruction.md:64",
        ),
        ("test_checkpoint_2.py::test_allocation_list_ordering", "checkpoint_2/instruction.md:58"),
    ):
        note = found[case]
        assert (note.kind, note.spec_determined) == (SensitivityKind.RANDOMNESS, True)
        assert spec in note.spec
        assert case.split("::")[1] not in errata
