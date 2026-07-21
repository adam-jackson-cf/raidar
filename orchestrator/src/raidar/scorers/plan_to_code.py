"""Plan-to-code scorer definition."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from raidar.schemas.scorecard import MetricScore
from raidar.scorers.base import BaseScorer, ScorerContext, ScorerEvidence, register_scorer
from raidar.scorers.common import (
    functional_score_from_evidence,
    metric,
    valid_changed_file_paths,
    verification_stability_from_evidence,
)
from raidar.scorers.llm_as_judge import evaluate_llm_as_judge_metric
from raidar.scorers.verification import (
    EvidenceDependency,
    VerificationPlan,
    VerificationPlanContext,
    scenario_command_plan,
)


@register_scorer(scorer_id="plan-to-code", version=1)
class PlanToCode(BaseScorer):
    """Plan-to-code scorer promoted to an active code-backed definition."""

    status = "active"
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
    evidence_dependencies = (EvidenceDependency(kind="command"),)

    def verification_plan(self, context: VerificationPlanContext) -> VerificationPlan:
        """Declare scenario verification commands owned by this scorer."""

        return scenario_command_plan(self.id, context)

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        evidence = context.verification_evidence.for_owner(self.id)
        functional = functional_score_from_evidence(evidence)
        plan_packet = _retained_plan_packet(context)
        deterministic_scores = (
            _planned_scope_coverage_score(plan_packet),
            _acceptance_evidence_completeness_score(plan_packet),
            functional,
            verification_stability_from_evidence(evidence),
        )
        plan_adherence = evaluate_llm_as_judge_metric(
            workspace=context.workspace,
            scenario_dir=context.scenario_dir,
            scenario=context.scenario,
            metric_id="plan-adherence",
            judge_path="judges/plan-judge.toml",
            deterministic_metric_scores=deterministic_scores,
            retained_evidence=plan_packet,
            changed_surfaces=_changed_surfaces(context.workspace_changes),
            executor=getattr(context, "judge_executor", None),
        )
        if not functional.passed:
            plan_adherence = plan_adherence.model_copy(
                update={
                    "score": min(plan_adherence.score, functional.score),
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
                "direct: authoritative plan feature dashboard evidence, "
                f"features={feature_count}, passed={passed_features}"
            ),
        )
    return MetricScore(
        metric_id="planned-scope-coverage",
        score=0.0,
        passed=False,
        missing_patterns=["authoritative plan feature evidence"],
        evidence="missing authoritative plan feature evidence",
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
                "direct: authoritative acceptance tracker evidence, "
                f"acceptance={acceptance_count}, passed={passed_acceptance}"
            ),
        )
    return MetricScore(
        metric_id="acceptance-evidence-completeness",
        score=0.0,
        passed=False,
        missing_patterns=["authoritative acceptance tracker evidence"],
        evidence="missing authoritative acceptance tracker evidence",
    )


def _retained_plan_packet(context: ScorerContext) -> dict[str, Any]:
    try:
        scenario_root = Path(context.scenario_dir).resolve(strict=True)
    except OSError:
        return {}
    plan_path = _authoritative_plan_path(scenario_root)
    if plan_path is None:
        return {}
    try:
        content = plan_path.read_bytes()
        text = content.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return {}

    plan_sha256 = hashlib.sha256(content).hexdigest()
    if plan_path.parent.name != plan_sha256:
        return {}

    feature_evidence = _table_evidence_counts(text, "Feature Dashboard", prefix="feature")
    acceptance_evidence = _table_evidence_counts(text, "Acceptance Tracker", prefix="acceptance")
    if not _is_valid_plan_packet(feature_evidence, acceptance_evidence):
        return {}

    return {
        "path": str(plan_path.relative_to(scenario_root)),
        "sha256": plan_sha256,
        **feature_evidence,
        **acceptance_evidence,
    }


def _authoritative_plan_path(scenario_dir: Path) -> Path | None:
    try:
        scenario_root = scenario_dir.resolve(strict=True)
    except OSError:
        return None

    plan_root = scenario_root / ".enaible" / "intent-plan"
    try:
        candidates = sorted(
            path
            for path in plan_root.glob("[0-9a-f]" * 64 + "/intentplan.md")
            if path.is_file() and path.resolve(strict=True).is_relative_to(scenario_root)
        )
    except OSError:
        return None
    return candidates[0] if len(candidates) == 1 else None


def _is_valid_plan_packet(
    feature_evidence: dict[str, int], acceptance_evidence: dict[str, int]
) -> bool:
    return bool(feature_evidence["feature_count"] and acceptance_evidence["acceptance_count"])


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
    separator = _split_markdown_row(lines[1])
    if (
        not headers
        or len(headers) != len(separator)
        or any(not header for header in headers)
        or any(not re.fullmatch(r":?-{3,}:?", cell) for cell in separator)
    ):
        return []
    data_rows = [_split_markdown_row(line) for line in lines[2:]]
    if not data_rows or any(len(row) != len(headers) for row in data_rows):
        return []
    return [dict(zip(headers, row, strict=True)) for row in data_rows]


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
