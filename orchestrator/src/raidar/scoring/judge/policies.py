"""Versioned Jev policies: one mode per question set (design memo §3.5, plan H2).

Each policy is a YAML file in ``raidar/scoring/judge/policies/`` and is part of the repo,
so a mode change is a reviewed diff. Modes:

- ``off``: no Jev call; every decision is still audited as ``skipped``.
- ``shadow`` (the default for new policies): decisions are made, cached and
  audited, and their observations are shown as uncalibrated. They never feed
  Metrics or verdicts.
- ``enforce``: decisions count. A policy may only be ``enforce`` with a
  recorded promotion that meets the gate from
  ``evaluate-jev/references/policies.md``: at least 30 labelled decisions, at
  least 5 of the minority label, input sufficiency of at least 80%, and a
  frozen threshold chosen on a calibration split. The promotion names the
  runtimes it was calibrated on and must cover every runtime of the policy
  (a ``*`` wildcard policy cannot be promoted), and its calibration and
  evaluation trials must be disjoint, so near-identical checkpoints of one
  trajectory cannot sit on both sides. The numbers written here are claims:
  ``raidar.scoring.judge.promotion.verify_promotion`` re-derives them from the audit.
  A promotion also records which reviewer kinds (``human``, ``model`` and the
  model ids) its labels came from, the precision, recall and agreement of the
  frozen threshold on each side of the split, and the scorer version the
  enforce mode ships with (``raidar.scoring.judge.promotion`` writes all of these).

A run may lower a policy's mode (for example ``shadow`` to ``off``) but never
raise it; raising is a new policy version.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from raidar.design.experiments.canonical import digest_json

POLICY_DIR = Path(__file__).parent / "policies"
LABELS = ("correct", "false_positive", "false_negative", "uncertain")
MIN_LABELLED = 30
MIN_MINORITY = 5
MIN_INPUT_SUFFICIENCY = 0.8
_ID_PATTERN = r"^[a-z0-9][a-z0-9-]*(\.[a-z0-9][a-z0-9-]*)*$"


class PolicyMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    ENFORCE = "enforce"

    @property
    def rank(self) -> int:
        return (PolicyMode.OFF, PolicyMode.SHADOW, PolicyMode.ENFORCE).index(self)


class PolicyError(ValueError):
    """A policy file or a mode override is invalid."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CalibrationSplit(_Frozen):
    """Trial ids on each side of the split; a trial is one trajectory of one problem."""

    calibration: tuple[str, ...] = Field(min_length=1)
    evaluation: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _disjoint(self) -> Self:
        shared = sorted(set(self.calibration) & set(self.evaluation))
        if shared:
            raise ValueError(f"calibration and evaluation share trials {shared}")
        return self

    def side(self, trial: str | None) -> str | None:
        if trial in self.calibration:
            return "calibration"
        return "evaluation" if trial in self.evaluation else None


class SplitMetrics(_Frozen):
    """How the frozen threshold agrees with the labels on one side of the split."""

    labelled: int = Field(ge=0)
    precision: float | None = Field(default=None, ge=0.0, le=1.0)
    recall: float | None = Field(default=None, ge=0.0, le=1.0)
    agreement: float | None = Field(default=None, ge=0.0, le=1.0)


class Promotion(_Frozen):
    """Evidence that a policy passed the enforce gate; the threshold is frozen here."""

    threshold: float = Field(gt=0.0, lt=1.0)
    labelled: int = Field(ge=0)
    minority: int = Field(ge=0)
    input_sufficiency: float = Field(ge=0.0, le=1.0)
    runtimes: tuple[str, ...] = Field(min_length=1)
    split: CalibrationSplit
    regression_corpus: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewers: dict[Literal["human", "model"], int] | None = None
    reviewer_models: tuple[str, ...] = ()
    calibration_metrics: SplitMetrics | None = None
    evaluation_metrics: SplitMetrics | None = None
    scorer: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]*@[A-Za-z0-9._+-]+$")

    def gate_failures(self, policy_runtimes: tuple[str, ...]) -> list[str]:
        failures = []
        if "*" in policy_runtimes:
            failures.append("a '*' policy cannot be promoted; name its runtimes")
        uncovered = sorted(set(policy_runtimes) - set(self.runtimes) - {"*"})
        if uncovered:
            failures.append(f"runtimes {uncovered} have no calibration")
        if self.labelled < MIN_LABELLED:
            failures.append(f"{self.labelled} labelled decisions < {MIN_LABELLED}")
        if self.minority < MIN_MINORITY:
            failures.append(f"{self.minority} minority labels < {MIN_MINORITY}")
        if self.input_sufficiency < MIN_INPUT_SUFFICIENCY:
            failures.append(f"input sufficiency {self.input_sufficiency} < {MIN_INPUT_SUFFICIENCY}")
        return failures


class JevPolicy(_Frozen):
    id: str = Field(pattern=_ID_PATTERN)
    version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    mode: PolicyMode
    question_set: str = Field(min_length=1)
    runtimes: tuple[str, ...] = Field(min_length=1)
    labels: tuple[str, ...] = LABELS
    promotion: Promotion | None = None

    @model_validator(mode="after")
    def _enforce_needs_promotion(self) -> Self:
        if self.mode is PolicyMode.ENFORCE:
            if self.promotion is None:
                raise ValueError(f"policy {self.id}: enforce needs a recorded promotion")
            failures = self.promotion.gate_failures(self.runtimes)
            if failures:
                raise ValueError(f"policy {self.id}: promotion gate not met: {failures}")
        return self

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    def digest(self) -> str:
        return digest_json(self.model_dump(mode="json"))


def load_policy(path: Path) -> JevPolicy:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return JevPolicy.model_validate(raw)
    except (OSError, yaml.YAMLError, ValueError) as error:
        raise PolicyError(f"{path}: {error}") from error


class PolicyRegistry:
    """The policies of one run, after any mode overrides."""

    def __init__(self, policies: Iterable[JevPolicy]) -> None:
        self._policies: dict[str, JevPolicy] = {}
        for policy in policies:
            if policy.id in self._policies:
                raise PolicyError(f"duplicate Jev policy {policy.id}")
            self._policies[policy.id] = policy

    @classmethod
    def from_dir(cls, directory: Path = POLICY_DIR) -> PolicyRegistry:
        return cls(load_policy(path) for path in sorted(directory.glob("*.yaml")))

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._policies))

    def get(self, policy_id: str) -> JevPolicy:
        try:
            return self._policies[policy_id]
        except KeyError:
            raise PolicyError(f"unknown Jev policy {policy_id!r}") from None

    def with_overrides(self, overrides: Mapping[str, str]) -> PolicyRegistry:
        """Apply per-run mode overrides, which may only lower a policy's mode."""
        updated = dict(self._policies)
        for policy_id, raw_mode in overrides.items():
            policy = self.get(policy_id)
            try:
                mode = PolicyMode(raw_mode)
            except ValueError:
                raise PolicyError(f"policy {policy_id}: unknown mode {raw_mode!r}") from None
            if mode.rank > policy.mode.rank:
                raise PolicyError(
                    f"policy {policy_id}: a run cannot raise {policy.mode.value} to "
                    f"{mode.value}; promote it in the policy file"
                )
            updated[policy_id] = policy.model_copy(update={"mode": mode})
        return PolicyRegistry(updated.values())

    def modes(self) -> dict[str, str]:
        return {policy_id: self._policies[policy_id].mode.value for policy_id in self.ids()}
