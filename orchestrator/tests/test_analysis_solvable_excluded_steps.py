"""Steps the lock excludes from the solvable set do not distort the solvable outcomes.

A step pinned with an empty solvable set (validation excluded it as
``no_new_scoreable_tests``) has no verdict. The solvable outcomes score the other steps:
the solve rate is over the steps that have a set, the final outcome is the last such step's,
and the raw outcomes are untouched. With every step excluded the outcomes are unavailable,
never a measured zero.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from raidar.design.experiment.lock import seal_lock
from raidar.engine.job.assessment import lock_rescore_config
from raidar.scoring.catalog.builtin import default_scorer_registry
from raidar.scoring.catalog.outcomes import CORRECTNESS_REF
from raidar.scoring.framework.rescore import rescore_path
from raidar.scoring.framework.store import AssessmentStore
from tests.test_analysis_bound_prefix import ALL_STEPS, PROBLEM, _write_job
from tests.test_analysis_command import _analyze, _effect, _lock

pytestmark = pytest.mark.slow


def _pinned(excluded: set[str]) -> dict[str, list[str]]:
    return {step: [] if step in excluded else [f"{step}::case_0"] for step in ALL_STEPS}


def _report(tmp_path: Path, excluded: set[str]) -> dict[str, Any]:
    lock = _lock([CORRECTNESS_REF])
    lock["spec"]["problems"] = [{"ref": PROBLEM, "checkpoints": len(ALL_STEPS)}]
    solvable = {"sha256": "ab" * 32, "steps": _pinned(excluded)}
    lock["execution"]["references"] = {"problem": {PROBLEM: {"metadata": {"solvable": solvable}}}}
    lock = seal_lock(
        {
            key: value
            for key, value in lock.items()
            if key not in {"sections", "lock_sha256", "document_sha256"}
        }
    )
    lock_path = tmp_path / "excluded.lock.json"
    lock_path.write_text(json.dumps(lock))
    job_dir = _write_job(tmp_path, lock, bound=len(ALL_STEPS))
    store = tmp_path / "store"
    rescore_path(
        job_dir / "trials",
        (CORRECTNESS_REF,),
        AssessmentStore(store),
        default_scorer_registry(),
        config=lock_rescore_config(lock).scorers,
    )
    output = tmp_path / "out"
    result = _analyze(job_dir, store, lock_path, output)
    assert result.exit_code == 0, result.output
    return json.loads((output / "analysis.json").read_text())


# The baseline solves every checkpoint; the guided arm fails the second.
def test_an_excluded_middle_step_leaves_the_rate_over_the_scored_steps(tmp_path: Path) -> None:
    report = _report(tmp_path, {"checkpoint_2"})

    rate = _effect(report, "solve_rate_solvable")
    assert rate["baseline_mean"] == pytest.approx(1.0)
    assert rate["treatment_mean"] == pytest.approx(1.0)  # 2 of the 2 scored steps, not 2 of 3
    final = _effect(report, "final_solved_solvable")
    assert final["treatment_mean"] == pytest.approx(1.0)  # checkpoint_3 is the last scored step
    raw = _effect(report, "solve_rate")
    assert raw["baseline_mean"] == pytest.approx(1.0)
    assert raw["treatment_mean"] == pytest.approx(2 / 3)  # the raw outcome ignores exclusions
