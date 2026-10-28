"""The register check ``make quality`` runs: every definition in the tree matches its release.

``release_issues`` reports every agent, stage package and workflow that is unreleased or changed
since its latest release (naming the line to add), every registered name the tree does not define,
every agent CLI pin that differs from a runtime pack that bakes its harness in (the agent pin is
the source) and, given a base commit, any release the base registered that was removed or
re-hashed (released means frozen). ``release_base`` picks that base: the merge base with ``main``,
or ``HEAD``'s parent when ``HEAD`` is on ``main`` itself.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from raidar.contract.paths import REPO_ROOT, agents_root, releases_file, stages_root, workflows_root
from raidar.contract.releases import (
    ReleaseError,
    ReleaseKind,
    ReleaseRegister,
    load_register,
    parse_register,
    rewritten_releases,
)
from raidar.subject.agent.definition import AgentDefinition, agent_names, load_agent
from raidar.subject.environment import available_runtimes, load_runtime_pack
from raidar.subject.package import StagePackageError
from raidar.subject.package.registry import load_stage_package
from raidar.subject.releases import DEFINITION_ERRORS, tree_workflow_digest
from raidar.subject.stages import stage_names
from raidar.subject.workflow.definition import workflow_names

MAIN_REFS = ("main", "origin/main")


def _registration_issues(
    kind: ReleaseKind,
    names: list[str],
    digest_of: Callable[[str], str],
    register: ReleaseRegister,
) -> list[str]:
    issues = []
    for name in names:
        try:
            digest = digest_of(name)
        except (*DEFINITION_ERRORS, StagePackageError, ReleaseError, OSError) as exc:
            issues.append(str(exc))
            continue
        latest = register.latest(kind, name)
        if latest is None:
            issues.append(f"{kind.value}/{name} is not released: register {name}@1: {digest}")
        elif register.of(kind)[latest] != digest:
            issues.append(
                f"{kind.value}/{name} changed since {latest}: register "
                f"{name}@{latest.version + 1}: {digest}"
            )
    issues.extend(
        f"{kind.value} release name {name!r} is registered but not defined in the tree"
        for name in sorted(register.names(kind) - set(names))
    )
    return issues


def _pin_issues(agents: list[AgentDefinition]) -> list[str]:
    issues = []
    for runtime in available_runtimes():
        pack = load_runtime_pack(runtime)
        for agent in agents:
            pin = pack.image.harnesses.get(agent.harness.value)
            if pin is None:
                continue
            baked = (pin.npm_package, pin.executable, pin.version)
            pinned = (agent.tool.package, agent.tool.binary, agent.tool.version)
            if baked != pinned:
                issues.append(
                    f"agents/{agent.name}: tool {pinned} differs from pack {pack.id} "
                    f"image.harnesses.{agent.harness.value} {baked}; the agent pin is the source"
                )
    return issues


def _git(repo_root: Path, *args: str) -> str | None:
    done = subprocess.run(
        ["git", "-C", str(repo_root), *args], capture_output=True, text=True, check=False
    )
    return done.stdout.strip() if done.returncode == 0 else None


def release_base(repo_root: Path = REPO_ROOT) -> str | None:
    """The commit whose register the tree's must extend; ``None`` when there is nothing before.

    Off ``main``: the merge base with ``main`` (local, else ``origin/main``). On ``main``'s tip
    (merge base is ``HEAD``): ``HEAD``'s parent. ``ReleaseError`` when no ``main`` is found.
    """
    head = _git(repo_root, "rev-parse", "HEAD")
    if head is None:
        return None
    base = next((b for ref in MAIN_REFS if (b := _git(repo_root, "merge-base", "HEAD", ref))), None)
    if base is None:
        raise ReleaseError(
            "no main branch to check released versions against: fetch main or set RELEASES_BASE"
        )
    return _git(repo_root, "rev-parse", "HEAD~1") if base == head else base


def history_issues(base: str, repo_root: Path = REPO_ROOT) -> list[str]:
    """Releases registered at commit ``base`` that the tree's register removed or re-hashed."""
    path = releases_file(repo_root)
    relative = path.relative_to(repo_root).as_posix()
    text = _git(repo_root, "show", f"{base}:{relative}")
    if text is None:
        if _git(repo_root, "cat-file", "-e", f"{base}^{{commit}}") is None:
            return [f"release base {base!r} is not a commit"]
        return []  # the register did not exist yet at the base
    try:
        earlier = parse_register(text, f"{base}:{relative}")
        now = load_register(path)
    except ReleaseError as exc:
        return [str(exc)]
    return rewritten_releases(earlier, now)


def release_issues(repo_root: Path = REPO_ROOT, *, base: str | None = None) -> list[str]:
    """Every register, definition, pin and (given ``base``) history problem; empty when sound."""
    try:
        register = load_register(releases_file(repo_root))
    except ReleaseError as exc:
        return [str(exc)]
    a_root = agents_root(repo_root)
    issues = _registration_issues(
        ReleaseKind.AGENT,
        agent_names(a_root),
        lambda name: load_agent(a_root, name).digest,
        register,
    )
    issues += _registration_issues(
        ReleaseKind.STAGE,
        stage_names(repo_root),
        lambda name: load_stage_package(stages_root(repo_root) / name).digest,
        register,
    )
    issues += _registration_issues(
        ReleaseKind.WORKFLOW,
        workflow_names(workflows_root(repo_root)),
        lambda name: tree_workflow_digest(name, repo_root, register),
        register,
    )
    issues += _pin_issues(_loadable_agents(a_root))
    if base is not None:
        issues += history_issues(base, repo_root)
    return issues


def _loadable_agents(root: Path) -> list[AgentDefinition]:
    """The agents that load (a broken one is already reported by the registration check)."""
    agents = []
    for name in agent_names(root):
        try:
            agents.append(load_agent(root, name))
        except DEFINITION_ERRORS:
            continue
    return agents
