"""LLM-as-judge metric evaluation."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from raidar.codex_auth import OPENAI_API_KEY_ENV, resolve_codex_auth
from raidar.config import settings
from raidar.schemas.scenario import ScenarioDefinition
from raidar.schemas.scorecard import MetricScore
from raidar.scorers.deterministic import parse_judge_response
from raidar.scorers.paths import resolve_scorer_definition_file

SOURCE_PATTERNS = (
    "package.json",
    "src/lib/**/*.ts",
    "src/lib/**/*.tsx",
    "src/test/**/*.ts",
    "src/test/**/*.tsx",
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
    return _call_codex_judge(judge_role=judge_role, prompt=prompt)


def _call_codex_judge(*, judge_role: str, prompt: str) -> str:
    auth = resolve_codex_auth(requested_mode=settings.llm_as_judge.codex_auth_mode)
    if auth.resolved_mode != "chatgpt" or auth.auth_json_path is None:
        raise OSError("LLM judge requires Codex ChatGPT auth.")

    instruction = "\n\n".join((judge_role, prompt))
    with tempfile.TemporaryDirectory(prefix="raidar-codex-judge-") as codex_home:
        codex_home_path = Path(codex_home)
        auth_target = codex_home_path / "auth.json"
        auth_target.write_bytes(auth.auth_json_path.read_bytes())
        auth_target.chmod(0o600)
        env = os.environ.copy()
        env["CODEX_HOME"] = str(codex_home_path)
        env.pop(OPENAI_API_KEY_ENV, None)
        completed = subprocess.run(
            _codex_judge_command(),
            input=instruction,
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            timeout=settings.timeouts.command_default,
            check=False,
            env=env,
        )
    if completed.returncode != 0:
        output = _clip_output(f"{completed.stdout}\n{completed.stderr}")
        raise RuntimeError(f"Codex judge failed with exit {completed.returncode}: {output}")
    return _codex_response_text(completed.stdout)


def _codex_judge_command() -> list[str]:
    command = [
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
    clipped = " ".join(output.split())
    if len(clipped) > max_chars:
        return clipped[: max_chars - 3] + "..."
    return clipped


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
            _workspace_sources(workspace, scenario=scenario),
            _output_contract(),
        )
        if part
    )


def _scenario_context(scenario: ScenarioDefinition) -> str:
    requirements = [
        f"- {requirement.id}: {requirement.description}"
        for requirement in scenario.acceptance.requirements
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
    return f"Task prompt:\n{prompt_path.read_text(encoding='utf-8')}"


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
        chunks.append(f"File: {source_path.relative_to(workspace)}\n```text\n{clipped}\n```")
    return "Submitted workspace sources:\n\n" + "\n\n".join(chunks)


def _source_paths(workspace: Path, *, scenario: ScenarioDefinition) -> list[Path]:
    paths: dict[str, Path] = {}
    for pattern in (*_scenario_source_patterns(scenario), *SOURCE_PATTERNS):
        for path in workspace.glob(pattern):
            if path.is_file() and "node_modules" not in path.parts:
                paths[str(path.relative_to(workspace))] = path
    return list(paths.values())


def _scenario_source_patterns(scenario: ScenarioDefinition) -> tuple[str, ...]:
    patterns: list[str] = []
    for scorer_ref in scenario.scorers:
        artifact_config = scorer_ref.config.get("artifact-checks", {})
        required_paths = artifact_config.get("required_paths", [])
        if isinstance(required_paths, list):
            patterns.extend(path for path in required_paths if isinstance(path, str))
    for requirement in scenario.acceptance.requirements:
        check = requirement.check
        if check.type == "file_exists" and _looks_like_workspace_path(check.pattern):
            patterns.append(check.pattern)
    return tuple(dict.fromkeys(patterns))


def _looks_like_workspace_path(value: str) -> bool:
    return "/" in value and not value.startswith(("/", "../")) and ".." not in Path(value).parts


def _output_contract() -> str:
    return (
        "Return only JSON with keys: passed (boolean), score (number from 0 to 1), "
        "verdict (string), evidence (short string), findings (array), "
        "rubric_coverage (object), and residual_risk (array)."
    )
