"""Credential preflight: every selected arm's harness has its host credential before any trial.

Presence only: values are never read beyond a non-empty check, never printed and
never returned. A run whose arm lacks a credential would otherwise start Harbor,
build environments and fail inside the first trial; the preflight fails first,
naming the agent field and the variables that would satisfy it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from raidar.agents.config import Harness
from raidar.codex_auth import CODEX_AUTH_MODE_ENV, resolve_codex_auth
from raidar.harness import harness_definition

if TYPE_CHECKING:
    from raidar.execution.plan import ExperimentPlan
    from raidar.spec.schema import ExperimentSpec

# Harbor's claude-code agent authenticates with an API key (ANTHROPIC_API_KEY, or
# ANTHROPIC_AUTH_TOKEN) or a subscription token (CLAUDE_CODE_OAUTH_TOKEN).
CLAUDE_CREDENTIAL_NAMES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
CLAUDE_FORCE_OAUTH_ENV = "CLAUDE_FORCE_OAUTH"
SECRETS_HINT = "(Infisical project mac-os-cf, env dev, via make; else orchestrator/.env)"


class CredentialPreflightError(RuntimeError):
    """Raised when a selected arm's harness has no usable host credential."""


@dataclass(frozen=True)
class CredentialIssue:
    field: str
    message: str

    def render(self) -> str:
        return f"{self.field}: {self.message}"


def _present(env: Mapping[str, str], name: str) -> bool:
    return bool(env.get(name, "").strip())


def _codex_issue(env: Mapping[str, str]) -> str | None:
    try:
        codex_home = env.get("CODEX_HOME", "").strip()
        resolve_codex_auth(
            requested_mode=env.get(CODEX_AUTH_MODE_ENV, ""),
            auth_json_path=Path(codex_home).expanduser() / "auth.json" if codex_home else None,
            api_key_present=_present(env, "OPENAI_API_KEY"),
        )
    except (OSError, ValueError) as exc:
        return str(exc)
    return None


def _claude_issue(env: Mapping[str, str]) -> str | None:
    force_oauth = env.get(CLAUDE_FORCE_OAUTH_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
    if force_oauth and not _present(env, "CLAUDE_CODE_OAUTH_TOKEN"):
        return f"{CLAUDE_FORCE_OAUTH_ENV} is set but CLAUDE_CODE_OAUTH_TOKEN is not"
    if any(_present(env, name) for name in CLAUDE_CREDENTIAL_NAMES):
        return None
    return f"no credential: set one of {', '.join(CLAUDE_CREDENTIAL_NAMES)} {SECRETS_HINT}"


def _generic_issue(harness: str, env: Mapping[str, str]) -> str | None:
    names = [
        name
        for credential in harness_definition(harness).credentials
        if credential.required
        for name in credential.accepted_names
    ]
    if not names or any(_present(env, name) for name in names):
        return None
    return f"no credential: set one of {', '.join(names)} {SECRETS_HINT}"


def harness_credential_issue(harness: Harness, env: Mapping[str, str]) -> str | None:
    """Why ``harness`` cannot authenticate from ``env``, or ``None`` when it can."""
    if harness is Harness.CODEX_CLI:
        return _codex_issue(env)
    if harness is Harness.CLAUDE_CODE:
        return _claude_issue(env)
    return _generic_issue(harness.value, env)


def credential_issues(
    spec: ExperimentSpec, arm_ids: list[str], env: Mapping[str, str]
) -> list[CredentialIssue]:
    """One issue per agent (by its ``agents.<i>`` field) that a selected arm cannot run."""
    issues: list[CredentialIssue] = []
    seen: set[str] = set()
    for arm in spec.arms:
        if arm.id not in arm_ids or arm.agent in seen:
            continue
        seen.add(arm.agent)
        index, agent = next((i, a) for i, a in enumerate(spec.agents) if a.id == arm.agent)
        problem = harness_credential_issue(agent.harness, env)
        if problem is not None:
            field = f"agents.{index}.harness ({agent.id!r}, {agent.harness.value}, arm {arm.id!r})"
            issues.append(CredentialIssue(field, problem))
    return issues


def preflight_credentials(plan: ExperimentPlan, env: Mapping[str, str] | None = None) -> None:
    """Fail before any trial unless every selected arm's harness can authenticate.

    A no-LLM agent override (oracle/nop) runs no harness, so it needs nothing.
    """
    if plan.options.agent_override is not None:
        return
    arm_ids = sorted({trial.arm for trial in plan.selected_trials()})
    issues = credential_issues(plan.loaded.spec, arm_ids, os.environ if env is None else env)
    if issues:
        lines = "\n".join(f"  {issue.render()}" for issue in issues)
        raise CredentialPreflightError(
            f"credential preflight failed; no trial was started:\n{lines}"
        )
