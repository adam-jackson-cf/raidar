"""Agent entries checked against the agent CLI adapter's declared model support.

The spec reuses the adapters' own support tables (``REQUIRED_PROVIDER``,
``SUPPORTED_MODELS`` with per-model reasoning levels, ``ALLOWED_PROVIDERS``)
so an agent its CLI adapter would refuse at execution time is rejected by
``raidar validate``, with each problem located on the offending field.
Environment checks (CLI binary, credentials) stay with execution.
"""

from __future__ import annotations

from raidar.design.experiment.schema import AgentEntry
from raidar.subject.agent.adapters.external_cli import ExternalCliAdapter
from raidar.subject.agent.adapters.factory import adapter_class_for_harness
from raidar.subject.agent.adapters.harbor_cli import HarborCliAdapter

AgentIssue = tuple[str, str]
"""``(field location, message)``; ``raidar.design.experiment.validation`` wraps these as issues."""


def _harbor_issues(
    loc: str, agent: AgentEntry, adapter: type[HarborCliAdapter]
) -> list[AgentIssue]:
    label = adapter.ADAPTER_LABEL or agent.name.value
    if adapter.REQUIRED_PROVIDER and agent.provider != adapter.REQUIRED_PROVIDER:
        return [
            (
                f"{loc}.provider",
                f"{label} requires provider {adapter.REQUIRED_PROVIDER!r}, got {agent.provider!r}",
            )
        ]
    profile = adapter.SUPPORTED_MODELS.get(agent.model)
    if profile is None:
        supported = ", ".join(sorted(adapter.SUPPORTED_MODELS))
        return [
            (
                f"{loc}.model",
                f"{label} does not support model {agent.model!r} (supported: {supported})",
            )
        ]
    effort = agent.reasoning_effort
    if effort is None:
        return []
    if adapter.REASONING_UNSUPPORTED_MESSAGE is not None:
        return [(f"{loc}.reasoning_effort", adapter.REASONING_UNSUPPORTED_MESSAGE)]
    if effort not in profile.reasoning_levels:
        allowed = ", ".join(profile.reasoning_levels) or "(none)"
        return [
            (
                f"{loc}.reasoning_effort",
                f"model {agent.model!r} supports reasoning levels {allowed}; got {effort!r}",
            )
        ]
    return []


def _external_issues(
    loc: str, agent: AgentEntry, adapter: type[ExternalCliAdapter]
) -> list[AgentIssue]:
    allowed = adapter.ALLOWED_PROVIDERS
    if allowed and agent.provider not in allowed:
        return [
            (
                f"{loc}.provider",
                f"{agent.name.value} supports providers {', '.join(allowed)}; "
                f"got {agent.provider!r}",
            )
        ]
    return []


def agent_support_issues(index: int, agent: AgentEntry) -> list[AgentIssue]:
    """Provider/model/reasoning problems for one agent, per its agent CLI adapter."""
    loc = f"agents.{index}"
    adapter = adapter_class_for_harness(agent.harness)
    if issubclass(adapter, HarborCliAdapter):
        return _harbor_issues(loc, agent, adapter)
    if issubclass(adapter, ExternalCliAdapter):
        return _external_issues(loc, agent, adapter)
    return [(f"{loc}.name", f"no model support declared for agent {agent.name.value}")]
