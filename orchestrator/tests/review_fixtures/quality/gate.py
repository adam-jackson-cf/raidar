"""Synthetic gate/1 records for the Quality fixture's gated arm (labelled synthetic).

The gated arm's trials get, per checkpoint, the ``workflow/workflow.json`` the
host leaves in a step's agent log dir: a ``gate/1`` record (gate runs with their
checks) and the step's turns (fix turns are ``gate_fix``). The real
``verification-effort`` scorer then assesses every trial of the fixture (a trial
without a gate/1 record is ``not_applicable``), so the Quality view reads them
exactly as it reads a real run. Each trial draws from its own seeded generator,
so the rest of the fixture's values are unchanged.
"""

from __future__ import annotations

import json
import random
from collections.abc import Mapping, Sequence
from pathlib import Path

from raidar.scoring.framework.context import AssessContext, StepInputs
from raidar.scoring.framework.result import TRAJECTORY_SUBJECT, AssessResult, AssessStatus
from raidar.scoring.framework.validation import validate_result
from raidar.scoring.verification_effort.assess import NO_GATE
from raidar.scoring.verification_effort.grades import Grades
from raidar.scoring.verification_effort.manifest import VERIFICATION_EFFORT
from raidar.scoring.verification_effort.records import GATE_KEY, StepGate, _gate_record
from raidar.scoring.verification_effort.scorer import verification_effort_scorer
from raidar.scoring.verification_effort.steps import Step, step_values
from raidar.scoring.verification_effort.totals import trajectory_values

__all__ = [
    "VERIFICATION_EFFORT",
    "gate_records",
    "gate_result",
    "memory_gate_result",
    "parse_gate",
    "write_step_gate",
]

RECORD_PATH = Path("workflow") / "workflow.json"
MAX_ROUNDS = 3
# Refused runs before the gate passes per checkpoint; MAX_ROUNDS + 1 leaves the step
# unresolved. Fixed per position so the fixture's fourth checkpoint is always unresolved.
_REFUSALS = (0, 1, 2, MAX_ROUNDS + 1, 0)
_CHECKS = (
    ("python.ruff", "lint"),
    ("python.mypy", "types"),
    ("python.complexity", "complexity"),
    ("python.pytest", "tests"),
)


def _check(rng: random.Random, name: str, kind: str, *, failed: bool) -> dict[str, object]:
    return {
        "name": name,
        "category": kind,
        "blocking": True,
        "status": "failed" if failed else "passed",
        "exit": 1 if failed else 0,
        "seconds": round(rng.uniform(0.1, 0.6), 3),
        "cause": "exit 1" if failed else None,
    }


def _gate_run(rng: random.Random, run: int, *, refused: bool) -> dict[str, object]:
    failing = {name for name, _ in rng.sample(_CHECKS, rng.randint(1, 3))} if refused else set()
    return {
        "who": "seal",
        "run": run,
        "after": "stage_1_work" if run == 1 else f"stage_{run}_fix",
        "verdict": "refused" if refused else "passed",
        "exit": 1 if refused else 0,
        "seconds": round(rng.uniform(0.8, 3.0), 3),
        "error": None,
        "record": f"gate/gate-{run}.json",
        "attribution": "refused" if refused else "passed",
        "attribution_reason": "blocking checks failed" if refused else "all checks passed",
        "changed_during_gate": [],
        "checks": [_check(rng, name, kind, failed=name in failing) for name, kind in _CHECKS],
    }


def _turn(index: int, kind: str, rng: random.Random) -> dict[str, object]:
    return {
        "stage": "work" if kind == "agent" else "fix",
        "kind": kind,
        "turn": index,
        "log_dir": f"turns/{index}",
        "seconds": round(rng.uniform(20.0, 120.0), 3),
        "tokens": {
            "input": rng.randint(20_000, 60_000),
            "cached": rng.randint(0, 10_000),
            "output": rng.randint(800, 3_000),
            "cost_usd": round(rng.uniform(0.004, 0.02), 6),
        },
    }


def step_gate_record(rng: random.Random, position: int) -> dict[str, object]:
    """One step's ``workflow/workflow.json`` content (``position``: 0-based step)."""
    refusals = _REFUSALS[position % len(_REFUSALS)]
    runs = min(refusals, MAX_ROUNDS) + 1
    gates = [_gate_run(rng, run, refused=run <= refusals) for run in range(1, runs + 1)]
    turns = [
        _turn(1, "agent", rng),
        *(_turn(index, "gate_fix", rng) for index in range(2, runs + 1)),
    ]
    return {
        "gate/1": {
            "max_fix_rounds": MAX_ROUNDS,
            "base": {},
            "canary": {},
            "setup_check": {},
            "integrity": [],
            "reaps": [],
            "hook_outputs": [],
            "errors": [],
            "pre_fix_snapshots": [],
            "gates": gates,
        },
        "turns": turns,
    }


def gate_records(checkpoints: Sequence[str], seed: str) -> dict[str, dict[str, object]]:
    """A gated trial's per-checkpoint records, drawn in order from one generator."""
    rng = random.Random(seed)
    return {
        checkpoint: step_gate_record(rng, position)
        for position, checkpoint in enumerate(checkpoints)
    }


def write_step_gate(agent_dir: Path, record: dict[str, object]) -> None:
    """One step's ``workflow/workflow.json`` under ``agent_dir``."""
    target = agent_dir / RECORD_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=2), encoding="utf-8")


def parse_gate(record: dict[str, object]) -> StepGate:
    """The ``StepGate`` a step's record reads as (what ``read_step_gate`` returns)."""
    gate = record[GATE_KEY]
    assert isinstance(gate, dict)
    return _gate_record(record, gate)


def memory_gate_result(
    gates: Mapping[str, StepGate] | None, checkpoints: Sequence[str]
) -> AssessResult:
    """The verification-effort result for per-checkpoint gates, without reading any record.

    Runs the scorer's own per-step and trajectory value rules over ``gates`` (``None``: the
    trial ran without a gate/1 package).
    """
    if gates is None:
        return AssessResult(status=AssessStatus.NOT_APPLICABLE, reason=NO_GATE, observations=())
    steps = [Step(_inputs(checkpoint, None), gates[checkpoint]) for checkpoint in checkpoints]
    found = [(step, step_values(step, frozenset(), Grades(None))) for step in steps]
    totals = trajectory_values([(step.inputs.step, values) for step, values in found])
    observations = [
        measured.observation(metric, step.inputs.step)
        for step, values in found
        for metric, measured in values.items()
    ] + [measured.observation(metric, TRAJECTORY_SUBJECT) for metric, measured in totals.items()]
    return AssessResult(status=AssessStatus.OK, observations=tuple(observations))


def _inputs(checkpoint: str, agent_dir: Path | None) -> StepInputs:
    return StepInputs(
        step=checkpoint,
        reached=True,
        checkpoint=checkpoint,
        snapshot=None,
        snapshot_digest=None,
        record=None,
        record_digest=None,
        trace_dir=agent_dir,
        trace_digest=None,
        agent_result=None,
    )


def _step(trial_dir: Path, checkpoint: str) -> StepInputs:
    agent_dir = trial_dir / "steps" / checkpoint / "agent"
    return _inputs(checkpoint, agent_dir if agent_dir.is_dir() else None)


def gate_result(
    trial_dir: Path, checkpoints: Sequence[str], *, gated: bool, seed: str, problem: str
) -> AssessResult:
    """Write the trial's gate records (gated arm only) and assess them with verification-effort."""
    if gated:
        for checkpoint, record in gate_records(checkpoints, seed).items():
            agent_dir = trial_dir / "steps" / checkpoint / "agent"
            agent_dir.mkdir(parents=True, exist_ok=True)
            write_step_gate(agent_dir, record)
    ctx = AssessContext(
        problem=problem, steps=tuple(_step(trial_dir, checkpoint) for checkpoint in checkpoints)
    )
    return validate_result(VERIFICATION_EFFORT, ctx, verification_effort_scorer().assess(ctx))
