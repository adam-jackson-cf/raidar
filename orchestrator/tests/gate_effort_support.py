"""Builders for the gate-effort tests: a step's agent log dir as the guardrail-gate wrapper left it.

The gate record, the run logs in ``guardrail-check``'s printed format, the per-run JSON reports
(with their tests block), the autofix block, the pre-fix grades in the Assessment store and the
graded step records.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from raidar.grading.models import CaseGroup, CaseResult, CaseStatus, VerifierGrade
from raidar.grading.prefix_store import GRADE_KIND, grade_path
from raidar.grading.records import (
    BundleRef,
    CoverageRef,
    GraderRef,
    SnapshotRef,
    StepRecord,
)
from raidar.scoring.framework.context import AssessContext, StepInputs
from raidar.scoring.framework.result import AssessResult, AssessStatus, Observation
from raidar.scoring.framework.validation import validate_result
from raidar.scoring.gate_effort import GATE_EFFORT, gate_effort_scorer, gate_effort_scorer_for_store
from raidar.scoring.gate_effort.records import RECORD_PATH, REPORT_SCHEMA

REFUSED = """\
guardrail-check: REFUSED (3 blocking, 1 warnings, 2 Python files)
gate-check structure pass
gate-check ruff fail
gate-check mypy fail
Blocking (fix these; the change is refused until they pass) (3):
  forge.py:10:1: C901 `run` is too complex (11 > 10)
  forge.py:12:5: MYPY.no-untyped-def Function is missing a type annotation
  forge.py:2:1: I001 Import block is un-sorted or un-formatted
Warnings (not blocking) (1):
  forge.py:1:1: D100 Missing docstring in public module
Re-run `guardrail-check` to confirm.
"""
PASSED = "guardrail-check: PASSED (0 blocking, 1 warnings, 2 Python files)\ngate-check ruff pass\n"
CAPPED = (
    "guardrail-check: REFUSED (61 blocking, 0 warnings, 1 Python files)\n"
    "Blocking (fix these; the change is refused until they pass) (61):\n"
    + "".join(f"  a.py:{line}:1: F841 unused\n" for line in range(1, 61))
    + "  ... 1 more (see the JSON report)\n"
)


# ---- builders ----------------------------------------------------------------------------


def _run_record(run: int, after: str, *, passed: bool, error: str | None = None) -> dict:
    return {
        "run": run,
        "after": after,
        "passed": passed,
        "error": error,
        "timed_out": False,
        "log": f"guardrail-gate/gate_run_{run}.log",
        "report_dir": f"guardrail-gate/gate_run_{run}",
        "checks_failed": [] if passed else ["ruff"],
        "duration_sec": 0.5,
    }


def _stage(index: int, role: str, round_no: int, **usage: Any) -> dict:
    return {
        "index": index,
        "stage": role,
        "round": round_no,
        "error": None,
        "n_input_tokens": usage.get("tokens_in", 1000),
        "n_output_tokens": usage.get("tokens_out", 100),
        "cost_usd": usage.get("cost", 0.01),
        "duration_sec": usage.get("seconds", 30.0),
    }


def _write_gate(
    trace: Path,
    *,
    outputs: list[str],
    termination: str,
    signal: bool | None,
    reports: dict[int, list[dict]] | None = None,
    stages: list[dict] | None = None,
    runs: list[dict] | None = None,
    error: str | None = None,
) -> Path:
    gate_dir = trace / "guardrail-gate"
    gate_dir.mkdir(parents=True, exist_ok=True)
    afters = ["stage_1_work", *(f"stage_{n}_fix" for n in range(2, len(outputs) + 1))]
    afters = afters[: len(outputs)]
    default_runs = [
        _run_record(index, after, passed=output.startswith("guardrail-check: PASSED"))
        for index, (after, output) in enumerate(zip(afters, outputs, strict=True), 1)
    ]
    for index, output in enumerate(outputs, 1):
        (gate_dir / f"gate_run_{index}.log").write_text(output)
        (gate_dir / f"gate_run_{index}").mkdir()
    for index, findings in (reports or {}).items():
        report = {"schema": REPORT_SCHEMA, "verdict": "refused", "findings": findings}
        (gate_dir / f"gate_run_{index}" / "gate-1.json").write_text(json.dumps(report))
    default_stages = [_stage(1, "work", 0)] + [
        _stage(n, "fix", n - 1) for n in range(2, len(outputs) + 1)
    ]
    record = {
        "termination_reason": termination,
        "error": error,
        "rounds_configured": 3,
        "gate_runs": runs if runs is not None else default_runs,
        "stages": stages if stages is not None else default_stages,
        "gate_clean_at_step_end": signal,
    }
    (trace / RECORD_PATH).write_text(json.dumps(record))
    return trace


def _step(tmp_path: Path, name: str, *, reached: bool = True) -> StepInputs:
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


def _trace(step: StepInputs) -> Path:
    assert step.trace_dir is not None
    return step.trace_dir


def _assess(
    *steps: StepInputs, store: Path | None = None, config: dict | None = None
) -> AssessResult:
    ctx = AssessContext(problem="scb/forge", steps=steps, config=config or {})
    scorer = gate_effort_scorer_for_store(store) if store else gate_effort_scorer()
    return validate_result(GATE_EFFORT, ctx, scorer.assess(ctx))


def _assess_context(ctx: AssessContext) -> AssessResult:
    return validate_result(GATE_EFFORT, ctx, gate_effort_scorer().assess(ctx))


def _values(result: AssessResult) -> dict[tuple[str, str], Observation]:
    return {(item.metric, item.subject): item for item in result.observations}


def _value(result: AssessResult, metric: str, subject: str) -> float | None:
    return _values(result)[(metric, subject)].value


def _finding(code: str, tier: str = "block") -> dict:
    return {"code": code, "relpath": "a.py", "line": 1, "message": "m", "tier": tier}


def _shadow(trace: Path, output: str, *, findings: list[dict] | None = None) -> None:
    passed = output.startswith("guardrail-check: PASSED")
    _write_gate(
        trace,
        outputs=[output],
        termination="shadow_recorded",
        signal=None,
        reports={1: findings} if findings is not None else None,
    )
    record = json.loads((trace / RECORD_PATH).read_text())
    record |= {"mode": "shadow", "shadow": True, "shadow_verdict": passed}
    (trace / RECORD_PATH).write_text(json.dumps(record))


def _snapshot(tmp_path: Path, step: StepInputs, source: str) -> StepInputs:
    root = tmp_path / step.step / "snapshot"
    root.mkdir(parents=True, exist_ok=True)
    (root / "forge.py").write_text(source)
    (root / "ruff.toml").write_text("[lint]\n")
    return dataclasses.replace(step, snapshot=root, snapshot_digest=f"d-{step.step}")


def _context(*steps: StepInputs, starter: Path | None = None) -> AssessContext:
    return AssessContext(problem="scb/forge", steps=steps, config={}, starter=starter)


def _enforced_sources(tmp_path: Path, sources: list[str]) -> list[StepInputs]:
    steps = []
    for index, source in enumerate(sources, start=1):
        step = _snapshot(tmp_path, _step(tmp_path, f"checkpoint_{index}"), source)
        _write_gate(_trace(step), outputs=[PASSED], termination="gate_clean", signal=True)
        steps.append(step)
    return steps


CP1, CP2, CP3 = "checkpoint_1", "checkpoint_2", "checkpoint_3"
COVERAGE = "TEST.coverage-minimum"
BUNDLE = "b" * 64
PRE_FIX = "p" * 64


def _grade(step: str, passed: int, failed: int) -> VerifierGrade:
    statuses = [CaseStatus.PASSED] * passed + [CaseStatus.FAILED] * failed
    cases = tuple(
        CaseResult(id=f"c{i}", group=CaseGroup.CORE, checkpoint_origin=step, status=status)
        for i, status in enumerate(statuses)
    )
    return VerifierGrade(step=step, checkpoint=step, cases=cases)


def _graded(step: StepInputs, passed: int, failed: int) -> StepInputs:
    grade = _grade(step.step, passed, failed)
    record = StepRecord(
        step=step.step,
        checkpoint=step.step,
        bundle=BundleRef(root="/b", digest=BUNDLE, problem="toy"),
        snapshot=SnapshotRef(requested="artifact", used="artifact", digest="s", stored_path="x"),
        grader=GraderRef(image="g", environment_id="sha256:e", image_built=False, timeout_s=60),
        grade=grade,
        summary=grade.summary(),
        coverage=CoverageRef(enabled=False),
        rewards=grade.rewards(),
    )
    return dataclasses.replace(step, record=record, record_digest="r")


def _tests(passed: bool, *, coverage: float | None = 90.0, ran: int = 3) -> dict:
    return {"required": True, "tests_ran": ran, "coverage_percent": coverage, "runners": {}}


def _report(trace: Path, run: int, findings: list[dict], tests: dict | None) -> None:
    folder = trace / "guardrail-gate" / f"gate_run_{run}"
    folder.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "schema": "python-guardrails.gate-report/1",
        "verdict": "refused" if findings else "passed",
        "findings": findings,
    }
    if tests is not None:
        report["tests"] = tests
    (folder / "gate-1.json").write_text(json.dumps(report))


def _pre_fix(trace: Path, digest: str | None = PRE_FIX) -> None:
    record = json.loads((trace / RECORD_PATH).read_text())
    record["pre_fix_snapshots"] = [
        {"round": 1, "before_session": "stage_2_fix", "path": "p", "tree_digest": digest}
    ]
    (trace / RECORD_PATH).write_text(json.dumps(record))


def _store_grade(store: Path, step: str, passed: int, failed: int) -> None:
    path = grade_path(store, BUNDLE, step, PRE_FIX)
    path.parent.mkdir(parents=True, exist_ok=True)
    grade = _grade(step, passed, failed)
    payload = {
        "kind": GRADE_KIND,
        "step": step,
        "snapshot_digest": PRE_FIX,
        "grade": grade.model_dump(mode="json"),
    }
    path.write_text(json.dumps(payload))


def _enforced(tmp_path: Path) -> tuple[StepInputs, StepInputs]:
    """cp1: refused for missing tests, fixed (a fix round); cp2: clean first time."""
    first = _graded(_step(tmp_path, CP1), passed=3, failed=1)
    _write_gate(_trace(first), outputs=[REFUSED, PASSED], termination="gate_clean", signal=True)
    _report(
        _trace(first),
        1,
        [_finding("TEST.missing"), _finding("C901")],
        _tests(False, coverage=None, ran=0),
    )
    _report(_trace(first), 2, [], _tests(True, coverage=92.5))
    _pre_fix(_trace(first))
    second = _graded(_step(tmp_path, CP2), passed=4, failed=0)
    _write_gate(_trace(second), outputs=[PASSED], termination="gate_clean", signal=True)
    _report(_trace(second), 1, [], _tests(True, coverage=88.0))
    return first, second


def _tests_block(*, ran: int, failed: int = 0, coverage: float | None = 90.0) -> dict:
    return {
        "required": True,
        "tests_ran": ran,
        "failed": failed,
        "coverage_percent": coverage,
        "runners": {},
    }


def _at(code: str, relpath: str = "forge.py", line: int = 1, tier: str = "block") -> dict:
    return {"code": code, "relpath": relpath, "line": line, "message": "m", "tier": tier}


def _status(result: AssessResult, metric: str, subject: str) -> AssessStatus:
    return _values(result)[(metric, subject)].status


def _pass(run: int, *, mode: str = "apply", before: int | None = None, **extra: Any) -> dict:
    return {
        "run": run,
        "mode": mode,
        "before_gate_run": before if mode == "apply" else None,
        "after_gate_run": 1 if mode == "dry_run" else None,
        "error_type": None,
        "error": None,
        "files_changed": ["forge.py"],
        "lines_changed": 6,
        "codes_fixed": {"FORMAT": 1, "I001": 1, "D202": 1},
        "codes_fixed_blocking": {"FORMAT": 1, "I001": 1},
        "post_check": None,
        **extra,
    }


def _autofix(trace: Path, runs: list[dict], *, mode: str = "apply") -> None:
    record = json.loads((trace / RECORD_PATH).read_text())
    record["autofix"] = {
        "enabled": True,
        "command": "guardrail-check --autofix",
        "mode": mode,
        "runs": runs,
        "errors": sum(1 for run in runs if run["error_type"]),
    }
    (trace / RECORD_PATH).write_text(json.dumps(record))


def _post_check(trace: Path, findings: list[dict]) -> dict:
    folder = trace / "guardrail-gate" / "post_autofix_run_1"
    folder.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": "python-guardrails.gate-report/1",
        "verdict": "refused" if findings else "passed",
        "findings": findings,
    }
    (folder / "gate-1.json").write_text(json.dumps(report))
    return {
        "run": 1,
        "after": "stage_1_work",
        "passed": not findings,
        "error": None,
        "timed_out": False,
        "log": "guardrail-gate/post_autofix_run_1.log",
        "report_dir": "guardrail-gate/post_autofix_run_1",
        "mode": "shadow",
    }


def _enforced_autofix(tmp_path: Path) -> StepInputs:
    """cp1: autofix, then refused for C901 and the coverage minimum, fixed in one round."""
    step = _step(tmp_path, CP1)
    trace = _trace(step)
    _write_gate(
        trace,
        outputs=[REFUSED, PASSED],
        termination="gate_clean",
        signal=True,
        stages=[_stage(1, "work", 0), _stage(2, "fix", 1, cost=0.05, seconds=120.0)],
    )
    _report(trace, 1, [_at("C901"), _at(COVERAGE)], None)
    _report(trace, 2, [], None)
    _autofix(
        trace,
        [
            _pass(1, before=1),
            _pass(
                2,
                before=2,
                files_changed=[],
                lines_changed=0,
                codes_fixed={},
                codes_fixed_blocking={},
            ),
        ],
    )
    return step


GUARDRAIL_STYLE_GATE = (
    'if [ -f fixed.txt ]; then echo "guardrail-check: PASSED (0 blocking, 0 warnings, 1 Python '
    'files)"; else echo "guardrail-check: REFUSED (2 blocking, 0 warnings, 1 Python files)"; '
    'echo "Blocking (fix these) (2):"; echo "  feature.py:1:1: C901 too complex"; '
    'echo "  feature.py:2:1: MYPY.no-untyped-def missing"; '
    'printf \'{"schema": "%s", "findings": [{"code": "C901", "tier": "block"}, '
    '{"code": "MYPY.no-untyped-def", "tier": "block"}]}\' '
    f'"{REPORT_SCHEMA}" > "$RAIDAR_GATE_REPORT_DIR/gate-$RAIDAR_GATE_RUN.json"; exit 1; fi'
)
