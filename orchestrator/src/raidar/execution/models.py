"""Execution records (plan WS-D): planned trials, checkpoint attempts, trajectories.

A ``PlannedTrial`` is one arm x block x problem trajectory (one Harbor
multi-step trial). After it runs, the driver writes one ``TrajectoryRecord``
holding a ``CheckpointAttempt`` per planned checkpoint, the execution-validity
gate and the N1 phase timings. Correctness facts come from the host-only
oracle records (``raidar-oracle/steps/<step>/record.json``), never from
Harbor's aggregated trial reward.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExecutionModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CheckpointState(StrEnum):
    SOLVED = "solved"
    FAILED = "failed"
    NOT_ATTEMPTED = "not_attempted"
    INFRA_ERROR = "infra_error"


class TrialHashes(ExecutionModel):
    """Content identity of one trial's inputs (N3); ``trial`` digests all of them."""

    lock: str
    problem: str
    arm: str
    condition: str = Field(description="CompiledCondition.digest, including setup.sh")
    agent: str
    protocol: str
    trial: str


class PlannedTrial(ExecutionModel):
    trial_id: str
    block: int = Field(ge=0)
    order: int = Field(ge=0, description="Position within the block (AB/BA order)")
    sequence: int = Field(ge=0, description="Global start order across the plan")
    arm: str
    problem: str
    problem_index: int = Field(ge=0)
    steps: tuple[str, ...] = Field(min_length=1)
    hashes: TrialHashes


class ProgressionRule(ExecutionModel):
    """How a trajectory proceeds past a checkpoint (F4)."""

    mode: Literal["continue", "halt_on_failure"]
    reward_key: str
    threshold: float | None = None

    def harbor_min_reward(self) -> dict[str, float] | None:
        """Harbor ``[[steps]].min_reward`` (a missing key counts as -inf there)."""
        if self.mode == "continue" or self.threshold is None:
            return None
        return {self.reward_key: self.threshold}


class Usage(ExecutionModel):
    n_input_tokens: int | None = None
    n_cache_tokens: int | None = None
    n_output_tokens: int | None = None
    cost_usd: float | None = None


class SnapshotInfo(ExecutionModel):
    path: str
    digest: str


class ProbeSummary(ExecutionModel):
    passed: bool
    failures: tuple[str, ...] = ()


class CheckpointAttempt(ExecutionModel):
    kind: Literal["raidar-checkpoint-attempt"] = "raidar-checkpoint-attempt"
    schema_version: Literal[1] = 1
    trial_id: str
    arm: str
    block: int
    problem: str
    index: int = Field(ge=0)
    step: str
    checkpoint: str
    checkpoint_state: CheckpointState
    infra_reasons: tuple[str, ...] = ()
    strict_pass_rate: float | None = None
    rewards: dict[str, float] | None = None
    oracle_record: str | None = None
    instruction_sha256: str | None = None
    snapshot: SnapshotInfo | None = None
    parent_snapshot_digest: str | None = None
    trace_dir: str | None = None
    usage: Usage | None = None
    agent_execution_s: float | None = None
    termination: str | None = None
    probes: ProbeSummary | None = None


class ValidityCheck(ExecutionModel):
    name: str
    passed: bool
    detail: str = ""


class ExecutionValidity(ExecutionModel):
    """The kept execution-validity gate: an invalid trajectory is infra-excluded."""

    checks: tuple[ValidityCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


class PhaseSample(ExecutionModel):
    """One N1 phase measurement (seconds); ``step`` is None for trial-scoped phases."""

    phase: str
    seconds: float = Field(ge=0.0)
    step: str | None = None
    condition: str | None = Field(
        default=None, description="Budget selector, e.g. artifact/download, init/no-init, cold"
    )


class TrajectoryRecord(ExecutionModel):
    kind: Literal["raidar-trajectory"] = "raidar-trajectory"
    schema_version: Literal[1] = 1
    experiment: str
    run_id: str
    trial: PlannedTrial
    trial_dir: str
    progression: ProgressionRule
    truncated_to: int | None = None
    agent_override: str | None = None
    attempts: tuple[CheckpointAttempt, ...]
    reached: tuple[bool, ...]
    reached_source: Literal["executed", "derived"]
    validity: ExecutionValidity
    phases: tuple[PhaseSample, ...] = ()
    trial_exception: str | None = None

    @property
    def valid(self) -> bool:
        return self.validity.passed
