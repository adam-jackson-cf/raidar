"""Plan-to-code scorer definition."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from raidar.schemas.scorecard import MetricScore
from raidar.scorers.base import BaseScorer, ScorerContext, ScorerEvidence, register_scorer
from raidar.scorers.common import metric, valid_changed_file_paths, verification_stability_score
from raidar.scorers.llm_as_judge import evaluate_llm_as_judge_metric


@register_scorer(scorer_id="plan-to-code", version=1)
class PlanToCode(BaseScorer):
    """Plan-to-code scorer retained as a proposed code-backed definition."""

    status = "proposed"
    category = "quality"
    description = (
        "Scores implementation against an approved plan, including plan adherence "
        "and retained acceptance evidence."
    )
    metrics = (
        metric(
            "plan-adherence",
            "llm-as-judge",
            0.35,
            evidence=(
                "Approved plan, implementation diff summary, acceptance tracker, decision "
                "log, verification results, and deterministic metric summaries."
            ),
            score_derivation=(
                "Judge scores how faithfully implementation follows the approved plan "
                "with required citations to plan rows and changed surfaces."
            ),
            pass_fail=(
                "Passes when the judge finds planned outcomes delivered and deviations justified."
            ),
            config={"judge": "judges/plan-judge.toml"},
        ),
        metric(
            "planned-scope-coverage",
            "core",
            0.25,
            evidence=(
                "Plan feature rows, changed surfaces, completed acceptance rows, and "
                "evidence references."
            ),
            score_derivation=(
                "Scores completed planned features divided by approved planned features, "
                "with missing evidence incomplete."
            ),
            pass_fail="Passes when every planned feature has retained implementation evidence.",
        ),
        metric(
            "acceptance-evidence-completeness",
            "core",
            0.20,
            evidence=(
                "Acceptance tracker statuses, initial failures, passing evidence, and "
                "command outputs."
            ),
            score_derivation=(
                "Scores acceptance rows with valid passing evidence divided by planned "
                "acceptance rows."
            ),
            pass_fail="Passes when all acceptance criteria have retained passing evidence.",
        ),
        metric(
            "functional",
            "core",
            0.10,
            evidence="Build, test, and gate execution outcomes for the submitted code.",
            score_derivation="Uses the functional score computed from execution outputs.",
            pass_fail="Passes when functional execution passed.",
        ),
        metric(
            "verification-stability",
            "core",
            0.10,
            evidence="Verification gate failure count across the run.",
            score_derivation="Uses the verification stability score computed from gate history.",
            pass_fail="Passes when verification stability is greater than zero.",
        ),
    )

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        outputs = context.execution.outputs
        plan_packet = _retained_plan_packet(context)
        deterministic_scores = (
            _planned_scope_coverage_score(plan_packet),
            _acceptance_evidence_completeness_score(plan_packet),
            MetricScore(
                metric_id="functional",
                score=outputs.functional.score,
                passed=outputs.functional.passed,
                evidence=(
                    "direct: functional execution summary, "
                    f"build={outputs.functional.build_succeeded}, "
                    f"tests={outputs.functional.tests_passed}/{outputs.functional.tests_total}"
                ),
            ),
            verification_stability_score(outputs.verification_stability),
        )
        plan_adherence = evaluate_llm_as_judge_metric(
            workspace=context.workspace,
            scenario_dir=context.scenario_dir,
            scenario=context.scenario,
            metric_id="plan-adherence",
            judge_path="judges/plan-judge.toml",
            execution_outputs=outputs,
            deterministic_metric_scores=deterministic_scores,
            retained_evidence=plan_packet,
            changed_surfaces=_changed_surfaces(context.workspace_changes),
        )
        if not outputs.functional.passed:
            plan_adherence = plan_adherence.model_copy(
                update={
                    "score": min(plan_adherence.score, outputs.functional.score),
                    "passed": False,
                    "evidence": (
                        f"{plan_adherence.evidence}; deterministic functional execution failed"
                    ),
                }
            )
        return ScorerEvidence(
            metric_scores=(
                plan_adherence,
                *deterministic_scores,
            )
        )


def _planned_scope_coverage_score(plan_packet: dict[str, Any]) -> MetricScore:
    feature_count = int(plan_packet.get("feature_count", 0))
    passed_features = int(plan_packet.get("passed_feature_count", 0))
    if feature_count:
        score = passed_features / feature_count
        return MetricScore(
            metric_id="planned-scope-coverage",
            score=round(score, 3),
            passed=score >= 1.0,
            evidence=(
                "direct: retained plan feature dashboard evidence, "
                f"features={feature_count}, passed={passed_features}"
            ),
        )
    return MetricScore(
        metric_id="planned-scope-coverage",
        score=0.0,
        passed=False,
        missing_patterns=["retained plan packet feature evidence"],
        evidence=(
            "proxy: retained plan packet artifacts are not present in runtime context; "
            "missing evidence is incomplete rather than passing"
        ),
    )


def _acceptance_evidence_completeness_score(plan_packet: dict[str, Any]) -> MetricScore:
    acceptance_count = int(plan_packet.get("acceptance_count", 0))
    passed_acceptance = int(plan_packet.get("passed_acceptance_count", 0))
    if acceptance_count:
        score = passed_acceptance / acceptance_count
        return MetricScore(
            metric_id="acceptance-evidence-completeness",
            score=round(score, 3),
            passed=score >= 1.0,
            evidence=(
                "direct: retained acceptance tracker evidence, "
                f"acceptance={acceptance_count}, passed={passed_acceptance}"
            ),
        )
    return MetricScore(
        metric_id="acceptance-evidence-completeness",
        score=0.0,
        passed=False,
        missing_patterns=["retained acceptance tracker evidence"],
        evidence=(
            "proxy: retained acceptance tracker evidence is not present in runtime context; "
            "missing evidence is incomplete rather than passing"
        ),
    )


def _retained_plan_packet(context: ScorerContext) -> dict[str, Any]:
    plan_path = _retained_plan_path(Path(context.scenario_dir), Path(context.workspace))
    if plan_path is None:
        return {}
    text = plan_path.read_text(encoding="utf-8", errors="ignore")
    return {
        "path": str(plan_path),
        **_table_evidence_counts(text, "Feature Dashboard", prefix="feature"),
        **_table_evidence_counts(text, "Acceptance Tracker", prefix="acceptance"),
    }


def _retained_plan_path(scenario_dir: Path, workspace: Path) -> Path | None:
    candidates = [
        *scenario_dir.glob(".enaible/intent-plan/*/intentplan.md"),
        *workspace.glob(".enaible/intent-plan/*/intentplan.md"),
        scenario_dir / "intentplan.md",
        workspace / "intentplan.md",
    ]
    return next((path for path in candidates if path.is_file()), None)


def _table_evidence_counts(text: str, heading: str, *, prefix: str) -> dict[str, int]:
    rows = _table_rows(text, heading)
    passed_with_evidence = [
        row
        for row in rows
        if _cell_value(row, "status").lower() == "passed" and _row_has_evidence(row)
    ]
    return {
        f"{prefix}_count": len(rows),
        f"passed_{prefix}_count": len(passed_with_evidence),
    }


def _table_rows(text: str, heading: str) -> list[dict[str, str]]:
    section = _markdown_section(text, heading)
    lines = [line for line in section.splitlines() if line.startswith("|")]
    if len(lines) < 3:
        return []
    headers = _split_markdown_row(lines[0])
    data_rows = [line for line in lines[2:] if "---" not in line]
    return [
        dict(zip(headers, _split_markdown_row(row), strict=False))
        for row in data_rows
        if _split_markdown_row(row)
    ]


def _split_markdown_row(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _cell_value(row: dict[str, str], wanted: str) -> str:
    for key, value in row.items():
        if key.strip().lower() == wanted:
            return value.strip()
    return ""


def _row_has_evidence(row: dict[str, str]) -> bool:
    evidence_values = [
        value
        for key, value in row.items()
        if any(term in key.lower() for term in ("evidence", "reference", "command", "surface"))
    ]
    return any(_is_valid_evidence_value(value) for value in evidence_values)


def _is_valid_evidence_value(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"", "-", "0", "false", "no", "n/a", "none", "tbd"}:
        return False
    if normalized.isdigit():
        return int(normalized) > 0
    return True


def _markdown_section(text: str, heading: str) -> str:
    pattern = rf"## {re.escape(heading)}\n(?P<section>.*?)(?:\n## |\Z)"
    match = re.search(pattern, text, re.DOTALL)
    return match.group("section") if match else ""


def _changed_surfaces(workspace_changes: dict[str, Any]) -> list[str]:
    return [path.as_posix() for path in valid_changed_file_paths(workspace_changes)[:50]]
