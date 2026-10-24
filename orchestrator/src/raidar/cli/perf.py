"""``raidar perf check``: N1 overhead budgets over recorded runs (plan D4).

Report-only by default (user decision 2026-10-01): budget breaches are
warnings and the exit code is 0; malformed input still exits non-zero.
``--strict`` fails on any breach; the enforced gate (``make perf-gate``) runs
the no-LLM fixture with ``--strict``.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from raidar.engine.perf.budgets import (
    MIN_SAMPLES,
    NOT_RECORDED_BY_RUNS,
    PerfInputError,
    PhaseCheck,
    load_job_samples,
)
from raidar.engine.perf.contention import UNDETECTED_DOCKER_ACTIVITY, Contention
from raidar.engine.perf.perf_report import PerfReport, build_report


def _label(check: PhaseCheck) -> str:
    budget = check.budget
    return f"{budget.phase}[{budget.condition}]" if budget.condition else budget.phase


def _check_line(check: PhaseCheck, prefix: str = "") -> str:
    budget = check.budget
    value = check.maximum if budget.tripwire else check.p95
    shown = f"{value:.2f}" if value is not None else "-"
    kind = "max" if budget.tripwire else "p95"
    return (
        f"  {prefix}{check.verdict.value:12} {_label(check):40} n={check.samples:<4} "
        f"{kind}={shown:>7} budget={budget.seconds:g}s"
    )


def _contention_lines(contention: Contention) -> list[str]:
    configured = ", ".join(
        f"{run}={value if value is not None else 'unrecorded'}"
        for run, value in contention.configured_concurrency.items()
    )
    lines = [
        "Contention context:",
        f"  configured concurrency: {configured}",
        f"  peak concurrent trials (checked runs): {contention.peak_concurrent_trials}",
        f"  peak concurrent trials incl. overlapping runs: {contention.peak_including_other_jobs}",
    ]
    lines += [f"  overlapping run: {run}" for run in contention.overlapping_jobs]
    if contention.untimed_trials:
        lines.append(f"  trials without start/finish timestamps: {contention.untimed_trials}")
    lines.append(f"  note: {UNDETECTED_DOCKER_ACTIVITY}")
    return lines


def _warning_lines(report: PerfReport) -> list[str]:
    lines = []
    for index, check in enumerate(report.checks):
        if check in report.breaches:
            lines.append(_check_line(check, "WARNING "))
            lines += [
                _check_line(split.check, f"  at {split.label} concurrent: ")
                for split in report.splits.get(index, [])
            ]
    return lines


def _echo_text(report: PerfReport, trials: int, min_samples: int, strict: bool) -> None:
    mode = "strict" if strict else "report-only"
    click.echo(f"N1 budget check ({mode}) over {trials} trials (min samples {min_samples})")
    click.echo(f"budgets: {report.budgets}")
    for check in report.checks:
        click.echo(_check_line(check))
    for phase, reason in NOT_RECORDED_BY_RUNS.items():
        click.echo(f"  {'not-checked':12} {phase:40} {reason}")
    for line in _contention_lines(report.contention):
        click.echo(line)
    if report.breaches:
        click.echo(f"Budget warnings ({len(report.breaches)}):")
        for line in _warning_lines(report):
            click.echo(line)
        if not strict:
            click.echo("report-only: breaches are warnings (exit 0); pass --strict to fail on them")


def _payload(report: PerfReport, trials: int, min_samples: int, strict: bool) -> dict[str, object]:
    return {
        "passed": report.passed,
        "strict": strict,
        "budgets": report.budgets,
        "trials": trials,
        "min_samples": min_samples,
        "checks": [check.to_json() for check in report.checks],
        "warnings": [
            {
                **check.to_json(),
                "by_concurrency": [split.to_json() for split in report.splits.get(index, [])],
            }
            for index, check in enumerate(report.checks)
            if check in report.breaches
        ],
        "contention": report.contention.to_json(),
        "not_recorded_by_runs": NOT_RECORDED_BY_RUNS,
    }


def register(main: click.Group) -> None:
    main.add_command(perf_group)


@click.group("perf")
def perf_group() -> None:
    """N1 framework-overhead budgets."""


@perf_group.command("check")
@click.argument("job_dirs", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--min-samples",
    type=click.IntRange(min=1),
    default=MIN_SAMPLES,
    show_default=True,
    help="Samples a budgeted phase needs before its p95 is judged.",
)
@click.option(
    "--strict",
    is_flag=True,
    help="Exit 1 on any budget breach (insufficient, missing or over budget).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the checks as JSON.")
def perf_check_command(
    job_dirs: tuple[Path, ...], min_samples: int, strict: bool, as_json: bool
) -> None:
    """Report each phase's p95 against its accepted N1 budget (overhead-baseline.md §5).

    Report-only unless --strict: breaches are warnings and the exit code is 0.
    Malformed or empty input always exits non-zero.
    """
    try:
        loaded = load_job_samples(list(job_dirs))
        if not loaded.trial_count:
            raise click.ClickException("no trial records under the given job directories")
        report = build_report(list(job_dirs), loaded, min_samples=min_samples)
    except PerfInputError as error:
        raise click.ClickException(str(error)) from error
    if as_json:
        payload = _payload(report, loaded.trial_count, min_samples, strict)
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _echo_text(report, loaded.trial_count, min_samples, strict)
    if strict and not report.passed:
        raise SystemExit(1)
