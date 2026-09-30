"""Synthetic experiment-epoch fixture for the review surface (``make review-fixture-synthetic``).

Writes two clearly labelled synthetic experiments as a real ``raidar run``
would leave them, plus an Assessment store, under ``<dest>/runs`` and
``<dest>/store``:

- ``synthetic-bugfix``: one 3-checkpoint problem, one condition, three agent
  specs (reasoning effort is the factor). The low-effort spec has a degraded
  trajectory (checkpoint 3 fails) and one whose harness exited before
  verification (no headline score), so ranking, Δ-vs-best, unscored and
  failure paths all render.
- ``synthetic-skill``: one 2-checkpoint problem, conditions ``baseline`` and
  ``skills`` crossed with two agent specs on two harnesses, so condition diffs
  and per-condition comparisons render (each arm's condition digest covers its
  harness, as ``CompiledCondition.digest`` does, yet both arms share a revision).
- ``synthetic-policy-mix``: a one-shot problem scored under
  ``scb-canonical-oneshot@1``, compared with a scenario-epoch experiment on the
  same problem (``fixture_legacy``), so the headline-policy mismatch renders.

``<dest>/experiments`` holds scenario-epoch experiments (``fixture_legacy``),
so the old epoch renders beside the new one.

Review ids are ``<run id>-<trial id>``; the trial ids are chosen so the frozen
review-surface suite's deterministic ids (``synthetic-bugfix-gpt-5.5-low-04``
and friends) name the same kinds of trajectories as in the scenario epoch.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from raidar.review_projection.fixture_assessments import (
    LintFinding,
    QualityPlan,
    store_assessments,
)
from raidar.review_projection.fixture_legacy import write_scenario_epoch
from raidar.review_projection.fixture_trials import (
    CheckpointScript,
    Outcome,
    TrialPlan,
    TrialScript,
    write_bundle,
    write_trial,
)
from raidar.scorers.catalog import default_scorer_registry
from raidar.scorers.rescore_config import RescoreConfig
from raidar.scorers.store import AssessmentStore
from raidar.spec.canonical import digest_json
from raidar.spec.policies import DEFAULT_POLICY_REGISTRY
from raidar.spec.schema import ExperimentSpec

SYNTHETIC_MARKER = "synthetic"
POLICY_MIX_PROBLEM = "synthetic/policy-mix@1"
_STARTED = datetime(2026, 1, 1, tzinfo=UTC)
_SCORERS = ["correctness@1", "code-quality@1", "resource-usage@1"]
_LEDGER = (
    "import os\n\n\n"
    "def balance(entries):\n"
    "    total = 0\n"
    "    for kind, amount in entries:\n"
    "        total += amount if kind == 'credit' else -amount\n"
    "    return total\n"
)
_BUGFIX_PROMPT = (
    "Fix the ledger balance defect: debit entries are still added to the balance. "
    "The legacy JavaScript ledger was checked with `bun run test` and `bun run lint`; "
    "this Python port is checked with pytest and ruff."
)
_SKILL_PROMPT = "Build sum_even for the ledger utilities with minimal churn and tests first."
_SKILL_DIR = "skills/tdd"
_SKILL_FILE = f"{_SKILL_DIR}/SKILL.md"
_SKILL_TEXT = "# tdd\nWrite the failing test first, then the smallest change that passes it.\n"
_AGENTS = {
    "gpt55-low": ("codex-cli", "openai", "gpt-5.5", "low", "gpt-5.5-low"),
    "gpt55-medium": ("codex-cli", "openai", "gpt-5.5", "medium", "gpt-5.5-medium"),
    "gpt55-high": ("codex-cli", "openai", "gpt-5.5", "high", "gpt-5.5-high"),
    "sonnet": ("claude-code", "anthropic", "claude-sonnet-4.6", None, "claude-sonnet-4.6"),
}


@dataclass(frozen=True)
class PlannedTrajectory:
    """One planned synthetic trajectory: arm, block, checkpoint outcomes and code quality."""

    arm: str
    block: int
    outcomes: tuple[tuple[Outcome, int, int], ...]
    quality: float
    lint: bool = False
    failure: str | None = None


@dataclass(frozen=True)
class Design:
    experiment: str
    problem: str
    title: str
    prompt: str
    factors: list[str]
    conditions: dict[str, dict[str, str]]
    component_paths: dict[str, list[str]]
    arms: dict[str, tuple[str, str]]
    baseline_arm: str
    blocks: int
    trajectories: tuple[PlannedTrajectory, ...]

    def locked_conditions(self) -> dict[str, list[dict[str, object]]]:
        """The lock's hashed conditions: ``skills@1`` with the hash of each skill file."""
        return {
            condition: (
                [
                    {
                        "use": "skills@1",
                        "use_digest": digest_json("skills@1"),
                        "with": {},
                        "files": {path: digest_json(text) for path, text in files.items()},
                    }
                ]
                if files
                else []
            )
            for condition, files in self.conditions.items()
        }


def _solved(checkpoints: int) -> tuple[tuple[Outcome, int, int], ...]:
    return tuple(("solved", 6, 6) for _ in range(checkpoints))


def _bugfix() -> Design:
    harness_exit = "the harness exited before verification finished (exit code 137)"
    low = [
        PlannedTrajectory("gpt55-low", block, _solved(3), 0.9 + 0.01 * block) for block in (1, 2, 3)
    ]
    low += [
        PlannedTrajectory(
            "gpt55-low",
            4,
            (("solved", 6, 6), ("solved", 6, 6), ("failed", 2, 6)),
            0.8,
            lint=True,
        ),
        PlannedTrajectory("gpt55-low", 5, (("infra", 0, 0),) * 3, 0.0, failure=harness_exit),
    ]
    return Design(
        experiment="synthetic-bugfix",
        problem="synthetic/ledger-balance@1",
        title="Synthetic ledger balance defect repair across three checkpoints",
        prompt=_BUGFIX_PROMPT,
        factors=["agent.reasoning_effort"],
        conditions={"baseline": {}},
        component_paths={"baseline": []},
        arms={arm: ("baseline", arm) for arm in ("gpt55-medium", "gpt55-high", "gpt55-low")},
        baseline_arm="gpt55-medium",
        blocks=5,
        trajectories=(
            *(
                PlannedTrajectory("gpt55-medium", b, _solved(3), 0.92 + 0.005 * b)
                for b in range(1, 6)
            ),
            *(PlannedTrajectory("gpt55-high", b, _solved(3), 0.8 + 0.004 * b) for b in range(1, 6)),
            *low,
        ),
    )


def _skill() -> Design:
    quality = {
        "baseline-gpt": 0.74,
        "baseline-sonnet": 0.72,
        "skills-gpt": 0.86,
        "skills-sonnet": 0.9,
    }
    trajectories = [
        PlannedTrajectory(
            arm,
            block,
            (("solved", 4, 4), ("failed", 3, 4))
            if arm.startswith("baseline") and block == 2
            else _solved(2),
            value + 0.01 * (block - 2),
        )
        for arm, value in quality.items()
        for block in (1, 2, 3)
    ]
    return Design(
        experiment="synthetic-skill",
        problem="synthetic/skill-coding@1",
        title="Synthetic sum_even feature built across two checkpoints",
        prompt=_SKILL_PROMPT,
        factors=["condition", "agent"],
        conditions={"baseline": {}, "skills": {_SKILL_FILE: _SKILL_TEXT}},
        component_paths={"baseline": [], "skills": [_SKILL_DIR]},
        arms={
            "baseline-gpt": ("baseline", "gpt55-low"),
            "baseline-sonnet": ("baseline", "sonnet"),
            "skills-gpt": ("skills", "gpt55-low"),
            "skills-sonnet": ("skills", "sonnet"),
        },
        baseline_arm="baseline-gpt",
        blocks=3,
        trajectories=tuple(trajectories),
    )


def _policy_mix() -> Design:
    return Design(
        experiment="synthetic-policy-mix",
        problem=POLICY_MIX_PROBLEM,
        title="Synthetic one-shot ledger check, compared across headline policies",
        prompt=_BUGFIX_PROMPT,
        factors=["agent.reasoning_effort"],
        conditions={"baseline": {}},
        component_paths={"baseline": []},
        arms={"gpt55-low": ("baseline", "gpt55-low")},
        baseline_arm="gpt55-low",
        blocks=3,
        trajectories=tuple(
            PlannedTrajectory("gpt55-low", block, _solved(1), 0.84 + 0.02 * block)
            for block in (1, 2, 3)
        ),
    )


def _spec(design: Design) -> ExperimentSpec:
    agents = sorted({agent for _, agent in design.arms.values()})
    return ExperimentSpec.model_validate(
        {
            "schema_version": 1,
            "id": design.experiment,
            "description": f"{design.title} (synthetic review-surface fixture)",
            "problems": [{"ref": design.problem}],
            "agents": [
                {
                    "id": agent,
                    "harness": _AGENTS[agent][0],
                    "provider": _AGENTS[agent][1],
                    "model": _AGENTS[agent][2],
                    "reasoning_effort": _AGENTS[agent][3],
                    "version": "0.0.0",
                }
                for agent in agents
            ],
            "factors": design.factors,
            "conditions": [
                {
                    "id": condition,
                    "components": [{"use": "skills@1", "files": paths}] if paths else [],
                }
                for condition, paths in design.component_paths.items()
            ],
            "arms": [
                {"id": arm, "condition": condition, "agent": agent}
                for arm, (condition, agent) in design.arms.items()
            ],
            "assessment": {"scorers": _SCORERS},
            "analysis": {"baseline_arm": design.baseline_arm, "blocks": design.blocks, "seed": 0},
        }
    )


def _lock(design: Design, spec: ExperimentSpec) -> dict[str, object]:
    checkpoints = len(design.trajectories[0].outcomes)
    body: dict[str, object] = {
        "spec": spec.to_json(),
        "conditions": design.locked_conditions(),
        "headline_policy": {
            "ref": DEFAULT_POLICY_REGISTRY.default_for_checkpoints(checkpoints).ref,
            "selection": "default_by_k",
            "checkpoints": [checkpoints],
        },
        "problems": [
            {
                "label": design.problem,
                "checkpoints": checkpoints,
                "digest": digest_json(design.problem),
            }
        ],
    }
    return {**body, "lock_sha256": digest_json(body)}


def _checkpoint(step: str, outcome: tuple[Outcome, int, int], index: int) -> CheckpointScript:
    state, passed, total = outcome
    code = 0 if state == "solved" else 1
    return CheckpointScript(
        step=step,
        outcome=state,
        passed=passed,
        total=total,
        source=_LEDGER + f"\n\ndef checkpoint_{index + 1}():\n    return {index + 1}\n",
        commands=(("cat ledger.py", 0), ("uv run pytest -q", code), ("uv run ruff check .", 0)),
        message=(
            "The ledger checks pass for this checkpoint."
            if state == "solved"
            else "Some ledger cases still fail; stopping here."
        ),
    )


def _script(design: Design, trajectory: PlannedTrajectory, index: int) -> TrialScript:
    condition, agent = design.arms[trajectory.arm]
    label = _AGENTS[agent][4]
    trial_id = (
        f"{label}-{trajectory.block:02d}"
        if len(design.conditions) == 1
        else f"{trajectory.arm}-{trajectory.block:02d}"
    )
    return TrialScript(
        trial_id=trial_id,
        arm=trajectory.arm,
        block=trajectory.block,
        model=f"{_AGENTS[agent][1]}/{_AGENTS[agent][2]}",
        started=_STARTED + timedelta(minutes=10 * index),
        prompt=design.prompt,
        checkpoints=tuple(
            _checkpoint(f"checkpoint_{k + 1}", outcome, k)
            for k, outcome in enumerate(trajectory.outcomes)
        ),
        overlay=tuple(design.conditions[condition].items()),
        failure=trajectory.failure,
    )


def _quality(trajectory: PlannedTrajectory, steps: int) -> QualityPlan:
    value = round(trajectory.quality, 6)
    names = ("verbosity", "erosion", "architecture", "rework", "regression")
    lint = (
        [
            LintFinding(f"checkpoint_{k + 1}", 1, "F401 `os` imported but unused")
            for k in range(steps)
        ]
        if trajectory.lint
        else []
    )
    return QualityPlan(values=dict.fromkeys(names, value), lint=lint)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _run(design: Design, dest: Path, store: AssessmentStore) -> Path:
    spec = _spec(design)
    lock = _lock(design, spec)
    run_dir = dest / "runs" / design.experiment / design.experiment
    bundle = write_bundle(
        dest / "bundles" / design.experiment,
        design.problem,
        [f"checkpoint_{k + 1}" for k in range(len(design.trajectories[0].outcomes))],
    )
    _write_json(run_dir / "experiment.lock.json", lock)
    settings = RescoreConfig()
    (run_dir / "assessment-config.yaml").write_text(
        yaml.safe_dump(settings.model_dump(mode="json"), sort_keys=True)
    )
    registry = default_scorer_registry()
    conditions = design.locked_conditions()
    for index, trajectory in enumerate(design.trajectories):
        condition, agent = design.arms[trajectory.arm]
        plan = TrialPlan(
            experiment=design.experiment,
            run_id=design.experiment,
            run_dir=run_dir,
            problem=design.problem,
            problem_title=design.title,
            bundle=bundle,
            lock_sha256=str(lock["lock_sha256"]),
            # Like CompiledCondition.digest: the contents compiled for this arm's harness.
            condition_digest=digest_json(
                {"harness": _AGENTS[agent][0], "condition": conditions[condition]}
            ),
            agent_digest=digest_json(_AGENTS[agent]),
        )
        script = _script(design, trajectory, index)
        write_trial(plan, script)
        store_assessments(
            store,
            registry,
            run_dir / "trials" / script.trial_id,
            settings,
            _quality(trajectory, len(trajectory.outcomes)),
        )
    _write_json(run_dir / "plan.json", {"kind": "raidar-plan", "options": {"agent_override": None}})
    _write_json(
        run_dir / "run.json",
        {
            "kind": "raidar-run",
            "experiment": design.experiment,
            "run_id": design.experiment,
            "lock_sha256": lock["lock_sha256"],
            "experiment_file": None,
            "started_at": _STARTED.isoformat(),
            SYNTHETIC_MARKER: True,
        },
    )
    return run_dir


def generate_review_fixture(dest: Path) -> list[Path]:
    """Write the synthetic runs, their Assessment store and scenario-epoch experiments.

    Returns the run directories.
    """
    shutil.rmtree(dest, ignore_errors=True)
    store = AssessmentStore(dest / "store")
    runs = [_run(design, dest, store) for design in (_bugfix(), _skill(), _policy_mix())]
    write_scenario_epoch(dest / "experiments", policy_mix_scenario=POLICY_MIX_PROBLEM)
    return runs


def main() -> None:
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../.tmp/review-fixture")
    for run_dir in generate_review_fixture(dest.resolve()):
        print(f"Wrote synthetic run: {run_dir}")


if __name__ == "__main__":
    main()
