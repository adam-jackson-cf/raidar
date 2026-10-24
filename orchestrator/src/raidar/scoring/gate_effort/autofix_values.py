"""The autofix values of one step: passes, what they fixed, counts after it.

Read from the step's ``autofix`` block (``autofix_records``) beside the step's
cross-mode values. A step whose gate ran without autofix (guardrail-gate@1-@3,
or @4 with it off) has every autofix value ``not_applicable`` and its raw counts
are the step's own. Enforced (``apply``): the first gate run is the post-autofix
check, and the raw count adds back what the first pass removed. Shadow
(``dry_run``): the raw counts are the shadow check of the workspace and the
post-autofix ones come from the check of the fixed copy. A pass that failed
makes what depended on it ``unavailable`` with its error type, never a guess.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from raidar.scoring.framework.result import AssessStatus
from raidar.scoring.gate_effort.autofix_metrics import (
    AUTOFIX_ERRORS,
    AUTOFIX_FILES,
    AUTOFIX_LINES,
    AUTOFIX_REMOVED,
    AUTOFIX_ROUNDS,
    FIRST_PASS_POST,
    FIRST_RUN_POST,
    FIRST_RUN_RAW,
    STEP_END_POST,
)
from raidar.scoring.gate_effort.autofix_records import AutofixPass, StepAutofix
from raidar.scoring.gate_effort.cross_mode_metrics import FIRST_RUN_NET, STEP_END_NET
from raidar.scoring.gate_effort.families import FAMILIES, family_of
from raidar.scoring.gate_effort.findings import CODES_UNLISTED, UNTYPED_CALL_CODE, codes_once
from raidar.scoring.gate_effort.folding import duplicates
from raidar.scoring.gate_effort.measured import Measured
from raidar.scoring.gate_effort.outcomes import Step, first_pass
from raidar.scoring.gate_effort.records import GateRunView, StepGate

_OK = AssessStatus.OK
_NA = AssessStatus.NOT_APPLICABLE
_UNAVAILABLE = AssessStatus.UNAVAILABLE
_INSUFFICIENT = AssessStatus.INSUFFICIENT_EVIDENCE

NO_AUTOFIX = "the gate ran without autofix"
AUTOFIX_METRICS = (
    AUTOFIX_ROUNDS,
    AUTOFIX_ERRORS,
    AUTOFIX_FILES,
    AUTOFIX_LINES,
    AUTOFIX_REMOVED,
    *(f"{AUTOFIX_REMOVED}.{family}" for family in FAMILIES),
)
POST_METRICS = (FIRST_PASS_POST, FIRST_RUN_POST, STEP_END_POST)


def _failed(autofix_pass: AutofixPass, what: str) -> Measured:
    reason = f"the autofix pass {what} failed ({autofix_pass.error_type}: {autofix_pass.error})"
    return Measured.gap(_UNAVAILABLE, reason)


def _sum(values: list[int | None], missing: str) -> Measured:
    if any(value is None for value in values):
        return Measured.gap(_UNAVAILABLE, missing)
    return Measured(float(sum(value or 0 for value in values)))


def removed(autofix_pass: AutofixPass) -> int | None:
    """Block-tier findings one pass fixed (None: its report did not say)."""
    fixed = autofix_pass.fixed_blocking
    return None if fixed is None else sum(fixed.values())


def _family_removed(passes: list[AutofixPass]) -> dict[str, Measured]:
    names = [f"{AUTOFIX_REMOVED}.{family}" for family in FAMILIES]
    if any(p.fixed_blocking is None for p in passes):
        gap = Measured.gap(_UNAVAILABLE, "an autofix report has no block-tier codes")
        return dict.fromkeys(names, gap)
    counts = dict.fromkeys(FAMILIES, 0)
    for autofix_pass in passes:
        for code, count in (autofix_pass.fixed_blocking or {}).items():
            counts[family_of(code)] += count
    return {f"{AUTOFIX_REMOVED}.{family}": Measured(float(counts[family])) for family in FAMILIES}


def pass_values(autofix: StepAutofix) -> dict[str, Measured]:
    """What the step's autofix passes did (completed passes only; failures counted apart)."""
    done = [p for p in autofix.passes if p.ok]
    return {
        AUTOFIX_ROUNDS: Measured(float(len(done))),
        AUTOFIX_ERRORS: Measured(float(len(autofix.passes) - len(done))),
        AUTOFIX_FILES: _sum([p.files_changed for p in done], "an autofix report has no files"),
        AUTOFIX_LINES: _sum([p.lines_changed for p in done], "an autofix report has no lines"),
        AUTOFIX_REMOVED: _sum([removed(p) for p in done], "an autofix report has no codes"),
    } | _family_removed(done)


# ---- one gate run's net count -------------------------------------------------------------


def run_net(trace_dir: Path, run: GateRunView, record: Mapping[str, object]) -> Measured:
    """One run's blocking findings as the ``*_net`` counts them (cascade out, fold applied)."""
    if not run.decided:
        return Measured.gap(_INSUFFICIENT, f"the check could not decide: {run.error}")
    codes = codes_once(run)
    if codes is None:
        return Measured.gap(_UNAVAILABLE, CODES_UNLISTED)
    folded = duplicates(trace_dir, dict(record), run.findings.codes)
    if folded is None:
        return Measured.gap(_UNAVAILABLE, "the check's folded findings cannot be located")
    return Measured(float(len(codes) - codes.count(UNTYPED_CALL_CODE) - folded))


# ---- after and before autofix --------------------------------------------------------------


def _shadow_post(trace_dir: Path | None, autofix: StepAutofix) -> dict[str, Measured]:
    dry = next((p for p in autofix.passes if p.mode == "dry_run"), None)
    if dry is None:
        gap = Measured.gap(_INSUFFICIENT, "no autofix pass ran (the raw check did not decide)")
        return dict.fromkeys(POST_METRICS, gap)
    if not dry.ok:
        return dict.fromkeys(POST_METRICS, _failed(dry, "on the copy"))
    if dry.post_check is None or dry.post_check_record is None or trace_dir is None:
        gap = Measured.gap(_UNAVAILABLE, "no check of the fixed copy was recorded")
        return dict.fromkeys(POST_METRICS, gap)
    count = run_net(trace_dir, dry.post_check, dry.post_check_record)
    clean = Measured(1.0 if dry.post_check.passed else 0.0) if dry.post_check.decided else count
    return {FIRST_PASS_POST: clean, FIRST_RUN_POST: count, STEP_END_POST: count}


def _enforced_post(
    gate: StepGate, autofix: StepAutofix, values: Mapping[str, Measured]
) -> dict[str, Measured]:
    first = autofix.before(1)
    if first is None:
        gap = values[FIRST_RUN_NET]
        found = {FIRST_PASS_POST: gap, FIRST_RUN_POST: gap}
    elif not first.ok:
        found = dict.fromkeys((FIRST_PASS_POST, FIRST_RUN_POST), _failed(first, "before run 1"))
    else:
        found = {FIRST_PASS_POST: first_pass(gate), FIRST_RUN_POST: values[FIRST_RUN_NET]}
    decided = [run for run in gate.runs if run.decided]
    last = autofix.before(decided[-1].run) if decided else None
    if not decided or last is None:
        found[STEP_END_POST] = values[STEP_END_NET]
    elif not last.ok:
        found[STEP_END_POST] = _failed(last, f"before run {decided[-1].run}")
    else:
        found[STEP_END_POST] = values[STEP_END_NET]
    return found


def _raw(autofix: StepAutofix | None, shadow: bool, values: Mapping[str, Measured]) -> Measured:
    """The first run's count before autofix (an enforced pass's removals added back)."""
    net = values[FIRST_RUN_NET]
    if autofix is None or not autofix.enabled or shadow or net.status is not _OK:
        return net
    first = autofix.before(1)
    if first is None or not first.ok:
        return net  # no pass completed before run 1: that run was the raw tree
    taken = removed(first)
    if taken is None:
        return Measured.gap(_UNAVAILABLE, "the first autofix report has no block-tier codes")
    return Measured((net.value or 0.0) + taken)


def autofix_step_values(
    step: Step, autofix: StepAutofix | None, shadow: bool, values: Mapping[str, Measured]
) -> dict[str, Measured]:
    """The step's autofix, post-autofix and raw values (the cross-mode ``values`` beside)."""
    names = (*AUTOFIX_METRICS, *POST_METRICS, FIRST_RUN_RAW)
    if not step.inputs.reached:
        return dict.fromkeys(names, Measured.gap(_NA, "checkpoint not reached"))
    if step.gate is None or step.error is not None:
        first = values[FIRST_RUN_NET]
        return dict.fromkeys(names, Measured.gap(first.status, first.reason or "no gate record"))
    found = {FIRST_RUN_RAW: _raw(autofix, shadow, values)}
    if autofix is None or not autofix.enabled:
        gap = Measured.gap(_NA, NO_AUTOFIX)
        return found | dict.fromkeys((*AUTOFIX_METRICS, *POST_METRICS), gap)
    found |= pass_values(autofix)
    if shadow:
        return found | _shadow_post(step.inputs.trace_dir, autofix)
    return found | _enforced_post(step.gate, autofix, values)


__all__ = ["AUTOFIX_METRICS", "NO_AUTOFIX", "autofix_step_values", "removed", "run_net"]
