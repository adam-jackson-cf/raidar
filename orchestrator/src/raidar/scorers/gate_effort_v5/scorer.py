"""``gate-effort@5``: gate-effort@4 whose evidence messages count ``TEST.*`` as ``tests``.

``gate-effort@4`` (``raidar.scorers.gate_effort_v4``) is released and unchanged.
Its observations already count a ``TEST.*`` finding in the ``tests`` family
(``family_v4``), but the one-line summary of each gate run in its evidence
(``run 2 after stage_2_fix: refused, 3 blocking (...)``) still used
``gate-effort@1``'s family map, so a tests-check refusal read ``other 1``.
@5 keeps every metric, value, status and reason of @4 and writes the evidence
messages with ``family_v4``. Only the evidence text differs.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from raidar.scorers.gate_effort.families import FAMILIES
from raidar.scorers.gate_effort.manifest import BLOCKING_FINDINGS
from raidar.scorers.gate_effort.records import GateRunView, RunFindings
from raidar.scorers.gate_effort.scorer import _read, _Step
from raidar.scorers.gate_effort_v4.scorer import GATE_EFFORT_V4, GateEffortV4Scorer, family_v4
from raidar.scorers.sdk.context import AssessContext, statused_result
from raidar.scorers.sdk.manifest import ScorerManifest
from raidar.scorers.sdk.result import (
    AssessResult,
    AssessStatus,
    Evidence,
    EvidenceSource,
    Span,
)

GATE_EFFORT_V5 = GATE_EFFORT_V4.model_copy(
    update={
        "version": "5",
        "description": (
            "Effort a guardrail-gate commit gate cost per step (gate-effort@3), the gate's "
            "tests check (first run and step end: passed, missing tests, owned coverage) and "
            "the fix rounds' correctness (final minus pre-fix case pass rate, the pre-fix "
            "snapshot graded offline), as gate-effort@4, with each gate run's evidence "
            "summary counting TEST.* findings in the tests family. A shadow gate reports its "
            "first-run and code outcomes only. Not applicable to an arm without a gate."
        ),
    }
)


def families_text(findings: RunFindings) -> str:
    """The run's blocking findings per family (v4 map), as one phrase."""
    if not findings.complete:
        return f"{findings.listed} of them listed"
    counted = Counter(family_v4(code) for code in findings.codes or ())
    named = [f"{family} {counted[family]}" for family in FAMILIES if counted.get(family)]
    return ", ".join(named) or "none"


def run_summary(run: GateRunView) -> str:
    """One line per gate run: verdict, blocking total and its families (v4 map)."""
    head = f"run {run.run} after {run.after}"
    if not run.decided:
        return f"{head}: gate error ({run.error})"
    if run.passed:
        return f"{head}: passed"
    total = run.findings.total
    count = "an unknown number of" if total is None else str(total)
    verdict = "timed out" if run.timed_out else "refused"
    return f"{head}: {verdict}, {count} blocking ({families_text(run.findings)})"


def _run_evidence(step: str, trace_dir: Path, run: GateRunView) -> Evidence | None:
    path = trace_dir / run.log if run.log else None
    if path is None or not path.is_file():
        return None
    data = path.read_bytes()
    lines = data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1)
    if lines == 0:
        return None
    return Evidence(
        metric=BLOCKING_FINDINGS,
        subject=step,
        source=EvidenceSource.TRACE,
        path=run.log or "",
        sha256=hashlib.sha256(data).hexdigest(),
        kind="gate-run",
        spans=(Span(line=1, end_line=lines),),
        message=run_summary(run),
    )


def evidence(steps: Sequence[_Step]) -> tuple[Evidence, ...]:
    """One evidence item per gate run with a log, summarised with the v4 families."""
    found: list[Evidence | None] = []
    for step in steps:
        trace_dir = step.inputs.trace_dir
        if step.gate is None or trace_dir is None:
            continue
        found.extend(_run_evidence(step.inputs.step, trace_dir, run) for run in step.gate.runs)
    return tuple(item for item in found if item is not None)


class GateEffortV5Scorer(GateEffortV4Scorer):
    """gate-effort@4 with v4-family evidence summaries."""

    @property
    def manifest(self) -> ScorerManifest:
        return GATE_EFFORT_V5

    def assess(self, ctx: AssessContext) -> AssessResult:
        if ctx.config:
            return statused_result(
                GATE_EFFORT_V5, ctx, AssessStatus.ERROR, "gate-effort@5 takes no config"
            )
        result = super().assess(ctx)
        if not result.evidence:
            return result
        return result.model_copy(update={"evidence": evidence([_read(i) for i in ctx.steps])})


def gate_effort_v5_scorer() -> GateEffortV5Scorer:
    """Registry factory for ``gate-effort@5`` without a store (pre-fix grades unavailable)."""
    return GateEffortV5Scorer()


def gate_effort_v5_scorer_for_store(
    store_root: Path, *, replay_only: bool = False, trial: str | None = None
) -> GateEffortV5Scorer:
    """Store-bound factory: reads pre-fix grades from the Assessment store."""
    del replay_only, trial
    return GateEffortV5Scorer(store_root)


__all__ = [
    "GATE_EFFORT_V5",
    "GateEffortV5Scorer",
    "evidence",
    "families_text",
    "gate_effort_v5_scorer",
    "gate_effort_v5_scorer_for_store",
    "run_summary",
]
