"""Builders for the verification-effort tests: a step's agent log dir as a ``gate/1`` run left it.

``workflow/workflow.json`` with the ``gate/1`` record (seals, retained pre-fix trees) and the
turns of the step, the graded step records, and pre-fix grades in the Assessment store.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from raidar.grading.models import CaseGroup, CaseResult, CaseStatus, VerifierGrade
from raidar.grading.pre_fix.store import GRADE_KIND, SCHEMA_VERSION, grade_path
from raidar.grading.records import (
    BundleRef,
    CoverageRef,
    GraderRef,
    SnapshotRef,
    StepRecord,
    grading_identity,
)
from raidar.scoring.framework.context import AssessContext, StepInputs
from raidar.scoring.framework.result import AssessResult, Observation
from raidar.scoring.framework.validation import validate_result
from raidar.scoring.verification_effort import (
    VERIFICATION_EFFORT,
    verification_effort_scorer,
    verification_effort_scorer_for_store,
)

CP1, CP2, CP3 = "checkpoint_1", "checkpoint_2", "checkpoint_3"
BUNDLE = "b" * 64
PRE_FIX = "p" * 64
GRADER = GraderRef(image="g", environment_id="sha256:e", image_built=False, timeout_s=60)
WORKFLOW = "workflow/workflow.json"


def check(name: str, status: str = "passed", *, blocking: bool = True) -> dict[str, Any]:
    return {
        "name": f"verify.{name}",
        "category": "pytest" if name == "tests" else "command",
        "blocking": blocking,
        "status": status,
        "exit": 0 if status == "passed" else 1,
        "seconds": 1.5,
        "cause": None,
    }


def seal(run: int, verdict: str, *failed: str, seconds: float | None = 2.0) -> dict[str, Any]:
    """One seal: ``failed`` names the blocking checks that failed (all others pass)."""
    checks = [check("tests", "failed" if "tests" in failed else "passed")]
    checks += [check(name, "failed") for name in failed if name != "tests"]
    if "lint" not in failed:
        checks.append(check("lint", "passed"))
    return {
        "who": "seal",
        "run": run,
        "after": f"workflow/build-{run}",
        "verdict": verdict,
        "attribution": verdict if verdict in ("passed", "refused") else "agent_error",
        "seconds": seconds,
        "error": None if verdict != "error" else "a check crashed",
        "checks": [] if verdict == "error" else checks,
    }


def fix_turn(index: int, **tokens: Any) -> dict[str, Any]:
    usage = {"input": 1000, "cached": 0, "output": 100, "cost_usd": 0.25} | tokens
    return {"stage": "build", "kind": "gate_fix", "turn": index, "seconds": 30.0, "tokens": usage}


def write_gate(
    trace: Path,
    seals: list[dict[str, Any]],
    *,
    fixes: tuple[dict[str, Any], ...] = (),
    pre_fix: tuple[dict[str, Any], ...] = (),
    max_fix_rounds: int = 2,
) -> None:
    """``workflow/workflow.json`` of a step: a first build turn, the fix turns and the gate."""
    turns = [{"stage": "build", "kind": "first_build", "turn": 1, "seconds": 90.0, "tokens": None}]
    record = {
        "kind": "raidar-workflow",
        "turns": [*turns, *fixes],
        "gate/1": {
            "max_fix_rounds": max_fix_rounds,
            "errors": [],
            "pre_fix_snapshots": list(pre_fix),
            "gates": seals,
        },
    }
    path = trace / WORKFLOW
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")


def write_ungated(trace: Path) -> None:
    """A package workflow record with no ``gate/1`` primitive."""
    path = trace / WORKFLOW
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"kind": "raidar-workflow", "turns": []}), encoding="utf-8")


def pre_fix_entry(digest: str | None = PRE_FIX, round_: int = 1) -> dict[str, Any]:
    return {"round": round_, "path": f"gate/pre_fix_{round_}/app", "tree_digest": digest}


def step(tmp_path: Path, name: str, *, reached: bool = True) -> StepInputs:
    trace = tmp_path / name / "agent"
    if reached:
        trace.mkdir(parents=True, exist_ok=True)
    return StepInputs(
        step=name,
        reached=reached,
        checkpoint=name,
        snapshot=None,
        snapshot_digest=None,
        record=None,
        record_digest=None,
        trace_dir=trace if reached else None,
        trace_digest="t" if reached else None,
        agent_result=None,
    )


def trace(item: StepInputs) -> Path:
    assert item.trace_dir is not None
    return item.trace_dir


def with_snapshot(tmp_path: Path, item: StepInputs, source: str) -> StepInputs:
    root = tmp_path / item.step / "snapshot"
    root.mkdir(parents=True, exist_ok=True)
    (root / "forge.py").write_text(source)
    return dataclasses.replace(item, snapshot=root, snapshot_digest=f"d-{item.step}")


def context(*steps: StepInputs, starter: Path | None = None) -> AssessContext:
    return AssessContext(problem="scb/forge", steps=steps, config={}, starter=starter)


def assess(*steps: StepInputs, store: Path | None = None) -> AssessResult:
    ctx = context(*steps)
    scorer = verification_effort_scorer_for_store(store) if store else verification_effort_scorer()
    return validate_result(VERIFICATION_EFFORT, ctx, scorer.assess(ctx))


def values(result: AssessResult) -> dict[tuple[str, str], Observation]:
    return {(item.metric, item.subject): item for item in result.observations}


def value(result: AssessResult, metric: str, subject: str) -> float | None:
    return values(result)[(metric, subject)].value


def _grade(name: str, passed: int, failed: int) -> VerifierGrade:
    statuses = [CaseStatus.PASSED] * passed + [CaseStatus.FAILED] * failed
    cases = tuple(
        CaseResult(id=f"c{i}", group=CaseGroup.CORE, checkpoint_origin=name, status=status)
        for i, status in enumerate(statuses)
    )
    return VerifierGrade(step=name, checkpoint=name, cases=cases)


def graded(item: StepInputs, passed: int, failed: int) -> StepInputs:
    """``item`` with the verifier's own final grade."""
    grade = _grade(item.step, passed, failed)
    record = StepRecord(
        step=item.step,
        checkpoint=item.step,
        bundle=BundleRef(root="/b", digest=BUNDLE, problem="toy"),
        snapshot=SnapshotRef(requested="artifact", used="artifact", digest="s", stored_path="x"),
        grader=GRADER,
        grade=grade,
        summary=grade.summary(),
        coverage=CoverageRef(enabled=False),
        rewards=grade.rewards(),
    )
    return dataclasses.replace(item, record=record, record_digest="r")


def store_pre_fix_grade(store: Path, name: str, passed: int, failed: int) -> None:
    identity = grading_identity(GRADER)
    path = grade_path(store, BUNDLE, name, PRE_FIX, identity=identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": GRADE_KIND,
        "identity": identity,
        "schema_version": SCHEMA_VERSION,
        "step": name,
        "snapshot_digest": PRE_FIX,
        "grade": _grade(name, passed, failed).model_dump(mode="json"),
    }
    path.write_text(json.dumps(payload))
