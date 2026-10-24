"""Markdown summary of an ``AnalysisReport`` (the human half of the gate packet)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from raidar.analysis.curves import Band, Curve
from raidar.analysis.effects import Effect, Exclusions, IntervalOut
from raidar.analysis.gate_effort import GateDescriptive
from raidar.analysis.manipulation import CheckSeparation, ManipulationReport
from raidar.analysis.outcomes import (
    ALL_METRICS,
    CROSS_MODE_CUMULATIVE,
    GATE_EFFORT_REF,
    GATE_METRICS,
    GATE_PREFIX,
    PROCESS_COMPLIANCE_REF,
    REFUSAL_KINDS,
    VERIFIER_DISAGREEMENTS,
    VERIFIER_METRICS,
)
from raidar.analysis.ranking import Ranking, RankingRow
from raidar.analysis.report import AnalysisReport, ArmSummary, Comparison
from raidar.analysis.stats import confidence_label
from raidar.scorers.gate_effort.families import FAMILIES
from raidar.scorers.sdk.manifest import Direction

MISSING = "—"
BETTER = {
    Direction.HIGHER_BETTER: "↑ higher",
    Direction.LOWER_BETTER: "↓ lower",
    Direction.NEUTRAL: "neutral",
}
"""How each compared metric's ``Better`` cell reads (the direction its scorer declares)."""
DIRECTIONS = {metric.id: metric.direction for metric in (*ALL_METRICS, *VERIFIER_METRICS)}
BETTER_LEGEND = (
    "Better: the direction the metric's scorer declares (↑ higher is better, ↓ lower is "
    "better); Δ is treatment minus baseline, so whether a Δ is an improvement depends on it. "
    "Every quality component (V, E, H, R, G) is a score where higher is better (erosion E is 1 "
    "minus the share of code mass in complex functions)."
)


CUMULATIVE_MARK = " †"
CUMULATIVE_LEGEND = (
    "† A shadow gate sums this over its never-fixed tree at every step (it re-counts the "
    "accumulated debt), an enforced gate over each step's new work only, so a shadow arm's "
    "value does not compare with an enforced arm's. Compare total_new_first_run_blocking* "
    "and total_new_autofix_* across gate modes."
)


def _better(metric: str, direction: Direction | None = None) -> str:
    found = direction if direction is not None else DIRECTIONS.get(metric)
    better = BETTER[found] if found is not None else MISSING
    return better + CUMULATIVE_MARK if metric in CROSS_MODE_CUMULATIVE else better


def _legend(metrics: Iterable[str]) -> list[str]:
    """The cumulative-count legend, when a table shows a marked metric."""
    return ["", CUMULATIVE_LEGEND, ""] if CROSS_MODE_CUMULATIVE.intersection(metrics) else []


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
        _better(effect.metric, effect.direction),
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
    direction = DIRECTIONS.get(curve.series)
    better = "" if direction is None else f"; better: {BETTER[direction]}"
    lines = [
        f"#### {curve.label} (`{curve.series}`, {curve.view} view, {curve.problem}{better})",
        "",
    ]
    rows = (
        [point.step, _band(point.baseline), _band(point.treatment), _band(point.delta)]
        for point in curve.points
    )
    return lines + _table(("Checkpoint", "Baseline", "Treatment", "Δ"), rows)


def _basis(check: CheckSeparation) -> str:
    if check.min_share is None:
        return check.basis
    return f"{check.basis} (min share {check.min_share:g})"


def _manipulation(report: ManipulationReport | None) -> list[str]:
    if report is None:
        return []
    rows = (
        [
            check.check,
            _num(check.baseline.mean),
            _num(check.guided.mean),
            _num(check.p_value),
            _basis(check),
            check.verdict.value,
            check.reason,
        ]
        for check in report.checks
    )
    source = "pre-registered by the lock" if report.preregistered else "process defaults"
    header = ("Check", "Baseline", "Treatment", "p", "Basis", "Verdict", "Reason")
    return [
        f"### Manipulation check: **{report.verdict.value}**",
        "",
        f"Checks: {source}.",
        "",
        *_table(header, rows),
    ]


GATE_HEADLINE = tuple(
    metric for metric in GATE_METRICS if "." not in metric.id.removeprefix(GATE_PREFIX)
)
GATE_RANKING = (
    "gate.first_pass_clean_rate",
    "gate.unresolved_rate",
    "gate.mean_rounds_to_clean",
    "gate.total_fix_cost_usd",
)


def _descriptive(item: GateDescriptive | None) -> str:
    if item is None:
        return MISSING
    if item.n == 0:
        gaps = ", ".join(f"{status} {count}" for status, count in item.gaps.items())
        return f"{MISSING} ({gaps or 'no trials'})"
    return f"{_num(item.mean)} {_ci(item.interval)} n={item.n}"


def _gate_mean(arm: ArmSummary, metric: str) -> float | None:
    item = (arm.gate_effort or {}).get(metric)
    return item.mean if item is not None else None


UNTYPED_CALL_ROW = "typing: of which no-untyped-call (call-site cascade)"
"""gate-effort reports the mypy ``no-untyped-call`` cascade apart from the typing family."""


def _cascade_row(arms: Sequence[ArmSummary]) -> list[list[str]]:
    metric = f"{GATE_PREFIX}total_first_run_untyped_call"
    if not any(metric in (arm.gate_effort or {}) for arm in arms):
        return []
    return [[UNTYPED_CALL_ROW, *(f"{_num(_gate_mean(arm, metric))} / —" for arm in arms)]]


def _family_row(family: str, arms: Sequence[ArmSummary]) -> list[str]:
    kinds = ("total_first_run_blocking", "total_blocking_findings")
    return [
        family,
        *(
            " / ".join(_num(_gate_mean(arm, f"gate.{kind}.{family}")) for kind in kinds)
            for arm in arms
        ),
    ]


def _descriptive_rows(
    columns: Sequence[dict[str, GateDescriptive]], metrics: Sequence[str]
) -> list[list[str]]:
    """One row per metric any column reports: the metric, then each column's descriptive."""
    reported = {metric for column in columns for metric in column}
    return [
        [metric, _better(metric), *(_descriptive(column.get(metric)) for column in columns)]
        for metric in metrics
        if metric in reported
    ]


def _gate_arms(report: AnalysisReport) -> list[str]:
    arms = [arm for arm in report.arms if arm.gate_effort is not None]
    if not arms:
        return []
    names = [arm.arm for arm in arms]
    columns = [arm.gate_effort or {} for arm in arms]
    rows = _descriptive_rows(columns, [metric.id for metric in GATE_HEADLINE])
    return [
        "",
        f"## Gate effort ({report.gate_scorer or GATE_EFFORT_REF}, per arm)",
        "",
        "Mean [CI] n over valid trials; an arm without the commit gate has no value "
        "(not applicable), never 0; an arm with a shadow gate has its first-run values only; "
        "a step with no source change is not applicable to first-pass clean. The tests check "
        "and fix_round_correctness_delta (final minus pre-fix case pass rate over steps with a "
        "fix round; not applicable to a shadow gate) are reported; a failed tests check counts "
        "once in every blocking count. Blocking counts that compare across gate "
        "modes: total_new_first_run_blocking (what each step's own work added before any "
        "fix), final_step_end_blocking (the tree the last step handed on); a shadow gate's "
        "total_first_run_blocking re-counts the accumulated tree at every step. The mypy "
        "no-untyped-call cascade is reported apart, and failing tests and missed coverage as "
        "separate first-run outcomes; first-pass clean rates ignore the "
        "auto-fixable layout codes (FORMAT, I001, W291-W293), the tests check (TEST.*), or "
        "both, and the cross-mode counts are also given without the no-untyped-call cascade "
        "(*_ex_cascade) and with a refused suppression comment "
        "counted once (mypy's unused-ignore at a SUPPRESS line folded into it; *_net). "
        "guardrail-gate's autofix step is reported (passes, errors, files and lines "
        "changed, block-tier findings fixed), with the counts after autofix and before it "
        "(*_post_autofix_net, *_raw_net: an enforced gate's runs see the fixed tree, a shadow "
        "gate's check the raw one, so the arms compare like for like) and the fix sessions' "
        "cost and wall time by the kind of refusal that started them; the autofix lines and "
        "block-tier findings each step's own work added "
        "(total_new_autofix_*: the step's first pass minus what the previous step left for "
        "autofix) compare across gate modes where total_autofix_* do not.",
        "",
        *_table(("Metric", "Better", *names), rows),
        *_legend(row[0] for row in rows),
        "Blocking findings per trial by rule family (mean first run / mean over every run):",
        "",
        *_table(
            ("Family", *names),
            [*(_family_row(family, arms) for family in FAMILIES), *_cascade_row(arms)],
        ),
        *_autofix_family_table(arms, names),
        *_refusal_table(arms, names),
    ]


def _family_cell(arm: ArmSummary, metrics: Sequence[str], family: str) -> str:
    return " / ".join(_num(_gate_mean(arm, f"{metric}.{family}")) for metric in metrics)


def _autofix_family_table(arms: Sequence[ArmSummary], names: Sequence[str]) -> list[str]:
    """gate-effort: block-tier findings the autofix passes fixed, per family (and new)."""
    metric = f"{GATE_PREFIX}total_autofix_removed_blocking"
    if not any(metric in (arm.gate_effort or {}) for arm in arms):
        return []
    new = f"{GATE_PREFIX}total_new_autofix_removed_blocking"
    with_new = any(new in (arm.gate_effort or {}) for arm in arms)
    metrics = (metric, new) if with_new else (metric,)
    rows = [[family, *(_family_cell(arm, metrics, family) for arm in arms)] for family in FAMILIES]
    title = (
        "Block-tier findings the autofix passes fixed per trial by rule family (mean summed "
        "over the passes † / mean each step's own work added, cross-mode):"
        if with_new
        else "Block-tier findings the autofix passes fixed per trial by rule family (mean):"
    )
    return [title, "", *_table(("Family", *names), rows)]


def _refusal_table(arms: Sequence[ArmSummary], names: Sequence[str]) -> list[str]:
    """gate-effort: fix-session cost and wall time by the kind of refusal that started it."""
    cost, wall = (
        f"{GATE_PREFIX}total_fix_cost_usd_by_refusal",
        f"{GATE_PREFIX}total_fix_wall_s_by_refusal",
    )
    if not any(f"{cost}.mixed" in (arm.gate_effort or {}) for arm in arms):
        return []
    rows = [
        [
            kind,
            *(
                f"{_num(_gate_mean(arm, f'{cost}.{kind}'))} / "
                f"{_num(_gate_mean(arm, f'{wall}.{kind}'))}"
                for arm in arms
            ),
        ]
        for kind in REFUSAL_KINDS
    ]
    return [
        "Fix sessions per trial by the refusal that started them (mean cost USD / wall s; "
        "coverage_minimum: TEST.coverage-minimum was the only blocking code):",
        "",
        *_table(("Refusal", *names), rows),
    ]


def _sensitivity_notes(report: AnalysisReport) -> list[str]:
    """The known verifier sensitivities of the report's problems, one line each."""
    notes = [
        f"- `{note['case']}` ({ref}, {note['kind']}, spec-determined: {note['spec_determined']})"
        f": {note['mechanism']}"
        for ref, found in (report.verifier_sensitivities or {}).items()
        for note in found
    ]
    if not notes:
        return []
    heading = "Known verifier sensitivities (not errata; the specification determines them):"
    return [heading, "", *notes, ""]


def _verifier_arms(report: AnalysisReport) -> list[str]:
    """Grading-vs-coverage disagreements per arm, and the known sensitivities beside them."""
    arms = [arm for arm in report.arms if arm.verifier_diagnostics is not None]
    if not arms:
        return []
    columns = [arm.verifier_diagnostics or {} for arm in arms]
    rows = _descriptive_rows(columns, [VERIFIER_DISAGREEMENTS])
    return [
        "",
        "## Verifier sensitivity (diagnostic; never an outcome)",
        "",
        "Grader cases whose outcome differed between the grading run and the coverage run, "
        "per trial (code-quality@3 regression_parity_excluded_cases, summed). Correctness "
        "and the solve rates read the grading run's verdict; G leaves such cases out.",
        "",
        *_table(("Metric", "Better", *(arm.arm for arm in arms)), rows),
        *_sensitivity_notes(report),
    ]


def _process_arms(report: AnalysisReport) -> list[str]:
    arms = [arm for arm in report.arms if arm.process_outcomes is not None]
    if not arms:
        return []
    columns = [arm.process_outcomes or {} for arm in arms]
    rows = _descriptive_rows(columns, sorted({metric for column in columns for metric in column}))
    return [
        "",
        f"## Process outcomes ({report.process_scorer or PROCESS_COMPLIANCE_REF}, per arm)",
        "",
        "Mean [CI] n over valid trials; stall_rate is the share of reached steps in which a "
        "session changed nothing and ended asking a question or refused without an edit "
        "(only each step's first session counts); an arm "
        "without the stall wrapper has no value (not applicable), never 0. "
        "A test command counts as a test run only when its output does "
        "not say its runner was not found (total_test_commands_not_run counts those). "
        "work_session_tests_run_rate is the share of steps whose work session "
        "itself ran tests after its last edit (a gate's fix sessions do not count); "
        "plan_written counts in compliance_rate only when the condition asks for a plan. "
        "work_session_tests_run_any_rate is the share whose work session ran the "
        "tests at all and work_session_tests_after_last_code_edit_rate the share that ran them "
        "after its last edit to a non-test file.",
        "",
        *_table(("Metric", "Better", *[arm.arm for arm in arms]), rows),
    ]


def _process_effects(comparison: Comparison) -> list[str]:
    if comparison.process_note:
        return ["### Process outcomes (paired)", "", comparison.process_note + ".", ""]
    if not comparison.process_effects:
        return []
    header = (
        "Metric", "Better", "View", "Problem", "n", "Baseline", "Treatment", "Δ", "CI",
        "Verdict", "Excluded", "Cost Δ USD",
    )  # fmt: skip
    return [
        "### Process outcomes (paired)",
        "",
        *_table(header, (_effect_row(effect) for effect in comparison.process_effects)),
    ]


def _gate_effects(comparison: Comparison) -> list[str]:
    if comparison.gate_note:
        return ["### Gate effort (paired)", "", comparison.gate_note + ".", ""]
    if not comparison.gate_effects:
        return []
    header = (
        "Metric", "Better", "View", "Problem", "n", "Baseline", "Treatment", "Δ", "CI",
        "Verdict", "Excluded", "Cost Δ USD",
    )  # fmt: skip
    return [
        "### Gate effort (paired)",
        "",
        *_table(header, (_effect_row(effect) for effect in comparison.gate_effects)),
        *_legend(effect.metric for effect in comparison.gate_effects),
    ]


def _mcnemar(effects: Sequence[Effect]) -> list[str]:
    rows = [
        [
            effect.metric,
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
    header = ("Outcome", "Problem", "Both", "Baseline only", "Treatment only", "Neither", "Exact p")
    return ["### Final checkpoint solved (paired 2×2, exact McNemar)", "", *_table(header, rows)]


def _headline_effect(label: str, effect: Effect) -> str:
    return (
        f"**{label}** `{effect.metric}` ({effect.problem}): "
        f"Δ {_num(effect.mean_delta)} {_ci(effect.ci)} "
        f"→ **{effect.verdict.value}** ({effect.verdict_reason}); "
        f"cost Δ {_num(effect.cost.delta_mean)} USD per trial."
    )


def _primary_lines(comparison: Comparison) -> list[str]:
    lines = [_headline_effect("Primary outcome", comparison.primary)]
    if comparison.primary_raw is not None:
        lines += ["", _headline_effect("Raw (beside the primary)", comparison.primary_raw)]
    return lines


def _comparison(comparison: Comparison, confidence: float) -> list[str]:
    header = (
        "Metric", "Better", "View", "Problem", "n", "Baseline", "Treatment", "Δ",
        f"{confidence_label(confidence)} CI",
        "Verdict", "Excluded", "Cost Δ USD",
    )  # fmt: skip
    lines = [
        f"## {comparison.treatment} vs {comparison.baseline}",
        "",
        *_primary_lines(comparison),
        "",
        "### Effects",
        "",
        BETTER_LEGEND,
        "",
        *_table(header, (_effect_row(effect) for effect in comparison.effects)),
        *_mcnemar([e for e in comparison.effects if e.mcnemar is not None]),
        *_manipulation(comparison.manipulation),
        *_gate_effects(comparison),
        *_process_effects(comparison),
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


def _count(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


def _errata_lines(report: AnalysisReport) -> list[str]:
    """Which errata the ``*_errata`` outcomes apply; every other outcome is the raw grade."""
    if not report.errata:
        return []
    pinned = "; ".join(
        f"`{ref}` errata `{str(errata.get('sha256'))[:12]}` "
        f"({_count(errata.get('exclude_from_solved'))} exclude-from-solved cases)"
        for ref, errata in sorted(report.errata.items())
    )
    primary = str(report.primary_metric or "")
    which = (
        f"the primary (`{primary}`) is one of them and its raw counterpart is reported beside it"
        if primary.endswith("_errata")
        else "every other outcome, including the primary, is the raw SCB grade"
    )
    return [
        f"- Problem errata: {pinned}. Only the `*_errata` outcomes (errata-applied solve, "
        f"reached, final solved) use it; {which}"
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


def _ranking_row(row: RankingRow, reference_arm: str, gate_columns: Sequence[str]) -> list[str]:
    gate = row.gate_means or {}
    return [
        str(row.rank),
        row.arm + (" (reference)" if row.arm == reference_arm else ""),
        str(row.n),
        _num(row.mean),
        _ci(row.interval),
        *(_num(entry.value) for entry in row.pass_at_k),
        _num(row.cost_means.get("total_cost_usd")),
        _num(row.cost_means.get("total_tokens")),
        _num(row.cost_means.get("total_agent_wall_s")),
        *(_num(gate.get(column)) for column in gate_columns),
    ]


def _ranking(ranking: Ranking | None) -> list[str]:
    if ranking is None:
        return []
    ks = [entry.k for entry in ranking.rows[0].pass_at_k] if ranking.rows else []
    gated = any(row.gate_means is not None for row in ranking.rows)
    gate_columns = GATE_RANKING if gated else ()
    header = (
        "Rank", "Arm", "n", f"Mean {ranking.rank_by}", "95% CI",
        *(f"pass@{k}" for k in ks), "Mean cost USD", "Mean tokens", "Mean agent s",
        *gate_columns,
    )  # fmt: skip
    rows = [_ranking_row(row, ranking.reference_arm, gate_columns) for row in ranking.rows]
    return [
        "",
        "## Ranking (benchmark)",
        "",
        f"Ordered by `{ranking.rank_by}` ({ranking.direction}); pass@k counts "
        f"{ranking.pass_definition}. Paired verdicts against the reference agent follow.",
        "",
        *_table(header, rows),
    ]


def _pooling_lines(report: AnalysisReport) -> list[str]:
    pooling = report.pooling
    if pooling is None:
        return []
    lines = []
    if pooling.unmeasured:
        lines.append(
            "- Records integrity: **unmeasured** (no mechanics epoch, so no records manifest): "
            + ", ".join(pooling.unmeasured)
        )
    lines.extend(
        f"- **Pooling override `{finding.field}`:** {finding.message}"
        for finding in pooling.findings
    )
    if not pooling.timing_cost_comparable:
        lines.append(
            "- **Timing and cost are not comparable:** the jobs ran on different "
            "execution-target hardware; only correctness and quality pool."
        )
    return lines


def render_markdown(report: AnalysisReport) -> str:
    metric = report.metric
    lines = [f"# Analysis: {report.experiment}", ""]
    if not report.citable:
        lines += [
            f"> **Not citable:** a {report.experiment_kind} experiment checks operational "
            "health; its verdicts are not evidence about any treatment or agent.",
            "",
        ]
    if report.dev_job is not None:
        lines += [f"> **Development job, not arm evidence:** {report.dev_job.note}.", ""]
    lines += [
        f"- Kind: `{report.experiment_kind}`; lock: `{report.lock_sha256[:12]}`; "
        f"jobs: {', '.join(report.jobs)}",
        f"- Primary outcome: `{report.primary_metric}` ({report.primary_source}; progression "
        f"`{report.progression}`); baseline arm: `{report.baseline_arm}`",
        f"- Composite S: headline metric `{metric.get('ref')}` ({metric.get('selection')})",
        _settings_line(report),
    ]
    if report.replaced_trials:
        lines.append(f"- Replaced invalid trials: {', '.join(report.replaced_trials)}")
    lines.extend(_errata_lines(report))
    lines.extend(_pooling_lines(report))
    header = (
        "Arm", "Planned", "Run", "Valid", "Infra error", "Not run",
        "Mean cost USD", "Mean tokens", "Mean agent s",
    )  # fmt: skip
    lines += ["", "## Arms", "", *_table(header, (_arm_row(arm) for arm in report.arms))]
    lines.extend(_gate_arms(report))
    lines.extend(_process_arms(report))
    lines.extend(_verifier_arms(report))
    lines.extend(_ranking(report.ranking))
    for comparison in report.comparisons:
        lines.extend(_comparison(comparison, report.settings.confidence))
    lines.extend(_sample_size(report))
    return "\n".join(lines).rstrip() + "\n"
