"""Markdown summary of an ``AnalysisReport`` (the human half of the gate packet)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from raidar.analysis.curves import Band, Curve
from raidar.analysis.effects import Effect, Exclusions, IntervalOut
from raidar.analysis.manipulation import ManipulationReport
from raidar.analysis.report import AnalysisReport, ArmSummary, Comparison
from raidar.analysis.stats import confidence_label

MISSING = "—"


def _num(value: float | None) -> str:
    if value is None:
        return MISSING
    return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:.4g}"


def _ci(interval: IntervalOut | None) -> str:
    if interval is None:
        return MISSING
    return f"[{interval.low:.4g}, {interval.high:.4g}]"


def _table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return [*lines, ""]


def _excluded(exclusions: Exclusions) -> str:
    return (
        f"{exclusions.excluded}/{exclusions.planned} (infra {exclusions.infra_error}, "
        f"ineligible {exclusions.ineligible}, unassessed {exclusions.unassessed}, "
        f"not run {exclusions.not_run})"
    )


def _arm_row(arm: ArmSummary) -> list[str]:
    return [
        arm.arm,
        str(arm.planned),
        str(arm.run),
        str(arm.valid),
        str(arm.infra_error),
        str(arm.not_run),
        _num(arm.cost_means.get("total_cost_usd")),
        _num(arm.cost_means.get("total_tokens")),
        _num(arm.cost_means.get("total_agent_wall_s")),
    ]


def _effect_row(effect: Effect) -> list[str]:
    return [
        effect.metric,
        effect.view,
        effect.problem,
        str(effect.n_pairs),
        _num(effect.baseline_mean),
        _num(effect.treatment_mean),
        _num(effect.mean_delta),
        _ci(effect.ci),
        f"**{effect.verdict.value}**",
        _excluded(effect.exclusions),
        f"{_num(effect.cost.delta_mean)} {_ci(effect.cost.delta_ci)}",
    ]


def _band(band: Band) -> str:
    return f"{_num(band.mean)} {_ci(band.ci)} n={band.n}"


def _curve(curve: Curve) -> list[str]:
    lines = [f"#### {curve.label} (`{curve.series}`, {curve.view} view, {curve.problem})", ""]
    rows = (
        [point.step, _band(point.baseline), _band(point.treatment), _band(point.delta)]
        for point in curve.points
    )
    return lines + _table(("Checkpoint", "Baseline", "Treatment", "Δ"), rows)


def _manipulation(report: ManipulationReport | None) -> list[str]:
    if report is None:
        return []
    rows = (
        [
            check.check,
            _num(check.baseline.mean),
            _num(check.guided.mean),
            _num(check.p_value),
            check.verdict.value,
            check.reason,
        ]
        for check in report.checks
    )
    return [
        f"### Manipulation check: **{report.verdict.value}**",
        "",
        *_table(("Check", "Baseline", "Treatment", "p", "Verdict", "Reason"), rows),
    ]


def _mcnemar(effects: Sequence[Effect]) -> list[str]:
    rows = [
        [
            effect.problem,
            str(t.both_solved),
            str(t.baseline_only),
            str(t.treatment_only),
            str(t.neither),
            _num(t.p_value),
        ]
        for effect in effects
        if (t := effect.mcnemar) is not None
    ]
    if not rows:
        return []
    header = ("Problem", "Both", "Baseline only", "Treatment only", "Neither", "Exact p")
    return ["### Final checkpoint solved (paired 2×2, exact McNemar)", "", *_table(header, rows)]


def _comparison(comparison: Comparison, confidence: float) -> list[str]:
    primary = comparison.primary
    header = (
        "Metric", "View", "Problem", "n", "Baseline", "Treatment", "Δ",
        f"{confidence_label(confidence)} CI",
        "Verdict", "Excluded", "Cost Δ USD",
    )  # fmt: skip
    lines = [
        f"## {comparison.treatment} vs {comparison.baseline}",
        "",
        f"**Primary outcome** `{primary.metric}` ({primary.problem}): "
        f"Δ {_num(primary.mean_delta)} {_ci(primary.ci)} "
        f"→ **{primary.verdict.value}** ({primary.verdict_reason}); "
        f"cost Δ {_num(primary.cost.delta_mean)} USD per trajectory.",
        "",
        "### Effects",
        "",
        *_table(header, (_effect_row(effect) for effect in comparison.effects)),
        *_mcnemar([e for e in comparison.effects if e.mcnemar is not None]),
        *_manipulation(comparison.manipulation),
        "### Per-checkpoint curves",
        "",
    ]
    for curve in comparison.curves:
        lines.extend(_curve(curve))
    return lines


def _sample_size(report: AnalysisReport) -> list[str]:
    rows = []
    for comparison in report.comparisons:
        for effect in comparison.effects:
            size = effect.sample_size
            if size is None:
                continue
            rows.append(
                [
                    f"{comparison.treatment} vs {comparison.baseline}",
                    effect.metric,
                    effect.problem,
                    size.basis,
                    str(size.pilot_pairs),
                    _num(size.pilot_sd),
                    f"{size.delta:g} ({size.delta_source})",
                    MISSING if size.recommended_pairs is None else str(size.recommended_pairs),
                    MISSING if size.additional_pairs is None else str(size.additional_pairs),
                    _num(size.projected_cost_usd),
                    size.reason or "",
                ]
            )
    basis = report.sample_size_basis
    standardized = ", ".join(f"d={d}: {n}" for d, n in basis.standardized.items())
    header = (
        "Comparison", "Metric", "Problem", "Basis", "Pilot n", "Pilot SD", "δ", "Pairs",
        "Additional", "Projected USD", "Note",
    )  # fmt: skip
    return [
        "## Sample size (J2)",
        "",
        f"`{basis.formula}`: {basis.power:.0%} power at two-sided alpha {basis.alpha}. "
        f"Standardized effects: {standardized}. Size confirmatory runs from these; "
        "never stop early on p < .05.",
        "",
        *_table(header, rows),
    ]


def _settings_line(report: AnalysisReport) -> str:
    settings = report.settings
    pinned = "pinned by the lock" if settings.pinned_by_lock else "not pinned by the lock"
    changed = ", ".join(settings.changed_from_defaults) or "none"
    overridden = ", ".join(settings.overridden) or "none"
    return (
        f"- Verdict thresholds ({settings.source}; {pinned}; overridden on the command line: "
        f"{overridden}; changed from defaults: {changed}): "
        f"min pairs {settings.min_pairs}, max excluded share "
        f"{settings.max_exclusion_fraction:.0%}, alpha {settings.alpha}; intervals are "
        f"paired t below {settings.bootstrap_min_pairs} pairs, bootstrap "
        f"({settings.bootstrap_samples} resamples, seed {settings.seed}) from there"
    )


def render_markdown(report: AnalysisReport) -> str:
    policy = report.headline_policy
    lines = [f"# Analysis: {report.experiment}", ""]
    if report.dev_run is not None:
        lines += [f"> **Development run, not arm evidence:** {report.dev_run.note}.", ""]
    lines += [
        f"- Lock: `{report.lock_sha256[:12]}`; runs: {', '.join(report.runs)}",
        f"- Primary outcome: `{report.primary_metric}` (progression `{report.progression}`); "
        f"baseline arm: `{report.baseline_arm}`",
        f"- Composite S: headline policy `{policy.get('ref')}` ({policy.get('selection')})",
        _settings_line(report),
    ]
    if report.replaced_trajectories:
        lines.append(f"- Replaced invalid trajectories: {', '.join(report.replaced_trajectories)}")
    header = (
        "Arm", "Planned", "Run", "Valid", "Infra error", "Not run",
        "Mean cost USD", "Mean tokens", "Mean agent s",
    )  # fmt: skip
    lines += ["", "## Arms", "", *_table(header, (_arm_row(arm) for arm in report.arms))]
    for comparison in report.comparisons:
        lines.extend(_comparison(comparison, report.settings.confidence))
    lines.extend(_sample_size(report))
    return "\n".join(lines).rstrip() + "\n"
