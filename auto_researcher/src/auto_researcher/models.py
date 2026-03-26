"""Typed objective, role, and loop state for autoresearch orchestration."""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from raidar.schemas.scenario import DeterministicCheck, LLMJudgeCriterion, RequirementSpec

ROLE_NAMES = ("designer", "critic", "planner", "executor", "reviewer", "governor")
DEFAULT_MUTATION_SURFACE = [
    "scenario.yaml",
    "prompt/",
    "rules/",
    "verifiers/",
    "tools/",
    "starter/",
]

ObjectiveStatus = Literal[
    "drafting_scenario",
    "awaiting_scenario_approval",
    "active",
    "completed",
    "blocked",
]
ResearchLoopStatus = Literal[
    "queued",
    "running",
    "review_pending",
    "iterating",
    "discarded",
    "promoted",
    "blocked",
    "completed",
]
LoopAction = Literal["iterate", "discard", "promote", "spawn_next", "stop"]
LoopExecutionMode = Literal["serial", "parallel"]


class RoleModelConfig(BaseModel):
    """Control-plane PI model assignment for one role."""

    provider: str = "openai-codex"
    model_id: str = "gpt-5.3-codex"


class ObjectiveInitRequest(BaseModel):
    """User-provided objective intake contract."""

    goal: str
    target_harness: str
    target_model: str
    objective_id: str | None = None
    approval_mode: Literal["scenario_only"] = "scenario_only"
    loop_execution_mode: LoopExecutionMode = "serial"
    max_revisions: int = 3
    max_parallel_loops: int = 3
    benchmark_repeats: int = 5
    benchmark_repeat_parallel: int = 1
    research_repeats: int = 3
    research_repeat_parallel: int = 1
    mutation_surface: list[str] = Field(default_factory=lambda: list(DEFAULT_MUTATION_SURFACE))
    role_models: dict[str, RoleModelConfig] = Field(default_factory=dict)


class ObjectiveState(BaseModel):
    """Persisted objective orchestration state."""

    objective_id: str
    created_at_utc: str
    updated_at_utc: str
    status: ObjectiveStatus
    goal: str
    target_harness: str
    target_model: str
    approval_mode: Literal["scenario_only"]
    loop_execution_mode: LoopExecutionMode
    max_revisions: int
    max_parallel_loops: int
    benchmark_repeats: int
    benchmark_repeat_parallel: int
    research_repeats: int
    research_repeat_parallel: int
    mutation_surface: list[str]
    scenario_slug: str | None = None
    scenario_name: str | None = None
    draft_scenario_ref: str | None = None
    scenario_ref: str | None = None
    frozen_metric_ids: list[str] = Field(default_factory=list)
    best_benchmark_ref: str | None = None
    role_models: dict[str, RoleModelConfig] = Field(default_factory=dict)
    revision_count: int = 0
    planner_round: int = 0
    latest_scenario_review_ref: str | None = None
    stop_reason: str | None = None


class ScenarioGateDesign(BaseModel):
    """Structured verification gate for drafted scenarios."""

    name: str
    command: list[str]


class StarterFileDesign(BaseModel):
    """One starter workspace file authored during scenario design."""

    path: str
    content: str


class ScenarioDesign(BaseModel):
    """Designer-authored scenario draft contract."""

    scenario_slug: str
    scenario_name: str
    description: str
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    category: str = "research"
    timeout_sec: int = 1800
    starter_root: str = "starter"
    prompt_entry: str = "prompt/task.md"
    prompt_text: str
    metric_ids: list[str]
    required_commands: list[list[str]] = Field(default_factory=list)
    gates: list[ScenarioGateDesign] = Field(default_factory=list)
    deterministic_checks: list[DeterministicCheck] = Field(default_factory=list)
    requirements: list[RequirementSpec] = Field(default_factory=list)
    llm_judge_rubric: list[LLMJudgeCriterion] = Field(default_factory=list)
    starter_files: list[StarterFileDesign] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_starter_files(self) -> ScenarioDesign:
        """Require a Bun starter package.json that can materialize a lockfile."""
        package_payload = _starter_package_payload(self.starter_files)
        _require_starter_dependencies(package_payload)
        self.required_commands = _ensure_prompt_commands(
            self.required_commands,
            self.prompt_text,
        )
        self.gates = _ensure_prompt_gates(self.gates, self.prompt_text)
        if not self.deterministic_checks:
            self.deterministic_checks = _derived_deterministic_checks(self.prompt_text)
        if not self.requirements:
            self.requirements = _derived_requirements(self.prompt_text)
        if not self.llm_judge_rubric:
            self.llm_judge_rubric = _derived_llm_judge_rubric(self.prompt_text)
        return self


def _starter_package_payload(starter_files: list[StarterFileDesign]) -> dict[str, object]:
    file_map = {PurePosixPath(item.path).as_posix(): item.content for item in starter_files}
    package_json = file_map.get("package.json")
    if package_json is None:
        raise ValueError("starter_files must include package.json at the starter root.")
    try:
        package_payload = json.loads(package_json)
    except json.JSONDecodeError as exc:
        raise ValueError("starter package.json must contain valid JSON.") from exc
    if not isinstance(package_payload, dict):
        raise ValueError("starter package.json must decode to a JSON object.")
    return package_payload


def _require_starter_dependencies(package_payload: dict[str, object]) -> None:
    dependency_sections = (
        "dependencies",
        "devDependencies",
        "optionalDependencies",
        "peerDependencies",
    )
    has_dependencies = any(
        isinstance(package_payload.get(section), dict) and bool(package_payload[section])
        for section in dependency_sections
    )
    if has_dependencies:
        return
    raise ValueError(
        "starter package.json must declare at least one dependency or devDependency "
        "so Bun can materialize bun.lock."
    )


def _ensure_prompt_commands(
    commands: list[list[str]],
    prompt_text: str,
) -> list[list[str]]:
    normalized = [list(command) for command in commands]
    start_command = ["bun", "run", "start"]
    if "bun run start" not in prompt_text or start_command in normalized:
        return normalized
    return [*normalized, start_command]


def _ensure_prompt_gates(
    gates: list[ScenarioGateDesign],
    prompt_text: str,
) -> list[ScenarioGateDesign]:
    normalized = [gate.model_copy(deep=True) for gate in gates]
    start_command = ["bun", "run", "start"]
    if "bun run start" not in prompt_text:
        return normalized
    if any(gate.command == start_command for gate in normalized):
        return normalized
    return [*normalized, ScenarioGateDesign(name="start", command=start_command)]


def _derived_deterministic_checks(prompt_text: str) -> list[DeterministicCheck]:
    checks = [
        DeterministicCheck(
            type="no_pattern",
            pattern="TODO",
            description="No TODO markers remain in production files",
        )
    ]
    if "Hello, Raidar!" in prompt_text:
        checks.append(
            DeterministicCheck(
                type="import_present",
                pattern="Hello, Raidar!",
                description="Greeting output literal is present in source",
            )
        )
    if "formatGreeting" in prompt_text:
        checks.append(
            DeterministicCheck(
                type="import_present",
                pattern="formatGreeting",
                description="Greeting formatter is present in source",
            )
        )
    return checks


def _derived_requirements(prompt_text: str) -> list[RequirementSpec]:
    requirements: list[RequirementSpec] = []
    if "Hello, Raidar!" in prompt_text:
        requirements.append(
            RequirementSpec(
                id="req-start-output",
                description="The starter app prints Hello, Raidar! for the smoke run.",
                check=DeterministicCheck(
                    type="import_present",
                    pattern="Hello, Raidar!",
                    description="Greeting output literal is present in source",
                ),
                required_test_patterns=["Raidar"],
            )
        )
    if "formatGreeting" in prompt_text:
        requirements.append(
            RequirementSpec(
                id="req-format-greeting",
                description="The scenario exports a reusable formatGreeting helper.",
                check=DeterministicCheck(
                    type="import_present",
                    pattern="formatGreeting",
                    description="formatGreeting helper is present in source",
                ),
                required_test_patterns=["formatGreeting", "Raidar", "Smoke"],
            )
        )
    return requirements


def _derived_llm_judge_rubric(prompt_text: str) -> list[LLMJudgeCriterion]:
    criteria = [
        LLMJudgeCriterion(
            criterion="The implementation must satisfy every explicit requirement in the prompt.",
            weight=0.5,
        )
    ]
    acceptance_lines = _prompt_section_lines(prompt_text, "Acceptance criteria")
    if acceptance_lines:
        weight = round(0.5 / len(acceptance_lines), 3)
        criteria.extend(
            LLMJudgeCriterion(
                criterion=f"The implementation must satisfy this acceptance criterion: {line}",
                weight=weight,
            )
            for line in acceptance_lines
        )
        return criteria
    if "Hello, Raidar!" in prompt_text:
        criteria.append(
            LLMJudgeCriterion(
                criterion=(
                    "The application should print exactly Hello, Raidar! when run via "
                    "bun run start."
                ),
                weight=0.25,
            )
        )
    if re.search(r"dependency-light|No additional runtime dependencies", prompt_text):
        criteria.append(
            LLMJudgeCriterion(
                criterion=(
                    "The implementation should avoid adding unnecessary runtime dependencies."
                ),
                weight=0.25,
            )
        )
    return criteria


def _prompt_section_lines(prompt_text: str, heading: str) -> list[str]:
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$([\s\S]*?)(?=^##\s+|\Z)",
        re.MULTILINE,
    )
    match = pattern.search(prompt_text)
    if match is None:
        return []
    return [
        _strip_bullet_prefix(line)
        for raw_line in match.group(1).splitlines()
        if (line := raw_line.strip())
    ]


def _strip_bullet_prefix(line: str) -> str:
    if line.startswith("- "):
        return line[2:].strip()
    numbered = re.match(r"^\d+\.\s+(.*)$", line)
    if numbered is not None:
        return numbered.group(1).strip()
    return line


class CriticReview(BaseModel):
    """Critic assessment of the drafted scenario."""

    decision: Literal["approve", "revise", "block"] = "approve"
    summary: str
    risks: list[str] = Field(default_factory=list)


class LoopPlan(BaseModel):
    """Planner-authored research loop candidate."""

    loop_id: str
    title: str
    hypothesis: str
    instructions: str


class PlannerPlan(BaseModel):
    """One planner round containing sibling loop proposals."""

    loops: list[LoopPlan]
    notes: list[str] = Field(default_factory=list)


class ExecutorMemo(BaseModel):
    """Executor-authored change summary for one loop iteration."""

    summary: str
    changed_files: list[str] = Field(default_factory=list)
    rationale: str | None = None


class ReviewMemo(BaseModel):
    """Reviewer assessment of a research-loop result."""

    recommended_action: LoopAction
    summary: str
    strengths: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)


class GovernorDecision(BaseModel):
    """Governor decision after reading reviewer output and constraints."""

    action: LoopAction
    reasoning: str


class ResearchLoopState(BaseModel):
    """Persisted state for one bounded research loop."""

    loop_id: str
    objective_id: str
    title: str
    hypothesis: str
    instructions: str
    created_at_utc: str
    updated_at_utc: str
    status: ResearchLoopStatus
    iteration: int = 1
    max_iterations: int = 3
    candidate_scenario_ref: str
    latest_research_summary_ref: str | None = None
    latest_diff_ref: str | None = None
    latest_review_ref: str | None = None
    latest_governor_ref: str | None = None
    promoted_benchmark_ref: str | None = None
    stop_reason: str | None = None
    session_paths: dict[str, str] = Field(default_factory=dict)


class ComparisonGuard(BaseModel):
    """Deterministic promotion gate outcome."""

    passed: bool
    improved_dimensions: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
