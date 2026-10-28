"""Credential preflight: every selected arm's agent has its host credential before any trial.

Presence only: values are never read beyond a non-empty check, never printed and
never returned. A run whose arm lacks a credential would otherwise start Harbor,
build environments and fail inside the first trial; the preflight fails first,
naming the agent field and the variables that would satisfy it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from raidar.subject.agent.config import Harness
from raidar.subject.agent.native import credentials

if TYPE_CHECKING:
    from raidar.engine.trial.plan import ExperimentPlan


class CredentialPreflightError(RuntimeError):
    """Raised when a selected arm's agent has no usable host credential."""


@dataclass(frozen=True)
class CredentialIssue:
    field: str
    message: str

    def render(self) -> str:
        return f"{self.field}: {self.message}"


def harness_credential_issue(harness: Harness, env: Mapping[str, str]) -> str | None:
    """Why ``harness`` cannot authenticate from ``env``, or ``None`` when it can."""
    return credentials(harness, env).issue


def credential_issues(
    plan: ExperimentPlan, arm_ids: list[str], env: Mapping[str, str]
) -> list[CredentialIssue]:
    """One issue per agent (by its ``agents.<i>`` field) that a selected arm cannot run."""
    spec = plan.loaded.spec
    issues: list[CredentialIssue] = []
    seen: set[str] = set()
    for arm_id in arm_ids:
        agent = plan.arms[arm_id].arm.agent
        if agent.entry.id in seen:
            continue
        seen.add(agent.entry.id)
        index = next(i for i, entry in enumerate(spec.agents) if entry.id == agent.entry.id)
        problem = harness_credential_issue(agent.harness, env)
        if problem is not None:
            field = (
                f"agents.{index}.release ({agent.entry.id!r}, {agent.released.ref}, arm {arm_id!r})"
            )
            issues.append(CredentialIssue(field, problem))
    return issues


def preflight_credentials(plan: ExperimentPlan, env: Mapping[str, str] | None = None) -> None:
    """Fail before any trial unless every selected arm's agent can authenticate.

    A no-LLM agent override (oracle/nop) runs no agent CLI, so it needs nothing.
    """
    if plan.options.agent_override is not None:
        return
    arm_ids = sorted({trial.arm for trial in plan.selected_trials()})
    issues = credential_issues(plan, arm_ids, os.environ if env is None else env)
    if issues:
        lines = "\n".join(f"  {issue.render()}" for issue in issues)
        raise CredentialPreflightError(
            f"credential preflight failed; no trial was started:\n{lines}"
        )
