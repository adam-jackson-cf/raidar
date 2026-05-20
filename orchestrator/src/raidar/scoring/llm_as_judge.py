"""LLM-as-judge metric evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from litellm import completion

from raidar.config import settings
from raidar.schemas.scenario import ScenarioDefinition
from raidar.schemas.scorecard import MetricScore
from raidar.scorers.paths import resolve_scorer_definition_file
from raidar.scoring.acceptance import parse_judge_response

SOURCE_PATTERNS = (
    "package.json",
    "src/**/*.ts",
    "src/**/*.tsx",
    "src/**/*.js",
    "src/**/*.jsx",
    "src/**/*.css",
    "app/**/*.ts",
    "app/**/*.tsx",
    "components/**/*.ts",
    "components/**/*.tsx",
)


def evaluate_llm_as_judge_metric(
    *,
    workspace: Path,
    scenario_dir: Path,
    scenario: ScenarioDefinition,
    metric_id: str,
    judge_path: str,
) -> MetricScore:
    """Evaluate a run with the scorer-provided judge role file."""

    try:
        resolved_judge_path = resolve_scorer_definition_file(
            judge_path,
            field_name="llm-as-judge.config.judge",
        )
    except (FileNotFoundError, ValueError) as exc:
        return MetricScore(
            metric_id=metric_id,
            score=0.0,
            passed=False,
            missing_patterns=[judge_path],
            evidence=str(exc),
        )

    judge_role = resolved_judge_path.read_text(encoding="utf-8")
    prompt = _judge_prompt(workspace=workspace, scenario_dir=scenario_dir, scenario=scenario)
    try:
        response = _call_judge(judge_role=judge_role, prompt=prompt)
    except Exception as exc:
        return MetricScore(
            metric_id=metric_id,
            score=0.0,
            passed=False,
            evidence=f"LLM judge failed: {type(exc).__name__}: {exc}",
        )
    return _metric_score_from_response(response, metric_id=metric_id)


def _call_judge(*, judge_role: str, prompt: str) -> str:
    response = completion(
        model=settings.llm_as_judge.model,
        messages=[
            {"role": "system", "content": judge_role},
            {"role": "user", "content": prompt},
        ],
        max_tokens=settings.llm_as_judge.max_tokens,
        temperature=0,
        num_retries=settings.llm_as_judge.max_retries,
    )
    content = response.choices[0].message.content
    if isinstance(content, str):
        return content
    return json.dumps(content)


def _metric_score_from_response(response: str, *, metric_id: str) -> MetricScore:
    parsed_json = _parse_json_response(response)
    if parsed_json is not None:
        score = _bounded_score(parsed_json.get("score"))
        passed = _bool_value(parsed_json.get("passed"), default=score >= 0.8)
        evidence = _evidence_from_json(parsed_json, fallback=response[:300])
        return MetricScore(
            metric_id=metric_id,
            score=score,
            passed=passed,
            evidence=evidence,
            judge_output=parsed_json,
        )

    parsed = parse_judge_response(response)
    return MetricScore(
        metric_id=metric_id,
        score=1.0 if parsed.passed else 0.0,
        passed=parsed.passed,
        evidence=parsed.evidence,
    )


def _parse_json_response(response: str) -> dict[str, Any] | None:
    text = response.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
            if text.startswith("json"):
                text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _bounded_score(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(1.0, numeric)), 3)


def _bool_value(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return default


def _evidence_from_json(parsed_json: dict[str, Any], *, fallback: str) -> str:
    for key in ("evidence", "summary", "verdict"):
        value = parsed_json.get(key)
        if isinstance(value, str) and value.strip():
            return value
    findings = parsed_json.get("findings")
    if isinstance(findings, list) and findings:
        return f"{len(findings)} judge finding(s)."
    return fallback


def _judge_prompt(
    *,
    workspace: Path,
    scenario_dir: Path,
    scenario: ScenarioDefinition,
) -> str:
    return "\n\n".join(
        part
        for part in (
            _scenario_context(scenario),
            _task_prompt(scenario_dir, scenario),
            _workspace_sources(workspace),
            _output_contract(),
        )
        if part
    )


def _scenario_context(scenario: ScenarioDefinition) -> str:
    requirements = "\n".join(
        f"- {requirement.id}: {requirement.description}"
        for requirement in scenario.acceptance.requirements
    )
    return (
        f"Scenario: {scenario.name}@{scenario.scenario_revision}\n"
        f"Description: {scenario.description}\n"
        f"Requirements:\n{requirements or '- No explicit requirements configured.'}"
    )


def _task_prompt(scenario_dir: Path, scenario: ScenarioDefinition) -> str:
    prompt_path = scenario_dir / scenario.prompt.entry
    if not prompt_path.is_file():
        return ""
    return f"Task prompt:\n{prompt_path.read_text(encoding='utf-8')}"


def _workspace_sources(workspace: Path) -> str:
    chunks: list[str] = []
    budget = settings.llm_as_judge.max_source_chars
    used = 0
    for source_path in _source_paths(workspace):
        if used >= budget:
            break
        try:
            content = source_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        remaining = budget - used
        clipped = content[:remaining]
        used += len(clipped)
        chunks.append(f"File: {source_path.relative_to(workspace)}\n```text\n{clipped}\n```")
    return "Submitted workspace sources:\n\n" + "\n\n".join(chunks)


def _source_paths(workspace: Path) -> list[Path]:
    paths: dict[str, Path] = {}
    for pattern in SOURCE_PATTERNS:
        for path in workspace.glob(pattern):
            if path.is_file() and "node_modules" not in path.parts:
                paths[str(path.relative_to(workspace))] = path
    return [paths[key] for key in sorted(paths)]


def _output_contract() -> str:
    return (
        "Return only JSON with keys: passed (boolean), score (number from 0 to 1), "
        "verdict (string), evidence (short string), findings (array), "
        "rubric_coverage (object), and residual_risk (array)."
    )
