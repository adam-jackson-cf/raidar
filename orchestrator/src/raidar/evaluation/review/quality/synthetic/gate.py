"""Synthetic commit-gate records for the Quality fixture's gated arm (labelled synthetic).

The gated arm's trials get, per checkpoint, the files ``guardrail-gate@1``
leaves in a step's agent log dir: the gate record, each gate run's output in
``guardrail-check``'s printed format and its JSON report. The real
``gate-effort`` scorer then assesses every trial of the fixture (an ungated
trial is ``not_applicable``), so the Quality view reads them exactly as it
reads a real run. Each trial draws from its own seeded generator, so the rest
of the fixture's values are unchanged.
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from pathlib import Path

from raidar.scoring.framework.context import AssessContext, StepInputs
from raidar.scoring.framework.result import AssessResult
from raidar.scoring.framework.validation import validate_result
from raidar.scoring.gate_effort import GATE_EFFORT, gate_effort_scorer
from raidar.scoring.gate_effort.records import RECORD_PATH, REPORT_SCHEMA

__all__ = ["GATE_EFFORT", "gate_result", "write_step_gate"]

MAX_ROUNDS = 3
# Refused runs before the gate passes; MAX_ROUNDS + 1 means the step ends unresolved.
_REFUSALS = (0, 0, 1, 1, 2, 4)
_CODES = (
    ("C901", "`handle` is too complex (12 > 10)"),
    ("PLR0912", "Too many branches (14 > 12)"),
    ("MYPY.no-untyped-def", "Function is missing a type annotation"),
    ("MYPY.arg-type", 'Argument 1 has incompatible type "str"; expected "int"'),
    ("F841", "Local variable `total` is assigned to but never used"),
    ("PLR0904", "Too many public methods (22 > 20)"),
    ("I001", "Import block is un-sorted or un-formatted"),
    ("N802", "Function name `Total` should be lowercase"),
    ("TST001", "subprocess.run in a test needs timeout="),
    ("SUPPRESS", "lint/type suppression comments are not allowed"),
)


def _findings(rng: random.Random, count: int) -> list[tuple[str, str, int]]:
    return [(*rng.choice(_CODES), 10 + 7 * index) for index in range(count)]


def _output(findings: Sequence[tuple[str, str, int]]) -> str:
    if not findings:
        return "guardrail-check: PASSED (0 blocking, 0 warnings, 3 Python files)\n"
    lines = [
        f"guardrail-check: REFUSED ({len(findings)} blocking, 0 warnings, 3 Python files)",
        f"Blocking (fix these; the change is refused until they pass) ({len(findings)}):",
        *(f"  ledger/core.py:{line}:1: {code} {message}" for code, message, line in findings),
        "Re-run `guardrail-check` to confirm.",
    ]
    return "\n".join(lines) + "\n"


def _stage(index: int, role: str, rng: random.Random) -> dict[str, object]:
    return {
        "index": index,
        "stage": role,
        "round": index - 1,
        "error": None,
        "n_input_tokens": rng.randint(20_000, 60_000),
        "n_output_tokens": rng.randint(800, 3_000),
        "cost_usd": round(rng.uniform(0.004, 0.02), 6),
        "duration_sec": round(rng.uniform(20.0, 120.0), 3),
    }


def write_step_gate(agent_dir: Path, rng: random.Random) -> None:
    """One step's gate record, run logs and reports under ``agent_dir``."""
    gate_dir = agent_dir / "guardrail-gate"
    refusals = rng.choice(_REFUSALS)
    runs = min(refusals, MAX_ROUNDS) + 1
    clean = refusals <= MAX_ROUNDS
    count = rng.randint(4, 14)
    records = []
    for run in range(1, runs + 1):
        refused = run <= refusals
        findings = _findings(rng, max(1, count // run)) if refused else []
        report_dir = gate_dir / f"gate_run_{run}"
        report_dir.mkdir(parents=True, exist_ok=True)
        (gate_dir / f"gate_run_{run}.log").write_text(_output(findings), encoding="utf-8")
        report = {
            "schema": REPORT_SCHEMA,
            "verdict": "refused" if refused else "passed",
            "findings": [{"code": code, "tier": "block"} for code, _, _ in findings],
        }
        (report_dir / "gate-1.json").write_text(json.dumps(report), encoding="utf-8")
        records.append(
            {
                "run": run,
                "after": "stage_1_work" if run == 1 else f"stage_{run}_fix",
                "passed": not refused,
                "error": None,
                "timed_out": False,
                "log": f"guardrail-gate/gate_run_{run}.log",
                "report_dir": f"guardrail-gate/gate_run_{run}",
                "duration_sec": round(rng.uniform(0.2, 0.9), 3),
            }
        )
    stages = [_stage(index, "work" if index == 1 else "fix", rng) for index in range(1, runs + 1)]
    record = {
        "termination_reason": "gate_clean" if clean else "rounds_exhausted",
        "error": None,
        "rounds_configured": MAX_ROUNDS,
        "gate_runs": records,
        "stages": stages,
        "gate_clean_at_step_end": clean,
    }
    (agent_dir / RECORD_PATH).write_text(json.dumps(record, indent=2), encoding="utf-8")


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
    """Write the trial's gate records (gated arm only) and assess them with gate-effort."""
    if gated:
        rng = random.Random(seed)
        for checkpoint in checkpoints:
            agent_dir = trial_dir / "steps" / checkpoint / "agent"
            agent_dir.mkdir(parents=True, exist_ok=True)
            write_step_gate(agent_dir, rng)
    ctx = AssessContext(
        problem=problem, steps=tuple(_step(trial_dir, checkpoint) for checkpoint in checkpoints)
    )
    return validate_result(GATE_EFFORT, ctx, gate_effort_scorer().assess(ctx))
