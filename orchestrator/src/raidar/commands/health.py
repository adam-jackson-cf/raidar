"""``raidar health JOB``: recompute every trial's run-health audit (``health.json``).

The audit also runs as each trial finishes (``raidar.execution.trial_run``); this command
re-derives it from the files on the host, which is the only point where ``T2`` (the sealed
records manifest) can be verified, and appends any new provisional fault candidates to
``<job>/fault-candidates.jsonl``. It exits 1 when any trial has a violation.
"""

from __future__ import annotations

from pathlib import Path

import click

from raidar.execution.fault_candidates import append_fault_candidates
from raidar.execution.health import audit_recorded_trial
from raidar.execution.health_model import HealthReport, write_health
from raidar.job_layout import JOB_MANIFEST, TRIAL_RECORDS_DIR, require_v2_job


def register(main: click.Group) -> None:
    main.add_command(health_command)


def _trial_ids(job_dir: Path) -> list[str]:
    records = sorted((job_dir / TRIAL_RECORDS_DIR).glob("*.json"))
    if not (job_dir / JOB_MANIFEST).is_file() or not records:
        raise click.ClickException(f"{job_dir} is not a job directory with trial records")
    return [path.stem for path in records]


def _echo(report: HealthReport) -> None:
    violations = len(report.violations)
    click.echo(f"{report.trial_id}: {violations} violation(s), {len(report.unmeasured)} unmeasured")
    for check in report.checks:
        click.echo(f"  {check.status:10} {check.id:9} {check.detail}")


@click.command("health")
@click.argument("job_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
def health_command(job_dir: Path) -> None:
    """Recompute each trial's health.json and record fault candidates."""
    require_v2_job(job_dir)
    failed = 0
    for trial_id in _trial_ids(job_dir):
        report = audit_recorded_trial(job_dir, trial_id)
        write_health(job_dir / "trials" / trial_id, report)
        append_fault_candidates(job_dir, report)
        _echo(report)
        failed += bool(report.violations)
    if failed:
        raise SystemExit(1)
