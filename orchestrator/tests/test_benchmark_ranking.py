"""Kinds in the analysis report: a benchmark's ranking view and a smoke report's stamp."""

from __future__ import annotations

from dataclasses import replace
from math import comb

import pytest

from raidar.evaluation.analysis.effects import Settings
from raidar.evaluation.analysis.markdown import render_markdown
from raidar.evaluation.analysis.ranking import pass_at_k
from raidar.evaluation.analysis.report import PlannedTrial, analyse
from tests.analysis_support import inputs, trial

AGENTS = ("codex", "claude", "gemini")
SOLVED = {  # attempt -> final_solved per agent; codex solves most, gemini least
    "codex": (1.0, 1.0, 0.0, 1.0),
    "claude": (1.0, 0.0, 0.0, 1.0),
    "gemini": (0.0, 0.0, 0.0, 1.0),
}
RATE = {"codex": 0.8, "claude": 0.6, "gemini": 0.3}


def _benchmark(attempts: int = 4, *, rank_by: str | None = None, infra: bool = False):
    outcomes = [
        trial(
            agent,
            attempt,
            {
                "solve_rate": RATE[agent] + 0.01 * attempt,
                "final_solved": SOLVED[agent][attempt],
                "total_cost_usd": {"codex": 0.1, "claude": 0.2, "gemini": 0.05}[agent],
                "total_tokens": 1000.0,
            },
            infra=infra and agent == "gemini" and attempt == 0,
        )
        for agent in AGENTS
        for attempt in range(attempts)
    ]
    planned = tuple(
        PlannedTrial(trial_id=f"toy@1-{a}-b{i}", arm=a, attempt=i, problem="toy@1")
        for i in range(attempts)
        for a in AGENTS
    )
    base = inputs(outcomes, attempts)
    return replace(
        base,
        kind="benchmark",
        baseline_arm="codex",
        planned=planned,
        rank_by=rank_by,
        attempts=attempts,
    )


def test_pass_at_k_is_the_unbiased_estimator() -> None:
    assert pass_at_k(4, 3, 1) == pytest.approx(0.75)
    assert pass_at_k(4, 2, 2) == pytest.approx(1 - comb(2, 2) / comb(4, 2))
    assert pass_at_k(4, 0, 3) == 0.0
    assert pass_at_k(4, 4, 4) == 1.0
    assert pass_at_k(2, 1, 3) is None  # fewer samples than k: no estimate, never zero


def test_a_benchmark_ranks_agents_with_intervals_pass_at_k_and_cost() -> None:
    report = analyse(_benchmark(), Settings(seed=3))

    ranking = report.ranking
    assert ranking is not None
    assert (ranking.rank_by, ranking.reference_arm) == ("solve_rate", "codex")
    assert [row.arm for row in ranking.rows] == ["codex", "claude", "gemini"]
    assert [row.rank for row in ranking.rows] == [1, 2, 3]
    codex = ranking.rows[0]
    assert codex.n == 4
    assert codex.mean == pytest.approx(0.8 + 0.01 * 1.5)
    assert codex.interval is not None
    assert codex.interval.low < codex.mean < codex.interval.high
    assert [entry.k for entry in codex.pass_at_k] == [1, 2, 3, 4]
    assert codex.pass_at_k[0].value == pytest.approx(0.75)
    assert codex.pass_at_k[1].value == pytest.approx(1 - comb(1, 2) / comb(4, 2))
    assert codex.pass_at_k[3].value == 1.0
    assert ranking.rows[2].pass_at_k[0].value == pytest.approx(0.25)
    assert codex.cost_means["total_cost_usd"] == pytest.approx(0.1)
    # The paired verdicts against the reference agent come from the same engine.
    assert [c.treatment for c in report.comparisons] == ["claude", "gemini"]
    assert all(c.baseline == "codex" for c in report.comparisons)
    assert report.citable
    assert report.experiment_kind == "benchmark"


def test_rank_by_orders_by_the_declared_metric_and_invalid_trials_never_count() -> None:
    report = analyse(_benchmark(rank_by="final_solved", infra=True), Settings(seed=3))

    ranking = report.ranking
    assert ranking is not None
    assert ranking.rank_by == "final_solved"
    gemini = next(row for row in ranking.rows if row.arm == "gemini")
    assert gemini.n == 3  # the infra-excluded attempt is not a sample
    assert gemini.pass_at_k[3].value is None
    assert gemini.pass_at_k[3].problems == 0
    assert [row.arm for row in ranking.rows] == ["codex", "claude", "gemini"]


def test_the_markdown_shows_the_ranking_view() -> None:
    text = render_markdown(analyse(_benchmark(), Settings(seed=3)))

    assert "## Ranking (benchmark)" in text
    assert "| 1 | codex (reference) | 4 |" in text
    assert "pass@4" in text
    assert "- Kind: `benchmark`" in text


def test_ab_tests_have_no_ranking_and_smoke_reports_are_not_citable() -> None:
    base = _benchmark()
    ab_test = analyse(replace(base, kind="ab-test"), Settings(seed=3))
    smoke_inputs = replace(
        base,
        kind="smoke",
        planned=tuple(t for t in base.planned if t.arm == "codex"),
        outcomes=tuple(o for o in base.outcomes if o.arm == "codex"),
    )
    smoke = analyse(smoke_inputs, Settings(seed=3))

    assert ab_test.ranking is None
    assert ab_test.citable
    assert smoke.ranking is None
    assert smoke.comparisons == ()
    assert not smoke.citable
    assert "> **Not citable:** a smoke experiment" in render_markdown(smoke)
    with pytest.raises(ValueError, match="no treatment arm"):
        analyse(replace(smoke_inputs, kind="ab-test"), Settings(seed=3))
