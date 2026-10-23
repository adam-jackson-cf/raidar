"""gate-effort@10: autofix counts that compare across gate modes (iteration 4 record, I5).

- every @9 observation is kept, value for value;
- ``new_autofix_*``: the step's first pass minus what the previous gated step left for
  autofix; enforced, the previous step left 0 (its tree was fixed), so the sum is the
  first passes'; shadow, the previous dry run, so the sum telescopes to the last one's;
- a failed pass makes what depended on it unavailable with its type; a gate without
  autofix is not applicable;
- the analysis compares the new totals only for @10, marks the cumulative totals, and
  every consumer reads @10 first.
"""

from __future__ import annotations

from pathlib import Path

from raidar.analysis.manipulation import GATE_EFFORT_REFS as MANIPULATION_REFS
from raidar.analysis.markdown import CUMULATIVE_LEGEND, _autofix_family_table, _better, _legend
from raidar.analysis.outcomes import GATE_PREFIX, gate_metrics_for
from raidar.problems.sensitivities import SensitivityKind, known_sensitivities
from raidar.review.quality.gate_view import GATE_EFFORT_REFS as REVIEW_REFS
from raidar.scorers.catalog import default_scorer_registry
from raidar.scorers.gate_effort.families import FAMILIES
from raidar.scorers.gate_effort_v9 import gate_effort_v9_scorer_for_store
from raidar.scorers.gate_effort_v9.manifest import GATE_EFFORT_V9
from raidar.scorers.gate_effort_v10 import (
    GATE_EFFORT_V10,
    gate_effort_v10_scorer,
    gate_effort_v10_scorer_for_store,
)
from raidar.scorers.sdk.context import AssessContext, StepInputs
from raidar.scorers.sdk.result import TRAJECTORY_SUBJECT, AssessResult, AssessStatus
from raidar.scorers.sdk.validation import validate_result
from raidar.verifier.offline_grading import PRE_FIX_GRADE_SCORERS
from tests.test_gate_effort_scorer import REFUSED, _step, _trace, _value, _values
from tests.test_gate_effort_v2_scorer import _shadow
from tests.test_gate_effort_v4_scorer import CP1, CP2, _enforced, _store_grade
from tests.test_gate_effort_v8_scorer import _at
from tests.test_gate_effort_v9_scorer import _autofix, _enforced_autofix, _pass, _post_check
from tests.test_iteration_4_consumers import _arm

T = TRAJECTORY_SUBJECT
LINES, REMOVED = "new_autofix_lines_changed", "new_autofix_removed_blocking"


def _assess(*steps: StepInputs, store: Path | None = None) -> AssessResult:
    ctx = AssessContext(problem="scb/forge", steps=steps, config={})
    scorer = gate_effort_v10_scorer_for_store(store) if store else gate_effort_v10_scorer()
    return validate_result(GATE_EFFORT_V10, ctx, scorer.assess(ctx))


def _status(result: AssessResult, metric: str, subject: str) -> AssessStatus:
    return _values(result)[(metric, subject)].status


def _shadow_steps(tmp_path: Path, lines: tuple[int, int], fixed: tuple[dict, dict]) -> tuple:
    steps = (_step(tmp_path, CP1), _step(tmp_path, CP2))
    for step, count, codes in zip(steps, lines, fixed, strict=True):
        trace = _trace(step)
        _shadow(trace, REFUSED, findings=[_at("FORMAT"), _at("C901", line=9)])
        dry = _pass(
            1,
            mode="dry_run",
            lines_changed=count,
            codes_fixed_blocking=codes,
            post_check=_post_check(trace, [_at("C901", line=9)]),
        )
        _autofix(trace, [dry], mode="dry_run")
    return steps


# ---- @9 kept ---------------------------------------------------------------------------------


def test_v10_keeps_every_v9_observation(tmp_path: Path) -> None:
    store = tmp_path / "store"
    _store_grade(store, CP1, passed=4, failed=0)
    for steps in ((_enforced_autofix(tmp_path / "e"),), _enforced(tmp_path / "g")):
        ctx = AssessContext(problem="scb/forge", steps=steps, config={})
        v9_scorer = gate_effort_v9_scorer_for_store(store)
        v9 = _values(validate_result(GATE_EFFORT_V9, ctx, v9_scorer.assess(ctx)))
        v10 = _values(_assess(*steps, store=store))

        assert set(v9) < set(v10)
        assert {key for key in v9 if v9[key] != v10[key]} == set()


def test_registration_and_config() -> None:
    registration = default_scorer_registry().get("gate-effort@10")
    assert registration.manifest.version == "10"
    ctx = AssessContext(problem="scb/forge", steps=(), config={"x": 1})
    result = gate_effort_v10_scorer().assess(ctx)
    assert (result.status, result.reason) == (AssessStatus.ERROR, "gate-effort@10 takes no config")


# ---- enforced and shadow -----------------------------------------------------------------------


def test_enforced_new_counts_are_the_first_pass_since_the_previous_tree_was_fixed(
    tmp_path: Path,
) -> None:
    step = _enforced_autofix(tmp_path)

    result = _assess(step)

    assert _value(result, LINES, CP1) == 6
    assert _value(result, REMOVED, CP1) == 2
    assert _value(result, f"{REMOVED}.style", CP1) == 2
    assert _value(result, f"total_{LINES}", T) == _value(result, "total_autofix_lines_changed", T)
    assert _value(result, f"total_{REMOVED}", T) == 2


def test_shadow_new_counts_telescope_to_the_last_dry_run(tmp_path: Path) -> None:
    fixed = ({"FORMAT": 1, "I001": 1}, {"FORMAT": 1, "I001": 3, "UP017": 1})
    steps = _shadow_steps(tmp_path, (10, 25), fixed)

    result = _assess(*steps)

    assert [_value(result, LINES, step) for step in (CP1, CP2)] == [10, 15]
    assert [_value(result, REMOVED, step) for step in (CP1, CP2)] == [2, 3]
    assert _value(result, "total_autofix_lines_changed", T) == 35, "the cumulative sum"
    assert _value(result, f"total_{LINES}", T) == 25, "the last dry run's count"
    assert _value(result, f"total_{REMOVED}", T) == 5
    assert sum(_value(result, f"total_{REMOVED}.{family}", T) or 0 for family in FAMILIES) == 5


def test_a_failed_shadow_pass_leaves_its_step_and_the_next_unavailable(tmp_path: Path) -> None:
    steps = _shadow_steps(tmp_path, (10, 25), ({"FORMAT": 1}, {"FORMAT": 2}))
    failed = _pass(1, mode="dry_run", error_type="copy_failed", error="disk full")
    _autofix(_trace(steps[0]), [failed], mode="dry_run")

    result = _assess(*steps)

    first = _values(result)[(LINES, CP1)]
    assert first.status is AssessStatus.UNAVAILABLE
    assert "copy_failed" in (first.reason or "")
    after = _values(result)[(LINES, CP2)]
    assert after.status is AssessStatus.UNAVAILABLE
    assert "previous gated step" in (after.reason or "")
    assert _status(result, f"total_{LINES}", T) is AssessStatus.UNAVAILABLE


def test_an_enforced_step_whose_last_pass_failed_leaves_the_next_step_unavailable(
    tmp_path: Path,
) -> None:
    first, second = _enforced(tmp_path)
    good = _pass(1, before=1)
    _autofix(_trace(first), [good, _pass(2, before=2, error_type="timeout", error="120 s")])
    _autofix(_trace(second), [_pass(1, before=1)])

    result = _assess(first, second)

    assert _value(result, LINES, CP1) == 6
    after = _values(result)[(LINES, CP2)]
    assert after.status is AssessStatus.UNAVAILABLE
    assert "timeout" in (after.reason or "")


def test_a_gate_without_autofix_has_no_new_autofix_values(tmp_path: Path) -> None:
    result = _assess(*_enforced(tmp_path))

    assert _status(result, LINES, CP1) is AssessStatus.NOT_APPLICABLE
    assert _status(result, f"total_{REMOVED}", T) is AssessStatus.NOT_APPLICABLE


# ---- consumers --------------------------------------------------------------------------------


def test_the_new_totals_are_compared_only_for_v10_and_the_cumulative_ones_are_marked() -> None:
    v10 = {metric.id.removeprefix(GATE_PREFIX) for metric in gate_metrics_for("gate-effort@10")}
    v9 = {metric.id.removeprefix(GATE_PREFIX) for metric in gate_metrics_for("gate-effort@9")}

    assert v10 - v9 == {
        f"total_{LINES}",
        f"total_{REMOVED}",
        *(f"total_{REMOVED}.{family}" for family in FAMILIES),
    }
    assert _better("gate.total_autofix_lines_changed") == "neutral †"
    assert _better("gate.total_first_run_blocking").endswith(" †")
    assert _better(f"gate.total_{LINES}") == "neutral"
    assert _legend(["gate.total_autofix_rounds"]) == ["", CUMULATIVE_LEGEND, ""]
    assert _legend([f"gate.total_{LINES}"]) == []


def test_the_family_table_shows_the_new_counts_beside_the_sums_for_v10() -> None:
    arm = _arm(
        "r1",
        {
            "gate.total_autofix_removed_blocking": 40.0,
            "gate.total_autofix_removed_blocking.style": 40.0,
            f"gate.total_{REMOVED}": 9.0,
            f"gate.total_{REMOVED}.style": 9.0,
        },
    )

    table = "\n".join(_autofix_family_table([arm], ["r1"]))

    assert "| style | 40 / 9 |" in table
    assert "each step's own work added" in table


def test_every_consumer_reads_v10_first() -> None:
    assert MANIPULATION_REFS[0] == "gate-effort@10"
    assert REVIEW_REFS[0] == "gate-effort@10"
    assert "gate-effort@10" in PRE_FIX_GRADE_SCORERS


# ---- forge's ordering cases: spec-determined sensitivities, not errata (18.11) ---------------

REPO = Path(__file__).resolve().parents[2]
FORGE = "scb/forge@38d627ecf668"


def test_forges_uuid_tie_break_cases_are_spec_determined_randomness_sensitivities() -> None:
    found = {note.case: note for note in known_sensitivities([FORGE], start=REPO)[FORGE]}
    errata = (REPO / "problems/registry/scb/forge/38d627ecf668.errata.yaml").read_text()

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
