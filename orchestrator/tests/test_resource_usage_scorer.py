"""resource-usage@8 behaviour: Harbor usage and timing, reconciled; unreported stays None."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from raidar.scoring.framework.atif import TrajectoryError, load_trajectory
from raidar.scoring.framework.context import AssessContext
from raidar.scoring.framework.result import AssessResult, AssessStatus, EvidenceSource, Observation
from raidar.scoring.framework.trial_inputs import agent_execution_seconds
from raidar.scoring.framework.validation import validate_result
from raidar.scoring.resource_usage import resource_usage_scorer
from tests.scorer_support import traced_memory_context
from tests.trace_trial_support import (
    TracedStep,
    agent_step,
    codex_exec,
    delegate,
    execution,
    session,
    trajectory,
    usage,
)


def _assess(ctx: AssessContext) -> AssessResult:
    scorer = resource_usage_scorer()
    return validate_result(scorer.manifest, ctx, scorer.assess(ctx))


def _by_key(result: AssessResult) -> dict[tuple[str, str], Observation]:
    return {(item.metric, item.subject): item for item in result.observations}


def _review_loop_trace() -> dict:
    """A review loop: the parent refers to an implement and a review session."""
    parent_steps = [
        {
            "step_id": index,
            "source": "system",
            "message": f"review-loop {name}",
            "observation": {"results": [{"subagent_trajectory_ref": [{"trajectory_id": name}]}]},
        }
        for index, name in ((1, "stage_1_implement"), (2, "stage_2_review"))
    ]
    implement = session(
        "stage_1_implement", agent_step(1, codex_exec("c1", "ls")), agent_step(2), agent_step(3)
    )
    review = session("stage_2_review", agent_step(1, codex_exec("c2", "pytest -q")), agent_step(2))
    return trajectory(*parent_steps, subagents=[review, implement])


def test_totals_reconcile_with_harbor_agent_results(tmp_path: Path) -> None:
    steps = [
        TracedStep(
            "checkpoint_1",
            trace=trajectory(agent_step(1, codex_exec("a", "ls")), agent_step(2)),
            usage=usage(1000, 50, 0.01),
            execution=execution("00:00.000000", "01:30.500000"),
        ),
        TracedStep(
            "checkpoint_2",
            # Reviewer sessions of a review loop are the arm's own spend.
            trace=_review_loop_trace(),
            usage=usage(84049, 511, 0.00216312, cache=72192),
            execution=execution("02:00.000000", "02:35.266879"),
        ),
    ]
    observed = _by_key(_assess(traced_memory_context(tmp_path, steps)))

    assert observed["input_tokens", "checkpoint_2"].value == 84049
    assert observed["output_tokens", "checkpoint_2"].value == 511
    assert observed["cost_usd", "checkpoint_2"].value == pytest.approx(0.00216312)
    assert observed["turns", "checkpoint_1"].value == 2
    assert observed["turns", "checkpoint_2"].value == 5
    assert observed["agent_wall_s", "checkpoint_1"].value == pytest.approx(90.5)

    agent_results = [step.usage for step in steps]
    assert observed["total_tokens", "trajectory"].value == sum(
        item["n_input_tokens"] + item["n_output_tokens"] for item in agent_results
    )
    assert observed["total_cost_usd", "trajectory"].value == pytest.approx(
        sum(item["cost_usd"] for item in agent_results)
    )
    assert observed["total_turns", "trajectory"].value == 7
    assert observed["total_agent_wall_s", "trajectory"].value == pytest.approx(90.5 + 35.266879)


def test_unreported_usage_stays_unavailable_and_unreached_steps_are_not_counted(
    tmp_path: Path,
) -> None:
    oracle_agent = {"n_input_tokens": None, "n_output_tokens": None, "cost_usd": None}
    steps = [
        TracedStep("checkpoint_1", trace=trajectory(agent_step(1)), usage=usage(10, 5, 0.5)),
        TracedStep("checkpoint_2", trace=None, usage=oracle_agent),
    ]
    ctx = traced_memory_context(tmp_path, steps, planned=["checkpoint_3"])
    observed = _by_key(_assess(ctx))

    for metric in ("input_tokens", "output_tokens", "cost_usd", "turns", "agent_wall_s"):
        missing = observed[metric, "checkpoint_2"]
        assert (missing.status, missing.value) == (AssessStatus.UNAVAILABLE, None)
        skipped = observed[metric, "checkpoint_3"]
        assert skipped.status is AssessStatus.NOT_APPLICABLE
    for metric in ("total_tokens", "total_cost_usd", "total_turns", "total_agent_wall_s"):
        total = observed[metric, "trajectory"]
        assert (total.status, total.value) == (AssessStatus.UNAVAILABLE, None)
    assert observed["input_tokens", "checkpoint_1"].value == 10


def test_turn_evidence_points_into_the_stored_trace(tmp_path: Path) -> None:
    ctx = traced_memory_context(
        tmp_path,
        [TracedStep("checkpoint_1", trace=trajectory(agent_step(1)), usage=usage(1, 1, 0.0))],
    )
    result = _assess(ctx)
    (evidence,) = result.evidence
    assert (evidence.metric, evidence.source, evidence.path) == (
        "turns",
        EvidenceSource.TRACE,
        "trajectory.json",
    )


def test_unreadable_trajectories_are_unavailable_not_zero(tmp_path: Path) -> None:
    ctx = traced_memory_context(
        tmp_path, [TracedStep("checkpoint_1", trace={"no": "steps"}, usage=usage(1, 1, 0.0))]
    )
    turns = _by_key(_assess(ctx))["turns", "checkpoint_1"]
    assert turns.status is AssessStatus.UNAVAILABLE
    assert "not an ATIF trajectory" in (turns.reason or "")

    trace_dir = tmp_path / "bad"
    trace_dir.mkdir()
    (trace_dir / "trajectory.json").write_text("{not json")
    with pytest.raises(TrajectoryError, match="not valid JSON"):
        load_trajectory(trace_dir)
    # Harbor owns the format: anything its ATIF model rejects is unreadable.
    renumbered = trajectory(agent_step(1))
    renumbered["steps"][1]["step_id"] = 5
    for invalid in ({"steps": [1]}, {**trajectory(), "subagent_trajectories": 3}, renumbered):
        (trace_dir / "trajectory.json").write_text(json.dumps(invalid))
        with pytest.raises(TrajectoryError, match="not an ATIF trajectory"):
            load_trajectory(trace_dir)


def test_turns_cover_sub_agents_at_any_depth_but_not_deterministic_dispatch(
    tmp_path: Path,
) -> None:
    grandchild = session("grandchild", agent_step(1), agent_step(2), agent_step(3))
    child = session(
        "child", agent_step(1), delegate("grandchild", source="agent"), subagents=[grandchild]
    )
    dispatch = {**agent_step(1), "llm_call_count": 0}
    trace = trajectory(dispatch, delegate("child"), subagents=[child])
    ctx = traced_memory_context(
        tmp_path, [TracedStep("checkpoint_1", trace=trace, usage=usage(1, 1, 0))]
    )
    loaded = load_trajectory(ctx.steps[0].trace_dir)
    assert loaded is not None
    assert [step.session for step in loaded.steps if step.source == "agent"] == [
        "main",
        "child",
        "child",
        "grandchild",
        "grandchild",
        "grandchild",
    ]
    assert _by_key(_assess(ctx))["turns", "checkpoint_1"].value == 5


def test_a_trial_with_no_usage_at_all_is_unavailable(tmp_path: Path) -> None:
    ctx = traced_memory_context(tmp_path, [TracedStep("checkpoint_1", trace=None, usage=None)])
    result = _assess(ctx)
    assert result.status is AssessStatus.UNAVAILABLE


@pytest.mark.parametrize(
    ("timing", "seconds"),
    [
        (execution("00:00", "00:12.5"), 12.5),
        # A zone-less start is read as UTC so both ends compare.
        ({"started_at": "2026-09-30T00:00:00", "finished_at": "2026-09-30T00:00:02Z"}, 2.0),
        ({"started_at": "yesterday", "finished_at": "2026-09-30T00:00:02Z"}, None),
        ({"started_at": "2026-09-30T00:00:02Z", "finished_at": "2026-09-30T00:00:01Z"}, None),
        ({"started_at": "2026-09-30T00:00:02Z"}, None),
        ("bad", None),
        (None, None),
    ],
)
def test_agent_wall_time_is_the_agent_execution_phase_or_unknown(
    timing: object, seconds: float | None
) -> None:
    assert agent_execution_seconds(timing) == seconds
