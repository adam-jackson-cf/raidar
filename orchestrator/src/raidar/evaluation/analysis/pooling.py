"""The pooling gate: which jobs may be analysed together, and whether their records are intact.

A job executed under the mechanics epoch carries provenance in ``job.json`` (``runner``,
``recipes``, ``effective_limits``, ``execution_target``) and a records manifest per trial.
``check_pooling`` always verifies each job's records manifests (tamper, never overridable);
with two or more jobs it refuses any difference in the fields below unless named in
``--pool-override``. The arm identity is compared in full (design 5.4): ``subject`` (each
arm's agent and workflow hashes) can be overridden, ``stage_code`` (each arm's stage code
digest) never can. A job without ``mechanics_epoch`` has no manifest: it is reported
``unmeasured`` (warned and labelled, not refused).

Whatever the number of jobs, every arm of every measured job must carry the same capability
declaration (``plan.json`` ``arms.<arm>.capabilities``; none recorded is refused), and every
valid trial of a problem, across arms and jobs, the same parity record (``TrialRecord.parity``:
image, seeded workspace, limits, network policy, agent process; none recorded is refused).
Neither can be overridden: the arm is the only thing an experiment varies. The report records
the shared declaration and, per problem, the shared parity record and how many trials share it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from raidar.contract.job_layout import TRIAL_RECORDS_DIR, TRIALS_DIR
from raidar.engine.health.records_manifest import tamper_findings
from raidar.evaluation.analysis.outcomes import ParityComparison, PoolFinding, PoolingReport

if TYPE_CHECKING:
    from raidar.evaluation.analysis.sources import JobDir


class PoolingError(ValueError):
    """The jobs cannot be pooled, or a job's records are not intact."""


PRE_EPOCH = "pre-epoch"
UNRECORDED = "unrecorded"
TAMPER = "tamper"
STAGE_CODE = "stage_code"
OVERRIDABLE = (
    "mechanics_epoch",
    "toolchain",
    "execution_code",
    "dirty_runner",
    "pack_recipe",
    "grader_recipe",
    "subject",
    "effective_limits",
    "timing_cost",
)


def _get(data: Mapping[str, Any], *path: str) -> Any:
    for key in path:
        if not isinstance(data, Mapping) or key not in data:
            return UNRECORDED
        data = data[key]
    return data


def _epoch(job: JobDir) -> str:
    return str(job.manifest.get("mechanics_epoch") or PRE_EPOCH)


def _hardware(job: JobDir) -> Any:
    target = job.manifest.get("execution_target")
    if not isinstance(target, Mapping):
        return UNRECORDED
    docker = target.get("docker") or {}
    return {
        "target": target.get("name"),
        "docker": {key: docker.get(key) for key in ("os", "arch", "ncpu", "mem_total")},
    }


def _arm_hashes(job: JobDir, *keys: str) -> dict[str, Any]:
    arms: dict[str, Any] = {}
    for trial in job.plan.get("trials", ()):
        hashes = trial.get("hashes", {})
        arms.setdefault(str(trial["arm"]), {key: hashes.get(key, UNRECORDED) for key in keys})
    return dict(sorted(arms.items()))


def _subject(job: JobDir) -> dict[str, Any]:
    """Each arm's agent and workflow identity."""
    return _arm_hashes(job, "agent", "workflow")


def _stage_code(job: JobDir) -> dict[str, Any]:
    """Each arm's stage code identity (never overridable)."""
    return _arm_hashes(job, STAGE_CODE)


def _tamper(job: JobDir) -> list[str]:
    sealed = job.manifest.get("records_manifests")
    sealed = sealed if isinstance(sealed, Mapping) else {}
    trials = [record.trial.trial_id for record in job.records]
    return [
        f"{job.job_id}: trial {trial}: {finding}"
        for trial in dict.fromkeys([*trials, *sealed])
        for finding in tamper_findings(
            job.path / TRIALS_DIR / trial,
            job.path / TRIAL_RECORDS_DIR / f"{trial}.json",
            job.path,
            sealed.get(trial),
        )
    ]


def _difference(jobs: Sequence[JobDir], value: Callable[[JobDir], Any], name: str) -> str | None:
    values = {job.job_id: value(job) for job in jobs}
    if len({repr(v) for v in values.values()}) <= 1:
        return None
    shown = "; ".join(f"{job}={v}" for job, v in values.items())
    return f"{name} differs between jobs: {shown}"


def _execution_difference(jobs: Sequence[JobDir]) -> PoolFinding | None:
    difference = _difference(
        jobs, lambda job: _get(job.manifest, "runner", "execution_sha256"), "execution_code"
    )
    missing = [
        job.job_id
        for job in jobs
        if _get(job.manifest, "runner", "execution_sha256") in (UNRECORDED, None, "")
    ]
    if difference is None and missing:
        difference = "execution code identity is unrecorded for: " + ", ".join(missing)
    return PoolFinding(field="execution_code", message=difference) if difference else None


def _refusals(jobs: Sequence[JobDir]) -> list[PoolFinding]:
    measured = [job for job in jobs if _epoch(job) != PRE_EPOCH]
    checks: list[tuple[str, Sequence[JobDir], Callable[[JobDir], Any]]] = [
        ("mechanics_epoch", jobs, _epoch),
        ("toolchain", measured, lambda j: _get(j.manifest, "runner", "toolchain_sha256")),
        ("pack_recipe", measured, lambda j: _get(j.manifest, "recipes", "pack")),
        ("grader_recipe", measured, lambda j: _get(j.manifest, "recipes", "graders")),
        ("subject", jobs, _subject),
        (STAGE_CODE, jobs, _stage_code),
        ("effective_limits", measured, lambda j: _get(j.manifest, "effective_limits")),
        ("timing_cost", jobs, _hardware),
    ]
    found = []
    for name, scope, value in checks:
        message = _difference(scope, value, name)
        if message is not None:
            found.append(PoolFinding(field=name, message=message))
    execution_difference = _execution_difference(measured)
    if execution_difference is not None:
        found.append(execution_difference)
    dirty = [job.job_id for job in measured if _get(job.manifest, "runner", "dirty") is not False]
    if dirty:
        found.append(
            PoolFinding(
                field="dirty_runner",
                message=f"runner checkout was dirty (or unknown) for: {', '.join(dirty)}",
            )
        )
    return found


CAPABILITIES = "capabilities"
PARITY = "parity"
NEVER_OVERRIDABLE = (TAMPER, STAGE_CODE, CAPABILITIES, PARITY)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _differing_keys(variants: Sequence[Mapping[str, Any]]) -> list[str]:
    keys = sorted({key for variant in variants for key in variant})
    return [key for key in keys if len({_canonical(v.get(key)) for v in variants}) > 1]


def _declarations(job: JobDir) -> dict[str, dict[str, str] | None]:
    """Each arm the job planned or recorded, with its capability declaration (``None``: none)."""
    arms = job.plan.get("arms") or {}
    planned = {str(trial["arm"]) for trial in job.plan.get("trials", ())}
    found: dict[str, dict[str, str] | None] = {}
    for arm in sorted(planned | {record.trial.arm for record in job.records}):
        entry = arms.get(arm)
        value = entry.get(CAPABILITIES) if isinstance(entry, Mapping) else None
        found[f"{job.job_id}/{arm}"] = dict(value) if isinstance(value, Mapping) and value else None
    return found


def _measured(jobs: Sequence[JobDir]) -> list[JobDir]:
    return [job for job in jobs if _epoch(job) != PRE_EPOCH]


def _capability_findings(
    missing: Sequence[str], variants: Mapping[str, Mapping[str, str]]
) -> list[PoolFinding]:
    found = []
    if missing:
        message = f"no capability declaration recorded for arm(s) {', '.join(missing)}"
        found.append(PoolFinding(field=CAPABILITIES, message=message))
    if len(variants) > 1:
        keys = _differing_keys(list(variants.values()))
        message = f"the arms declare different capabilities ({', '.join(keys)})"
        found.append(PoolFinding(field=CAPABILITIES, message=message))
    return found


def _capabilities(jobs: Sequence[JobDir]) -> tuple[dict[str, str] | None, list[PoolFinding]]:
    """The one declaration every arm of every measured job shares, and what refuses it."""
    arms = {name: value for job in _measured(jobs) for name, value in _declarations(job).items()}
    missing = [name for name, value in arms.items() if value is None]
    variants = {_canonical(value): value for value in arms.values() if value is not None}
    found = _capability_findings(missing, variants)
    shared = next(iter(variants.values())) if len(variants) == 1 and not found else None
    return shared, found


def _parity_records(jobs: Sequence[JobDir]) -> list[tuple[str, str, dict[str, Any] | None]]:
    """``(problem, job/trial, parity record)`` of every valid trial of every measured job."""
    return [
        (
            record.trial.problem,
            f"{job.job_id}/{record.trial.trial_id}",
            record.parity.model_dump(mode="json") if record.parity is not None else None,
        )
        for job in _measured(jobs)
        for record in job.records
        if record.valid
    ]


def _parity(jobs: Sequence[JobDir]) -> tuple[dict[str, ParityComparison], list[PoolFinding]]:
    """Per problem, the parity record its valid trials share, and what refuses it."""
    groups: dict[str, dict[str, list[str]]] = {}
    values: dict[str, dict[str, Any]] = {}
    found = []
    entries = _parity_records(jobs)
    missing = [name for _, name, value in entries if value is None]
    if missing:
        message = f"no parity record for trial(s) {', '.join(missing)}"
        found.append(PoolFinding(field=PARITY, message=message))
    for problem, name, value in entries:
        if value is not None:
            key = _canonical(value)
            values[key] = value
            groups.setdefault(problem, {}).setdefault(key, []).append(name)
    shared = {}
    for problem, variants in sorted(groups.items()):
        if len(variants) == 1:
            ((key, trials),) = variants.items()
            shared[problem] = ParityComparison(trials=len(trials), record=values[key])
            continue
        keys = _differing_keys([values[key] for key in variants])
        shown = "; ".join(", ".join(trials) for trials in variants.values())
        message = f"{problem}: trials differ in {', '.join(keys)} ({shown})"
        found.append(PoolFinding(field=PARITY, message=message))
    return shared, found


def _check_overrides(overrides: Sequence[str]) -> None:
    unknown = sorted(set(overrides) - set(OVERRIDABLE))
    if unknown:
        never = sorted(set(unknown) & set(NEVER_OVERRIDABLE))
        hint = f" ({', '.join(never)} is never overridable)" if never else ""
        raise PoolingError(
            f"--pool-override names unknown fields {unknown}{hint}; choose from "
            f"{', '.join(OVERRIDABLE)}"
        )


def _refuse(refused: Sequence[PoolFinding]) -> None:
    if refused:
        lifts = [item.field for item in refused if item.field in OVERRIDABLE]
        hint = f"lift with --pool-override ({', '.join(lifts)})" if lifts else "nothing lifts it"
        raise PoolingError(
            "the jobs cannot be pooled: "
            + "; ".join(item.message for item in refused)
            + f". Analyse them apart, or {hint}"
        )


def _require_intact(jobs: Sequence[JobDir]) -> None:
    violations = [finding for job in jobs if _epoch(job) != PRE_EPOCH for finding in _tamper(job)]
    if violations:
        raise PoolingError(
            "records manifest violation (never overridable): " + "; ".join(violations)
        )


def _report(
    jobs: Sequence[JobDir],
    lifted: Sequence[PoolFinding],
    capabilities: dict[str, str] | None,
    parity: dict[str, ParityComparison],
) -> PoolingReport:
    return PoolingReport(
        unmeasured=tuple(job.job_id for job in jobs if _epoch(job) == PRE_EPOCH),
        overrides_used=tuple(item.field for item in lifted),
        findings=tuple(lifted),
        timing_cost_comparable=all(item.field != "timing_cost" for item in lifted),
        capabilities=capabilities,
        parity=parity,
    )


def check_pooling(jobs: Sequence[JobDir], overrides: Sequence[str] = ()) -> PoolingReport:
    """The pooling report for ``jobs``, or a ``PoolingError`` naming every refusal."""
    _check_overrides(overrides)
    _require_intact(jobs)
    refusals = _refusals(jobs) if len(jobs) > 1 else []
    capabilities, capability_refusals = _capabilities(jobs)
    parity, parity_refusals = _parity(jobs)
    refused = [item for item in refusals if item.field not in overrides]
    _refuse([*refused, *capability_refusals, *parity_refusals])
    lifted = [item for item in refusals if item.field in overrides]
    return _report(jobs, lifted, capabilities, parity)
