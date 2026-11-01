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
from collections.abc import Sequence
from pathlib import Path

from raidar.scoring.framework.context import AssessContext, StepInputs
from raidar.scoring.framework.result import AssessResult
from raidar.scoring.framework.validation import validate_result
from raidar.scoring.verification_effort.manifest import VERIFICATION_EFFORT
from raidar.scoring.verification_effort.scorer import verification_effort_scorer

__all__ = ["VERIFICATION_EFFORT", "gate_result", "write_step_gate"]

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


def write_step_gate(agent_dir: Path, rng: random.Random, position: int) -> None:
    """One step's ``workflow/workflow.json`` under ``agent_dir`` (``position``: 0-based step)."""
    refusals = _REFUSALS[position % len(_REFUSALS)]
    runs = min(refusals, MAX_ROUNDS) + 1
    gates = [_gate_run(rng, run, refused=run <= refusals) for run in range(1, runs + 1)]
    turns = [
        _turn(1, "agent", rng),
        *(_turn(index, "gate_fix", rng) for index in range(2, runs + 1)),
    ]
    record = {
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
    target = agent_dir / RECORD_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=2), encoding="utf-8")


def _step(trial_dir: Path, checkpoint: str) -> StepInputs:
    agent_dir = trial_dir / "steps" / checkpoint / "agent"
    return StepInputs(
        step=checkpoint,
        reached=True,
        checkpoint=checkpoint,
        snapshot=None,
        snapshot_digest=None,
        record=None,
        record_digest=None,
        trace_dir=agent_dir if agent_dir.is_dir() else None,
        trace_digest=None,
        agent_result=None,
    )


def gate_result(
    trial_dir: Path, checkpoints: Sequence[str], *, gated: bool, seed: str, problem: str
) -> AssessResult:
    """Write the trial's gate records (gated arm only) and assess them with verification-effort."""
    if gated:
        rng = random.Random(seed)
        for position, checkpoint in enumerate(checkpoints):
            agent_dir = trial_dir / "steps" / checkpoint / "agent"
            agent_dir.mkdir(parents=True, exist_ok=True)
            write_step_gate(agent_dir, rng, position)
    ctx = AssessContext(
        problem=problem, steps=tuple(_step(trial_dir, checkpoint) for checkpoint in checkpoints)
    )
    return validate_result(VERIFICATION_EFFORT, ctx, verification_effort_scorer().assess(ctx))
