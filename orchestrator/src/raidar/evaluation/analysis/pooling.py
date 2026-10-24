"""The pooling gate: which jobs may be analysed together, and whether their records are intact.

A job executed under the mechanics epoch carries provenance in ``job.json`` (``runner``,
``recipes``, ``effective_limits``, ``execution_target``) and a records manifest per trial.
``check_pooling`` always verifies each job's records manifests (tamper, never overridable);
with two or more jobs it refuses any difference in the fields below unless named in
``--pool-override``. A job without ``mechanics_epoch`` has no manifest: it is reported
``unmeasured`` (warned and labelled, not refused).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from raidar.contract.job_layout import TRIAL_RECORDS_DIR, TRIALS_DIR
from raidar.engine.health.records_manifest import tamper_findings
from raidar.evaluation.analysis.outcomes import PoolFinding, PoolingReport

if TYPE_CHECKING:
    from raidar.evaluation.analysis.sources import JobDir


class PoolingError(ValueError):
    """The jobs cannot be pooled, or a job's records are not intact."""


PRE_EPOCH = "pre-epoch"
UNRECORDED = "unrecorded"
TAMPER = "tamper"
OVERRIDABLE = (
    "mechanics_epoch",
    "toolchain",
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


def _subject(job: JobDir) -> dict[str, str]:
    arms: dict[str, str] = {}
    for trial in job.plan.get("trials", ()):
        arms.setdefault(str(trial["arm"]), str(trial.get("hashes", {}).get("agent")))
    return dict(sorted(arms.items()))


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


def _refusals(jobs: Sequence[JobDir]) -> list[PoolFinding]:
    measured = [job for job in jobs if _epoch(job) != PRE_EPOCH]
    checks: list[tuple[str, Sequence[JobDir], Callable[[JobDir], Any]]] = [
        ("mechanics_epoch", jobs, _epoch),
        ("toolchain", measured, lambda j: _get(j.manifest, "runner", "toolchain_sha256")),
        ("pack_recipe", measured, lambda j: _get(j.manifest, "recipes", "pack")),
        ("grader_recipe", measured, lambda j: _get(j.manifest, "recipes", "graders")),
        ("subject", jobs, _subject),
        ("effective_limits", measured, lambda j: _get(j.manifest, "effective_limits")),
        ("timing_cost", jobs, _hardware),
    ]
    found = []
    for name, scope, value in checks:
        message = _difference(scope, value, name)
        if message is not None:
            found.append(PoolFinding(field=name, message=message))
    dirty = [job.job_id for job in measured if _get(job.manifest, "runner", "dirty") is not False]
    if dirty:
        found.append(
            PoolFinding(
                field="dirty_runner",
                message=f"runner checkout was dirty (or unknown) for: {', '.join(dirty)}",
            )
        )
    return found


def _check_overrides(overrides: Sequence[str]) -> None:
    unknown = sorted(set(overrides) - set(OVERRIDABLE))
    if unknown:
        hint = " (tamper is never overridable)" if TAMPER in unknown else ""
        raise PoolingError(
            f"--pool-override names unknown fields {unknown}{hint}; choose from "
            f"{', '.join(OVERRIDABLE)}"
        )


def _refuse(refused: Sequence[PoolFinding]) -> None:
    if refused:
        names = ", ".join(item.field for item in refused)
        raise PoolingError(
            "the jobs cannot be pooled: "
            + "; ".join(item.message for item in refused)
            + f". Analyse them apart, or lift with --pool-override ({names})"
        )


def _require_intact(jobs: Sequence[JobDir]) -> None:
    violations = [finding for job in jobs if _epoch(job) != PRE_EPOCH for finding in _tamper(job)]
    if violations:
        raise PoolingError(
            "records manifest violation (never overridable): " + "; ".join(violations)
        )


def _report(jobs: Sequence[JobDir], lifted: Sequence[PoolFinding]) -> PoolingReport:
    return PoolingReport(
        unmeasured=tuple(job.job_id for job in jobs if _epoch(job) == PRE_EPOCH),
        overrides_used=tuple(item.field for item in lifted),
        findings=tuple(lifted),
        timing_cost_comparable=all(item.field != "timing_cost" for item in lifted),
    )


def check_pooling(jobs: Sequence[JobDir], overrides: Sequence[str] = ()) -> PoolingReport:
    """The pooling report for ``jobs``, or a ``PoolingError`` naming every refusal."""
    _check_overrides(overrides)
    _require_intact(jobs)
    refusals = _refusals(jobs) if len(jobs) > 1 else []
    _refuse([item for item in refusals if item.field not in overrides])
    return _report(jobs, [item for item in refusals if item.field in overrides])
