"""``gate-effort``'s evidence: one item per gate run with a log, summarised in one line.

The summary reads ``run 2 after stage_2_fix: refused, 3 blocking (tests 1, ...)``; a
``TEST.*`` finding counts in the ``tests`` family.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

from raidar.scoring.framework.result import Evidence, EvidenceSource, Span
from raidar.scoring.gate_effort.families import family_counts
from raidar.scoring.gate_effort.metrics import BLOCKING_FINDINGS
from raidar.scoring.gate_effort.outcomes import Step
from raidar.scoring.gate_effort.records import GateRunView, RunFindings


def families_text(findings: RunFindings) -> str:
    """The run's blocking findings per family, as one phrase."""
    if not findings.complete:
        return f"{findings.listed} of them listed"
    counts = family_counts(findings.codes or ())
    named = [f"{family} {count}" for family, count in counts.items() if count]
    return ", ".join(named) or "none"


def run_summary(run: GateRunView) -> str:
    """One line per gate run: verdict, blocking total and its families."""
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


def evidence(steps: Sequence[Step]) -> tuple[Evidence, ...]:
    """One evidence item per gate run with a log."""
    found: list[Evidence | None] = []
    for step in steps:
        trace_dir = step.inputs.trace_dir
        if step.gate is None or trace_dir is None:
            continue
        found.extend(_run_evidence(step.inputs.step, trace_dir, run) for run in step.gate.runs)
    return tuple(item for item in found if item is not None)
