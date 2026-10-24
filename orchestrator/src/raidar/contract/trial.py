"""Execution records: planned trials, step results, trial records.

A ``PlannedTrial`` is one arm x attempt x problem trial (one Harbor
multi-step trial). After it runs, ``raidar.engine.trial.finish`` writes one ``TrialRecord``
holding a ``StepResult`` per planned checkpoint (Harbor's per-step result),
the execution-validity gate and the N1 phase timings. Correctness facts come
from the host-only verifier records (``raidar-oracle/steps/<step>/record.json``,
an identity-bearing evidence name), never from Harbor's aggregated trial reward.

Vocabulary (Harbor's): a *job* is one execution of a locked
experiment, a *trial* is one arm x attempt x problem run through every
checkpoint, a *step result* is one checkpoint of a trial, and *attempts* are
the paired, counterbalanced replicates of every arm.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NOT_STARTED = "not_started"
"""The ``StepResult.termination`` of a step that never started."""


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
    attempt: int = Field(ge=0)
    order: int = Field(ge=0, description="Position within the attempt (AB/BA order)")
    sequence: int = Field(ge=0, description="Global start order across the plan")
    arm: str
    problem: str
    problem_index: int = Field(ge=0)
    steps: tuple[str, ...] = Field(min_length=1)
    hashes: TrialHashes


class ProgressionRule(ExecutionModel):
    """How a trial proceeds past a checkpoint (F4)."""

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
    flags: tuple[str, ...] = Field(
        default=(),
        description=(
            "Integrity flags (raidar.subject.treatment.definition.integrity): agent behaviour "
            "a component records without failing the step, e.g. an edited protected-config copy or "
            "an attempt to get round a commit hook"
        ),
    )


class StepResult(ExecutionModel):
    kind: Literal["raidar-step-result"] = "raidar-step-result"
    schema_version: Literal[2] = 2
    trial_id: str
    arm: str
    attempt: int
    problem: str
    index: int = Field(ge=0)
    step: str
    checkpoint: str
    checkpoint_state: CheckpointState
    infra_reasons: tuple[str, ...] = ()
    strict_pass_rate: float | None = None
    rewards: dict[str, float] | None = None
    verifier_record: str | None = None
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
    """The kept execution-validity gate: an invalid trial is infra-excluded."""

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


class TrialRecord(ExecutionModel):
    kind: Literal["raidar-trial"] = "raidar-trial"
    schema_version: Literal[2] = 2
    experiment: str
    job_id: str
    trial: PlannedTrial
    trial_dir: str
    progression: ProgressionRule
    truncated_to: int | None = None
    agent_override: str | None = None
    step_results: tuple[StepResult, ...]
    reached: tuple[bool, ...]
    reached_source: Literal["executed", "derived"]
    validity: ExecutionValidity
    phases: tuple[PhaseSample, ...] = ()
    trial_exception: str | None = None

    @property
    def valid(self) -> bool:
        return self.validity.passed
