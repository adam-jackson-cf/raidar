"""LLM-as-judge metric evaluation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from raidar.codex_auth import resolve_codex_auth
from raidar.config import settings
from raidar.sanitization import sanitize_evidence_text
from raidar.schemas.scenario import ScenarioDefinition
from raidar.schemas.scorecard import MetricScore
from raidar.scorers.base import JudgeExecutor
from raidar.scorers.deterministic import source_file_paths
from raidar.scorers.paths import resolve_scorer_definition_file


class JudgeEvaluationError(RuntimeError):
    """Raised when a judge metric cannot produce valid structured analysis."""


class JudgeMetricOutput(BaseModel):
    """Strict structured output required from every judge-backed metric."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    passed: bool
    score: float = Field(ge=0, le=1)
    verdict: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    findings: list[dict[str, Any]]
    rubric_coverage: dict[str, Any]
    residual_risk: list[Any]


def evaluate_llm_as_judge_metric(
    *,
    workspace: Path,
    scenario_dir: Path,
    scenario: ScenarioDefinition,
    metric_id: str,
    judge_path: str,
    deterministic_metric_scores: tuple[MetricScore, ...] = (),
    retained_evidence: dict[str, Any] | None = None,
    changed_surfaces: list[str] | None = None,
    executor: JudgeExecutor | None = None,
) -> MetricScore:
    """Evaluate a run with the scorer-provided judge role file."""

    try:
        resolved_judge_path = resolve_scorer_definition_file(
            judge_path,
            field_name="llm-as-judge.config.judge",
        )
    except (FileNotFoundError, ValueError) as exc:
        raise JudgeEvaluationError(f"Judge definition unavailable: {exc}") from exc
    if executor is None:
        raise JudgeEvaluationError("Judge-backed metric requires an explicit judge executor")

    judge_role = resolved_judge_path.read_text(encoding="utf-8")
    prompt = _judge_prompt(
        workspace=workspace,
        scenario_dir=scenario_dir,
        scenario=scenario,
        deterministic_metric_scores=deterministic_metric_scores,
        retained_evidence=retained_evidence or {},
        changed_surfaces=changed_surfaces or [],
    )
    try:
        response = executor(judge_role, prompt)
    except Exception as exc:
        raise JudgeEvaluationError(f"Judge execution failed: {type(exc).__name__}: {exc}") from exc
    return _metric_score_from_response(response, metric_id=metric_id)


def codex_judge_executor(image_name: str) -> JudgeExecutor:
    """Bind judge execution to the prepared task image for network parity."""

    if not image_name.strip():
        raise JudgeEvaluationError("Prepared task image is unavailable for judge execution")

    def execute(judge_role: str, prompt: str) -> str:
        return _call_codex_judge(
            image_name=image_name,
            judge_role=judge_role,
            prompt=prompt,
        )

    return execute


def _call_codex_judge(*, image_name: str, judge_role: str, prompt: str) -> str:
    auth = resolve_codex_auth(requested_mode=settings.llm_as_judge.codex_auth_mode)
    if auth.resolved_mode != "chatgpt" or auth.auth_json_path is None:
        raise OSError("LLM judge requires Codex ChatGPT auth.")

    instruction = "\n\n".join((judge_role, prompt))
    completed = subprocess.run(
        _codex_judge_command(image_name=image_name, auth_path=auth.auth_json_path),
        input=instruction,
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        timeout=settings.timeouts.command_default,
        check=False,
    )
    if completed.returncode != 0:
        output = _clip_output(f"{completed.stdout}\n{completed.stderr}")
        raise RuntimeError(f"Codex judge failed with exit {completed.returncode}: {output}")
    return _codex_response_text(completed.stdout)


def _codex_judge_command(*, image_name: str, auth_path: Path) -> list[str]:
    command = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--volume",
        f"{auth_path}:/tmp/codex-home/auth.json:ro",
        "--env",
        "CODEX_HOME=/tmp/codex-home",
        image_name,
        "codex",
        "exec",
        "--ignore-user-config",
        "--ephemeral",
        "--disable",
        "plugins",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--json",
        "--model",
        settings.llm_as_judge.model,
    ]
    reasoning_effort = settings.llm_as_judge.reasoning_effort.strip()
    if reasoning_effort:
        command.extend(["-c", f"model_reasoning_effort={reasoning_effort}"])
    command.append("-")
    return command


def _codex_response_text(stdout: str) -> str:
    fallback_lines: list[str] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            if line.strip():
                fallback_lines.append(line)
            continue
        text = _codex_event_text(event)
        if text:
            fallback_lines.append(text)
    if fallback_lines:
        return "\n".join(fallback_lines).strip()
    return stdout.strip()


def _codex_event_text(event: dict[str, Any]) -> str:
    item = event.get("item")
    if isinstance(item, dict):
        if item.get("type") == "agent_message":
            text = item.get("text")
            return text if isinstance(text, str) else ""
        if item.get("type") == "message":
            return _content_text(item.get("content"))
    if event.get("type") in {"message", "agent_message"}:
        return _content_text(event.get("content") or event.get("text"))
    return ""


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text") or item.get("content")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def _clip_output(output: str, *, max_chars: int = 1000) -> str:
    return sanitize_evidence_text(output, max_chars=max_chars)


def _metric_score_from_response(response: str, *, metric_id: str) -> MetricScore:
    parsed_json = _parse_json_response(response)
    if parsed_json is None:
        raise JudgeEvaluationError("Judge response was not a JSON object")
    sanitized_json = _sanitize_json(parsed_json)
    try:
        output = JudgeMetricOutput.model_validate(sanitized_json)
    except ValidationError as exc:
        raise JudgeEvaluationError(f"Judge response violated the output contract: {exc}") from exc
    output_payload = output.model_dump(mode="json")
    return MetricScore(
        metric_id=metric_id,
        score=round(output.score, 3),
        passed=output.passed,
        evidence=output.evidence,
        judge_output=output_payload,
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


def _judge_prompt(
    *,
    workspace: Path,
    scenario_dir: Path,
    scenario: ScenarioDefinition,
    deterministic_metric_scores: tuple[MetricScore, ...] = (),
    retained_evidence: dict[str, Any] | None = None,
    changed_surfaces: list[str] | None = None,
) -> str:
    return "\n\n".join(
        part
        for part in (
            _scenario_context(scenario),
            _task_prompt(scenario_dir, scenario),
            _workspace_sources(workspace, scenario=scenario),
            _structured_judge_inputs(
                scenario,
                deterministic_metric_scores=deterministic_metric_scores,
                retained_evidence=retained_evidence or {},
                changed_surfaces=changed_surfaces or [],
            ),
            _output_contract(),
        )
        if part
    )


def _scenario_context(scenario: ScenarioDefinition) -> str:
    requirements = [
        f"- {requirement.id}: {requirement.description}"
        for requirement in scenario.requirements.items
    ]
    return (
        f"Scenario: {scenario.name}@{scenario.scenario_revision}\n"
        f"Description: {scenario.description}\n"
        f"Requirements:\n{chr(10).join(requirements) or '- No explicit requirements configured.'}"
    )


def _task_prompt(scenario_dir: Path, scenario: ScenarioDefinition) -> str:
    prompt_path = scenario_dir / scenario.prompt.entry
    if not prompt_path.is_file():
        return ""
    prompt_text = prompt_path.read_text(encoding="utf-8")
    return f"Task prompt:\n{sanitize_evidence_text(prompt_text, max_chars=4000)}"


def _workspace_sources(workspace: Path, *, scenario: ScenarioDefinition) -> str:
    chunks: list[str] = []
    budget = settings.llm_as_judge.max_source_chars
    used = 0
    for source_path in _source_paths(workspace, scenario=scenario):
        if used >= budget:
            break
        try:
            content = source_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        remaining = budget - used
        clipped = content[:remaining]
        used += len(clipped)
        sanitized = sanitize_evidence_text(clipped, max_chars=remaining)
        chunks.append(f"File: {source_path.relative_to(workspace)}\n```text\n{sanitized}\n```")
    return "Submitted workspace sources:\n\n" + "\n\n".join(chunks)


def _source_paths(workspace: Path, *, scenario: ScenarioDefinition) -> list[Path]:
    paths = {
        str(path.relative_to(workspace)): path
        for path in source_file_paths(workspace, tuple(scenario.languages))
    }
    for pattern in _scenario_source_patterns(scenario):
        for path in workspace.glob(pattern):
            if path.is_file() and "node_modules" not in path.parts:
                paths[str(path.relative_to(workspace))] = path
    return [paths[key] for key in sorted(paths)]


def _scenario_source_patterns(scenario: ScenarioDefinition) -> tuple[str, ...]:
    patterns: list[str] = []
    for scorer_ref in scenario.scorers:
        artifact_config = scorer_ref.config.get("artifact-checks", {})
        required_paths = artifact_config.get("required_paths", [])
        if isinstance(required_paths, list):
            patterns.extend(path for path in required_paths if isinstance(path, str))
    for requirement in scenario.requirements.items:
        check = requirement.check
        if check.type == "file_exists" and _looks_like_workspace_path(check.pattern):
            patterns.append(check.pattern)
    return tuple(dict.fromkeys(patterns))


def _looks_like_workspace_path(value: str) -> bool:
    return "/" in value and not value.startswith(("/", "../")) and ".." not in Path(value).parts


def _structured_judge_inputs(
    scenario: ScenarioDefinition,
    *,
    deterministic_metric_scores: tuple[MetricScore, ...],
    retained_evidence: dict[str, Any],
    changed_surfaces: list[str],
) -> str:
    metric_summaries = [
        {
            "metric_id": score.metric_id,
            "score": score.score,
            "passed": score.passed,
            "evidence": score.evidence,
        }
        for score in deterministic_metric_scores
    ]
    payload = {
        "scenario_id": f"{scenario.name}@{scenario.scenario_revision}",
        "requirement_count": len(scenario.requirements.items),
        "configured_scorers": len(scenario.scorers),
        "changed_surfaces": changed_surfaces[:50],
        "retained_evidence": retained_evidence,
        "deterministic_metric_summaries": metric_summaries,
    }
    payload_text = json.dumps(_sanitize_json(payload), sort_keys=True)
    return f"Structured judge inputs:\n{payload_text}"


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_evidence_text(value, max_chars=1000)
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_json(entry) for key, entry in value.items()}
    return value


def _output_contract() -> str:
    return (
        "Return only JSON with keys: passed (boolean), score (number from 0 to 1), "
        "verdict (string), evidence (short string), findings (array), "
        "rubric_coverage (object), and residual_risk (array)."
    )
