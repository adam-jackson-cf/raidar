"""Manifest of ``gate-effort``: what a commit gate cost the agent, per step.

It lives beside its implementation so the released scorers' digests, which cover
``builtin_manifests``, stay unchanged. The metrics are the ones of ``metrics``,
``cross_mode_metrics`` and ``autofix_metrics``, in that order (ids are part of the
scorer's identity).
"""

from __future__ import annotations

from raidar.scorers.gate_effort.autofix_metrics import (
    AUTOFIX_STEP_METRICS,
    AUTOFIX_TRAJECTORY_METRICS,
    NEW_AUTOFIX_STEP_METRICS,
    NEW_AUTOFIX_TRAJECTORY_METRICS,
)
from raidar.scorers.gate_effort.cross_mode_metrics import (
    EX_CASCADE_STEP_METRICS,
    EX_CASCADE_TRAJECTORY_METRICS,
    NET_STEP_METRICS,
    NET_TRAJECTORY_METRICS,
)
from raidar.scorers.gate_effort.metrics import (
    BLOCKING_STEP_METRICS,
    BLOCKING_TRAJECTORY_METRICS,
    EFFORT_STEP_METRICS,
    EFFORT_TRAJECTORY_METRICS,
    TESTS_STEP_METRICS,
    TESTS_TRAJECTORY_METRICS,
)
from raidar.scorers.sdk.manifest import EvidenceInput, ScorerManifest

GATE_EFFORT = ScorerManifest(
    id="gate-effort",
    version="10",
    description=(
        "Effort a guardrail-gate commit gate cost per step, the gate's tests check and "
        "the fix rounds' correctness (gate-effort@7: a failed tests check counted once, "
        "blocking counts that compare across gate modes with and without the mypy "
        "no-untyped-call cascade, failing tests and missed coverage apart, first-pass "
        "clean rates that ignore auto-fixable layout codes, the tests check, or both), plus "
        "the cross-mode blocking counts without the cascade and with a refused suppression "
        "comment counted once (mypy's unused-ignore at a SUPPRESS line folded into it). A "
        "shadow gate reports its first-run, step-end and code outcomes only. Not applicable "
        "to an arm without a gate. Plus a guardrail-gate@4 autofix step's effect: "
        "passes, errors, files and lines changed, block-tier findings fixed by family, "
        "first-pass clean and blocking counts after autofix (and before, comparable "
        "across an enforced and a shadow gate), and the fix sessions' cost and wall time "
        "split by the kind of refusal that started them (coverage minimum apart). Plus the "
        "autofix lines and block-tier findings "
        "that each step's own work added (new_autofix_*), which compare across an "
        "enforced and a shadow gate where the per-step autofix sums do not."
    ),
    runtimes=("*",),
    evidence_required=(
        EvidenceInput.TRACE,
        EvidenceInput.ORACLE_CASES,
        EvidenceInput.SNAPSHOT_CURRENT,
        EvidenceInput.SNAPSHOT_PRIOR,
    ),
    metrics=(
        *EFFORT_STEP_METRICS,
        *EFFORT_TRAJECTORY_METRICS,
        *TESTS_STEP_METRICS,
        *TESTS_TRAJECTORY_METRICS,
        *BLOCKING_STEP_METRICS,
        *BLOCKING_TRAJECTORY_METRICS,
        *EX_CASCADE_STEP_METRICS,
        *EX_CASCADE_TRAJECTORY_METRICS,
        *NET_STEP_METRICS,
        *NET_TRAJECTORY_METRICS,
        *AUTOFIX_STEP_METRICS,
        *AUTOFIX_TRAJECTORY_METRICS,
        *NEW_AUTOFIX_STEP_METRICS,
        *NEW_AUTOFIX_TRAJECTORY_METRICS,
    ),
)

__all__ = ["GATE_EFFORT"]
