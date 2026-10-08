"""``gate-effort@10``'s per-step ``new_autofix_*`` values (what each step's own work added).

A shadow gate never fixes ``/app``, so its dry run at every step re-fixes the
whole tree: its per-step ``autofix_lines_changed`` and
``autofix_removed_blocking`` repeat the accumulated debt and their sums count
it again at every step, while an enforced gate's first pass sees only the
step's new code (its previous step handed on a fixed tree). This is the
cumulative-versus-incremental problem gate-effort@6 solved for the blocking
counts, solved the same way: the step's first pass minus what the previous
gated step left for autofix.

- First pass: enforced, the pass before gate run 1; shadow, the dry run.
- Left for autofix at the step's end: enforced, 0 when the step's last pass
  completed (the tree it handed on is the fixed one), unavailable when it
  failed; shadow, the dry run's own counts.
"""

from __future__ import annotations

from collections.abc import Mapping

from raidar.scorers.gate_effort.families import FAMILIES
from raidar.scorers.gate_effort.scorer import Measured
from raidar.scorers.gate_effort_v4.scorer import family_v4
from raidar.scorers.gate_effort_v9.autofix_records import DRY_RUN, AutofixPass, StepAutofix
from raidar.scorers.gate_effort_v9.autofix_values import NO_AUTOFIX, removed
from raidar.scorers.gate_effort_v9.manifest import AUTOFIX_LINES
from raidar.scorers.gate_effort_v9.steps import PerStep
from raidar.scorers.gate_effort_v10.manifest import NEW_LINES, NEW_METRICS, NEW_REMOVED
from raidar.scorers.sdk.result import AssessStatus

_OK = AssessStatus.OK
_NA = AssessStatus.NOT_APPLICABLE
_UNAVAILABLE = AssessStatus.UNAVAILABLE

Counts = dict[str, Measured]


def _gap(status: AssessStatus, reason: str) -> Counts:
    return dict.fromkeys(NEW_METRICS, Measured.gap(status, reason))


def pass_counts(autofix_pass: AutofixPass) -> Counts:
    """One completed pass's lines and block-tier removals (in total and by family)."""
    lines = autofix_pass.lines_changed
    found: Counts = {
        NEW_LINES: Measured(float(lines))
        if lines is not None
        else Measured.gap(_UNAVAILABLE, "an autofix report has no lines")
    }
    taken = removed(autofix_pass)
    if taken is None:
        gap = Measured.gap(_UNAVAILABLE, "an autofix report has no block-tier codes")
        return found | {name: gap for name in NEW_METRICS if name != NEW_LINES}
    by_family = dict.fromkeys(FAMILIES, 0)
    for code, count in (autofix_pass.fixed_blocking or {}).items():
        by_family[family_v4(code)] += count
    found[NEW_REMOVED] = Measured(float(taken))
    return found | {
        f"{NEW_REMOVED}.{family}": Measured(float(count)) for family, count in by_family.items()
    }


def _failed(autofix_pass: AutofixPass, what: str) -> Counts:
    reason = f"the autofix pass {what} failed ({autofix_pass.error_type}: {autofix_pass.error})"
    return _gap(_UNAVAILABLE, reason)


def _first(autofix: StepAutofix, shadow: bool) -> Counts:
    """What the step's first pass found in the step's own work."""
    if shadow:
        found = next((p for p in autofix.passes if p.mode == DRY_RUN), None)
        what = "on the copy"
    else:
        found, what = autofix.before(1), "before run 1"
    if found is None:
        return _gap(_UNAVAILABLE, f"no autofix pass ran {what}")
    return pass_counts(found) if found.ok else _failed(found, what)


def _left(autofix: StepAutofix, shadow: bool, first: Counts) -> Counts:
    """What the step left for the next step's autofix."""
    if shadow:
        return first
    if not autofix.passes:
        return _gap(_UNAVAILABLE, "no autofix pass ran in the step")
    last = autofix.passes[-1]
    if not last.ok:
        return _failed(last, f"run {last.run} (the step's last)")
    return dict.fromkeys(NEW_METRICS, Measured(0.0))


def _minus(first: Counts, previous: Counts) -> Counts:
    found: Counts = {}
    for name in NEW_METRICS:
        now, before = first[name], previous[name]
        if now.status is not _OK:
            found[name] = now
        elif before.status is not _OK or before.value is None:
            reason = f"what the previous gated step left for autofix is unknown: {before.reason}"
            found[name] = Measured.gap(_UNAVAILABLE, reason)
        else:
            found[name] = Measured((now.value or 0.0) - before.value)
    return found


def with_new_autofix(
    found: PerStep, autofix: Mapping[str, StepAutofix | None], shadow: bool
) -> None:
    """Add each step's ``new_autofix_*`` values (``autofix``: the steps' autofix blocks)."""
    previous: Counts = dict.fromkeys(NEW_METRICS, Measured(0.0))
    for step, values in found:
        if not step.inputs.reached:
            values.update(_gap(_NA, "checkpoint not reached"))
            continue
        if step.gate is None or step.error is not None:
            same = values[AUTOFIX_LINES]
            values.update(_gap(same.status, same.reason or "no gate record"))
            if step.gated:
                previous = _gap(_UNAVAILABLE, f"{step.inputs.step} has no readable gate record")
            continue
        block = autofix.get(step.inputs.step)
        if block is None or not block.enabled:
            values.update(_gap(_NA, NO_AUTOFIX))
            previous = _gap(_NA, NO_AUTOFIX)
            continue
        first = _first(block, shadow)
        values.update(_minus(first, previous))
        previous = _left(block, shadow, first)


__all__ = ["pass_counts", "with_new_autofix"]
