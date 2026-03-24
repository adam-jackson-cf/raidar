"""Typed objective, role, and loop state for autoresearch orchestration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

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
    model_id: str = "gpt-5.4"


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
    research_repeats: int = 3
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
    research_repeats: int
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
    notes: list[str] = Field(default_factory=list)


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
