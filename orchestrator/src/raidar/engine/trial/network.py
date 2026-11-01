"""Bound Harbor's native task/phase egress policies before a trial starts.

Only the materialized task is rewritten. Provider endpoints and runtime package
registries form the ceiling. An authored task or phase can narrow it, including
disabling the network entirely.
"""

from __future__ import annotations

from pathlib import Path

from harbor.models.task.config import (
    NetworkMode,
    NetworkPolicy,
    TaskConfig,
    normalize_allowed_hosts,
)

PROVIDER_HOSTS = {
    "openai": ("api.openai.com", "chatgpt.com", "auth.openai.com"),
    "anthropic": ("api.anthropic.com",),
}
RUNTIME_PACKAGE_HOSTS = {
    "python": ("pypi.org", "files.pythonhosted.org", "registry.npmjs.org"),
}


def _host_intersection(first: str, second: str) -> str | None:
    """The host both entries allow; the ceiling holds host names only, so an IP/CIDR entry
    can never intersect it and is dropped."""
    if first == second:
        return first
    for pattern, candidate in ((first, second), (second, first)):
        if pattern.startswith("*.") and candidate.endswith(pattern[1:]):
            return candidate
    return None


def _restrict_policy(policy: NetworkPolicy, ceiling: NetworkPolicy) -> NetworkPolicy:
    if NetworkMode.NO_NETWORK in (policy.network_mode, ceiling.network_mode):
        return NetworkPolicy(network_mode=NetworkMode.NO_NETWORK)
    if policy.network_mode == NetworkMode.PUBLIC:
        return ceiling
    hosts = {
        host
        for first in policy.allowed_hosts
        for second in ceiling.allowed_hosts
        if (host := _host_intersection(first, second)) is not None
    }
    return NetworkPolicy(network_mode=NetworkMode.ALLOWLIST, allowed_hosts=sorted(hosts))


def restrict_trial_network(task_dir: Path, *, runtime: str, provider: str) -> None:
    """Write the effective native Harbor boundary for startup and every phase.

    Harbor ignores run-specific extra hosts on a public task baseline and allows
    explicit phase overrides to replace that baseline. Restricting all these
    native policies ensures none can restore public access later in the trial.
    """
    if runtime not in RUNTIME_PACKAGE_HOSTS or provider not in PROVIDER_HOSTS:
        raise ValueError(f"no trial network host contract for {runtime}/{provider}")
    hosts = normalize_allowed_hosts([*PROVIDER_HOSTS[provider], *RUNTIME_PACKAGE_HOSTS[runtime]])
    ceiling = NetworkPolicy(network_mode=NetworkMode.ALLOWLIST, allowed_hosts=sorted(set(hosts)))
    path = task_dir / "task.toml"
    task = TaskConfig.model_validate_toml(path.read_text())
    baseline = _restrict_policy(task.environment.resolve_baseline(), ceiling)
    task.environment.network_mode = baseline.network_mode
    task.environment.allowed_hosts = baseline.allowed_hosts
    for phase in (task.agent, task.verifier):
        policy = _restrict_policy(phase.explicit_phase_policy() or baseline, baseline)
        phase.network_mode = policy.network_mode
        phase.allowed_hosts = policy.allowed_hosts
    for step in task.steps or ():
        for phase, inherited in ((step.agent, task.agent), (step.verifier, task.verifier)):
            phase_ceiling = inherited.explicit_phase_policy() or baseline
            policy = _restrict_policy(
                phase.explicit_phase_policy() or phase_ceiling,
                phase_ceiling,
            )
            phase.network_mode = policy.network_mode
            phase.allowed_hosts = policy.allowed_hosts
    path.write_text(task.model_dump_toml())
