"""Oracle result and evidence models.

``CaseResult`` is the normalised, runner-independent unit of oracle truth
(design memo §2: ``CaseResult{id, group, checkpoint_origin, status}``). A step
is solved under the strict all-cases rule: no infrastructure failure, at least
one non-skipped case, and every non-skipped case passed. Skipped cases leave
the denominator, as in SCB's native runner and its Harbor port.

``RunEvidence`` carries the environment/corpus/invocation identity and the
per-node ledger, coverage contexts and process events of one sandbox run. The
coverage parity verdict compares the grading run against the separate coverage
run (see ``raidar.oracle.parity``).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

INFRA_REWARD_KEY = "infrastructure_failure"


class OracleModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CaseGroup(StrEnum):
    CORE = "core"
    FUNCTIONALITY = "functionality"
    ERROR = "error"
    REGRESSION = "regression"


class CaseStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class CaseResult(OracleModel):
    id: str = Field(min_length=1)
    group: CaseGroup
    checkpoint_origin: str = Field(min_length=1)
    status: CaseStatus


class GroupCount(OracleModel):
    passed: int = Field(ge=0)
    total: int = Field(ge=0)


class InfraFailure(OracleModel):
    """Why a grading run produced no trustworthy verdict (never a solve/fail)."""

    code: str = Field(min_length=1)
    detail: str = ""


class OracleGrade(OracleModel):
    step: str = Field(min_length=1)
    checkpoint: str = Field(min_length=1)
    cases: tuple[CaseResult, ...]
    infra_failure: InfraFailure | None = None

    def group_counts(self) -> dict[CaseGroup, GroupCount]:
        counts: dict[CaseGroup, GroupCount] = {}
        for group in CaseGroup:
            graded = [c for c in self.cases if c.group is group and c.status != CaseStatus.SKIPPED]
            passed = sum(1 for c in graded if c.status == CaseStatus.PASSED)
            counts[group] = GroupCount(passed=passed, total=len(graded))
        return counts

    @property
    def solved(self) -> bool:
        graded = [c for c in self.cases if c.status != CaseStatus.SKIPPED]
        return (
            self.infra_failure is None
            and bool(graded)
            and all(c.status == CaseStatus.PASSED for c in graded)
        )

    def pass_rate(self, groups: frozenset[CaseGroup] | None = None) -> float | None:
        """Pass rate over graded cases, or ``None`` when unavailable (never 0.0 for unknown)."""
        counts = [
            count
            for group, count in self.group_counts().items()
            if groups is None or group in groups
        ]
        total = sum(count.total for count in counts)
        if self.infra_failure is not None or total == 0:
            return None
        return sum(count.passed for count in counts) / total

    def rewards(self) -> dict[str, float]:
        """Step reward for Harbor: verdict only, never case detail.

        An unavailable value is omitted, never reported as 0.0 (F6): an
        infrastructure failure carries only ``infrastructure_failure=1.0``, and
        a rate is present only when its group has graded cases. Harbor's
        ``min_reward`` treats a missing key as below threshold, so a halt gate
        on ``solved`` stops on an unavailable verdict without scoring it.
        """
        if self.infra_failure is not None:
            return {INFRA_REWARD_KEY: 1.0}
        rates = {
            "strict_pass_rate": self.pass_rate(),
            "core_pass_rate": self.pass_rate(frozenset({CaseGroup.CORE})),
            "regression_pass_rate": self.pass_rate(frozenset({CaseGroup.REGRESSION})),
        }
        return {
            "solved": 1.0 if self.solved else 0.0,
            **{key: rate for key, rate in rates.items() if rate is not None},
            INFRA_REWARD_KEY: 0.0,
        }

    def summary(self) -> dict[str, object]:
        return {
            "solved": self.solved,
            "strict_pass_rate": self.pass_rate(),
            "group_counts": {
                group.value: count.model_dump() for group, count in self.group_counts().items()
            },
            "cases": len(self.cases),
        }


class EnvironmentEvidence(OracleModel):
    grader_environment_id: str = Field(min_length=1)
    grader_tools_sha256: str = Field(min_length=1)
    grader_interpreter_sha256: str
    submission_interpreter_sha256: str
    platform_sha256: str = Field(min_length=1)
    plugin_sha256: str = Field(min_length=1)


class CorpusEvidence(OracleModel):
    corpus_sha256: str = Field(min_length=1)
    selected_tests: tuple[str, ...]


class InvocationEvidence(OracleModel):
    argv: tuple[str, ...] = Field(min_length=1)
    cwd: str = Field(min_length=1)


class LedgerEntry(OracleModel):
    node_id: str = Field(min_length=1)
    phase: str = Field(min_length=1)
    outcome: str = Field(min_length=1)


class CoverageContext(OracleModel):
    node_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    executed_lines: tuple[int, ...]


class ProcessEvent(OracleModel):
    node_id: str = Field(min_length=1)
    phase: str = Field(min_length=1)
    event: str = Field(min_length=1)


class RunEvidence(OracleModel):
    role: str = Field(pattern="^(grading|coverage)$")
    environment: EnvironmentEvidence
    corpus: CorpusEvidence
    invocation: InvocationEvidence
    measurement_prefix: tuple[str, ...] = ()
    ledger: tuple[LedgerEntry, ...]
    coverage: tuple[CoverageContext, ...] = ()
    process_events: tuple[ProcessEvent, ...] = ()
    invalid_coverage_paths: int = Field(default=0, ge=0)


class ParityVerdict(OracleModel):
    eligible: bool
    reasons: tuple[str, ...] = ()
