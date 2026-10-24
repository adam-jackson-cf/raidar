"""The run-health audit of one trial (``raidar health``; mechanics-v1 design r2 §3.2, step 5).

``audit_trial`` re-derives, from the evidence a trial left on the host, whether the trial's
result can be trusted, and returns one ``HealthReport`` (``health.json``). Families:

- **V** verdict integrity: ``V1`` a timed-out evaluator is never a pass; ``V2`` graded case
  counts agree between the verifier ledger and the trial record; ``V3`` the evaluated tree
  digest equals the step's final ``/app`` snapshot.
- **H** limits and records: ``H1`` every expected record file is present; ``H3`` effective
  limits are recorded; ``H4``-``H6`` agent, agent-setup and verifier wall times stay within
  them; ``H-target`` the Docker target stayed reachable (step 1's ``target_reachable``).
- **L** liveness: ``L1`` a liveness record exists per agent phase; ``L2`` the idle decision
  (``idle_observed`` and an enforce ``idle_killed`` are a pass with a finding: the kill is
  classified like an agent timeout; ``kill_failed`` is a violation).
- **P** comparability: ``P2`` batch concurrency is recorded; ``P3`` job-level comparison warnings.
- **T** tamper: ``T1`` the tool tree is unchanged since the first hash; ``T2`` the records
  manifest verifies; ``T3`` the reaper left no residual process.

A check without evidence is ``unmeasured`` with its reason, never ``pass``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from raidar.contract.job_layout import TRIAL_RECORDS_DIR
from raidar.contract.snapshot import tree_digest
from raidar.engine.health.base import Audit, make_check
from raidar.engine.health.model import PASS, UNMEASURED, VIOLATION, HealthCheck, HealthReport
from raidar.engine.health.signals import (
    check_l1,
    check_l2,
    check_p2,
    check_p3,
    check_t1,
    check_t2,
    check_t3,
)
from raidar.engine.perf.contention import read_manifest
from raidar.engine.trial.models import CheckpointState, StepResult, TrialRecord
from raidar.engine.trial.timestamps import span_s
from raidar.grading.records import StepRecord

DEADLINE_GRACE_S = 60.0
"""Harbor ends a phase at its timeout and then cancels, tears down and writes its timing, so
a phase's recorded span may run a little past its limit; beyond this grace it is a violation."""


def _timed_out(ledger: StepRecord) -> bool:
    failure = ledger.grade.infra_failure
    return failure is not None and "timeout" in failure.code


def _counted_as_pass(step: StepResult, ledger: StepRecord) -> bool:
    rewards = step.rewards or {}
    return (
        step.checkpoint_state is CheckpointState.SOLVED
        or rewards.get("solved", 0.0) > 0
        or ledger.grade.solved
    )


def check_v1(a: Audit) -> HealthCheck:
    graded = a.graded()
    if not graded:
        return make_check("V1", UNMEASURED, "no verifier record to audit")
    timed_out = [step.step for step, ledger in graded if _timed_out(ledger)]
    counted = [
        step.step
        for step, ledger in graded
        if _timed_out(ledger) and _counted_as_pass(step, ledger)
    ]
    if counted:
        return make_check(
            "V1", VIOLATION, f"evaluator timed out but counted as a pass: {counted}", steps=counted
        )
    return make_check(
        "V1",
        PASS,
        f"{len(graded)} graded step(s); evaluator timeouts: {timed_out or 'none'}, none a pass",
    )


def _count_mismatch(step: StepResult, ledger: StepRecord) -> str | None:
    grade = ledger.grade
    rate = grade.pass_rate()
    recorded = (step.rewards or {}).get("strict_pass_rate")
    if ledger.summary != grade.summary():
        return "ledger summary differs from its own cases"
    if step.strict_pass_rate != rate:
        return f"record strict_pass_rate {step.strict_pass_rate!r} vs ledger {rate!r}"
    if rate is not None and recorded is not None and abs(recorded - rate) > 1e-9:
        return f"reward strict_pass_rate {recorded!r} vs ledger {rate!r}"
    if step.rewards is not None and step.rewards != ledger.rewards:
        return "record rewards differ from the ledger's"
    return None


def check_v2(a: Audit) -> HealthCheck:
    graded = a.graded()
    if not graded:
        return make_check("V2", UNMEASURED, "no verifier record to audit")
    found = {step.step: why for step, ledger in graded if (why := _count_mismatch(step, ledger))}
    if found:
        return make_check(
            "V2", VIOLATION, f"case counts disagree: {found}", steps=list(found), mismatches=found
        )
    cases = {step.step: len(ledger.grade.cases) for step, ledger in graded}
    return make_check(
        "V2", PASS, f"graded cases agree between ledger and record: {cases}", cases=cases
    )


def _digest_findings(a: Audit, step: StepResult, ledger: StepRecord) -> tuple[str | None, bool]:
    """(mismatch, verified): ``verified`` is False when the stored tree is not there to hash."""
    digest = ledger.snapshot.digest
    if step.snapshot is None or step.snapshot.digest != digest:
        return f"{step.step}: record snapshot differs from the ledger's evaluated digest", True
    stored = a.trial_dir / ledger.snapshot.stored_path
    if not stored.is_dir():
        return None, False
    actual = tree_digest(stored)
    if actual != digest:
        return f"{step.step}: stored /app snapshot {actual[:12]} != evaluated {digest[:12]}", True
    return None, True


def _v3_complete(results: list[tuple[StepResult, str | None, bool]]) -> HealthCheck:
    unchecked = [step.step for step, _, verified in results if not verified]
    if unchecked:
        detail = f"stored /app snapshot not on the host for {unchecked}"
        return make_check("V3", UNMEASURED, detail, steps=unchecked)
    detail = f"evaluated digest equals the stored /app snapshot for {len(results)} step(s)"
    return make_check("V3", PASS, detail)


def check_v3(a: Audit) -> HealthCheck:
    graded = a.graded()
    if not graded:
        return make_check("V3", UNMEASURED, "no verifier record to audit")
    results = [(step, *_digest_findings(a, step, ledger)) for step, ledger in graded]
    bad = [(step.step, why) for step, why, _ in results if why]
    if bad:
        return make_check(
            "V3", VIOLATION, "; ".join(why for _, why in bad), steps=[s for s, _ in bad]
        )
    return _v3_complete(results)


def check_h1(a: Audit) -> HealthCheck:
    missing = [label for label, path in a.expected_files() if not path.is_file()]
    if missing:
        return make_check("H1", VIOLATION, f"missing record files: {missing}", missing=missing)
    detail = f"result.json and {len(a.attempted)} step(s) of record files present"
    return make_check("H1", PASS, detail)


def check_h3(a: Audit) -> HealthCheck:
    if not a.job:
        return make_check("H3", UNMEASURED, "no job.json")
    limits = a.limits
    if limits is None:
        return make_check(
            "H3", UNMEASURED, "job.json records no effective_limits (a job from before step 4)"
        )
    absent = [
        key
        for key in ("agent_s", "agent_setup_s", "verifier_s", "multipliers")
        if key not in limits
    ]
    absent += [
        f"{key}.{a.arm}"
        for key in ("agent_s", "agent_setup_s")
        if isinstance(limits.get(key), Mapping) and a.arm not in limits[key]
    ]
    if absent:
        return make_check("H3", VIOLATION, f"effective_limits lacks {absent}", missing=absent)
    return make_check("H3", PASS, "effective limits recorded", limits=dict(limits))


def _scaled(a: Audit, key: str, multiplier: str) -> float | None:
    limits = a.limits or {}
    base = limits.get(key)
    if isinstance(base, Mapping):
        base = base.get(a.arm)
    if not isinstance(base, int | float):
        return None
    multipliers = limits.get("multipliers") or {}
    return (
        float(base)
        * float(multipliers.get(multiplier) or 1.0)
        * float(multipliers.get("timeout_multiplier") or 1.0)
    )


def _wall_time(
    check_id: str,
    a: Audit,
    limit: float | None,
    spans: Sequence[tuple[str | None, float | None]],
    what: str,
) -> HealthCheck:
    if limit is None:
        return make_check(
            check_id, UNMEASURED, f"no recorded {what} limit (the task's own default applies)"
        )
    measured = [(step, span) for step, span in spans if span is not None]
    if not measured:
        return make_check(check_id, UNMEASURED, f"no {what} timing in result.json")
    over = [(step, span) for step, span in measured if span > limit + DEADLINE_GRACE_S]
    if over:
        return make_check(
            check_id,
            VIOLATION,
            f"{what} ran {over} s past the {limit:g} s limit (grace {DEADLINE_GRACE_S:g} s)",
            steps=[step for step, _ in over if step],
            limit_s=limit,
        )
    return make_check(
        check_id,
        PASS,
        f"{what} within {limit:g} s (longest {max(s for _, s in measured):.1f} s)",
        limit_s=limit,
    )


def check_h4(a: Audit) -> HealthCheck:
    spans = [
        (s.step, span_s((a.items.get(s.step) or {}).get("agent_execution"))) for s in a.attempted
    ]
    return _wall_time(
        "H4", a, _scaled(a, "agent_s", "agent_timeout_multiplier"), spans, "agent phase"
    )


def check_h5(a: Audit) -> HealthCheck:
    spans = [(None, span_s((a.result or {}).get("agent_setup")))]
    limit = _scaled(a, "agent_setup_s", "agent_setup_timeout_multiplier")
    return _wall_time("H5", a, limit, spans, "agent setup")


def check_h6(a: Audit) -> HealthCheck:
    spans = [(s.step, span_s((a.items.get(s.step) or {}).get("verifier"))) for s in a.attempted]
    return _wall_time(
        "H6", a, _scaled(a, "verifier_s", "verifier_timeout_multiplier"), spans, "verifier phase"
    )


def check_h_target(a: Audit) -> HealthCheck:
    lost = [c for c in a.record.validity.checks if c.name == "target_reachable" and not c.passed]
    if lost:
        return make_check("H-target", VIOLATION, lost[0].detail)
    target = a.job.get("execution_target")
    if not isinstance(target, Mapping):
        return make_check("H-target", UNMEASURED, "job.json records no execution_target")
    return make_check(
        "H-target", PASS, f"target {target.get('name')!r} stayed reachable for the trial"
    )


CHECKS: tuple[Callable[[Audit], HealthCheck], ...] = (
    check_v1, check_v2, check_v3,
    check_h1, check_h3, check_h4, check_h5, check_h6, check_h_target,
    check_l1, check_l2,
    check_p2, check_p3,
    check_t1, check_t2, check_t3,
)  # fmt: skip


def audit_trial(
    job_dir: Path, trial_dir: Path, record: TrialRecord, job: Mapping[str, Any]
) -> HealthReport:
    audit = Audit(job_dir, trial_dir, record, job)
    return HealthReport(
        trial_id=record.trial.trial_id, checks=tuple(check(audit) for check in CHECKS)
    )


def audit_recorded_trial(job_dir: Path, trial_id: str) -> HealthReport:
    """Audit a finished trial from its files (what ``raidar health`` does)."""
    record_file = job_dir / TRIAL_RECORDS_DIR / f"{trial_id}.json"
    record = TrialRecord.model_validate_json(record_file.read_text())
    return audit_trial(job_dir, job_dir / record.trial_dir, record, read_manifest(job_dir))
