"""Decision proofs for TEST.tiers and GATE.pre-commit affected T2 selection."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
SEAMS = (
    "orchestrator/tests/test_engine_slow.py",
    "orchestrator/tests/test_gate_quality.py",
    "orchestrator/tests/test_subject_slow.py",
)
OWNERS = {
    "orchestrator/src/raidar/engine/": (SEAMS[0],),
    "orchestrator/src/raidar/subject/": (SEAMS[2],),
    "scripts/checks/": (SEAMS[1],),
}


class SeamSelector(Protocol):
    """The in-memory decision used by the affected T2 process shell."""

    def select_affected_tests(
        self,
        changed_paths: Sequence[str],
        seam_tests: Sequence[str],
        owners: Mapping[str, Sequence[str]],
    ) -> list[str]: ...


@pytest.fixture
def selector() -> SeamSelector:
    """Load the check script the same way as repository-gate proofs."""
    source = ROOT / "scripts/checks/run-affected-seams.py"
    assert source.is_file(), "TEST.tiers: missing affected T2 seam selector"
    spec = importlib.util.spec_from_file_location("affected_seam_proof", source)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast("SeamSelector", module)


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        ([], []),
        (["orchestrator/src/raidar/engine/trial/runner.py"], [SEAMS[0]]),
        (["scripts/checks/check-repository-paths.py"], [SEAMS[1]]),
        (
            [
                "orchestrator/src/raidar/subject/primitive/run.py",
                "orchestrator/src/raidar/engine/trial/runner.py",
                "orchestrator/src/raidar/engine/trial/runner.py",
            ],
            [SEAMS[0], SEAMS[2]],
        ),
    ],
)
def test_known_owners_select_only_their_t2_seams(
    selector: SeamSelector, changed: list[str], expected: list[str]
) -> None:
    """TEST.tiers: known changes select their union, not unrelated T2 files."""
    assert selector.select_affected_tests(changed, SEAMS, OWNERS) == expected


@pytest.mark.parametrize(
    "changed",
    [
        ["orchestrator/src/raidar/new_owner/run.py"],
        ["orchestrator/src/raidar/engine_extra/run.py"],
        ["contract/src/raidar_contract/package.py"],
        ["Makefile"],
        ["orchestrator/src/raidar/engine/trial/runner.py", "unmapped/new.py"],
        ["../outside.py"],
    ],
)
def test_unknown_ownership_conservatively_selects_every_t2_seam(
    selector: SeamSelector, changed: list[str]
) -> None:
    """GATE.pre-commit: unknown ownership never silently skips relevant T2."""
    assert selector.select_affected_tests(changed, SEAMS, OWNERS) == list(SEAMS)


def test_changed_t2_file_selects_itself_without_a_source_owner(selector: SeamSelector) -> None:
    """TEST.tiers: editing a known seam must exercise that seam."""
    assert selector.select_affected_tests([SEAMS[2]], SEAMS, OWNERS) == [SEAMS[2]]


def test_stale_owner_destination_falls_back_to_full_t2(selector: SeamSelector) -> None:
    """GATE.pre-commit: a stale map cannot turn an affected change into no tests."""
    owners = {"orchestrator/src/raidar/engine/": ("orchestrator/tests/deleted.py",)}
    assert selector.select_affected_tests(
        ["orchestrator/src/raidar/engine/trial/runner.py"], SEAMS, owners
    ) == list(SEAMS)


def test_overlapping_owner_entries_preserve_every_affected_seam(selector: SeamSelector) -> None:
    """TEST.tiers: narrower ownership cannot hide a broader affected seam."""
    owners = {
        "orchestrator/src/raidar/": (SEAMS[1],),
        "orchestrator/src/raidar/engine/": (SEAMS[0],),
    }
    assert selector.select_affected_tests(
        ["orchestrator/src/raidar/engine/trial/runner.py"], SEAMS, owners
    ) == [SEAMS[0], SEAMS[1]]


def test_selection_is_independent_of_input_order(selector: SeamSelector) -> None:
    """TEST.tiers: source, inventory and map order do not alter the selection."""
    changed = ["scripts/checks/policy.py", "orchestrator/src/raidar/engine/trial/run.py"]
    assert selector.select_affected_tests(
        changed[::-1], SEAMS[::-1], dict(reversed(tuple(OWNERS.items())))
    ) == [SEAMS[0], SEAMS[1]]
