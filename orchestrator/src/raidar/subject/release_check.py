"""The register check ``make quality`` runs: every definition in the tree matches its release.

``release_issues`` reports every agent, stage package and workflow that is unreleased or changed
since its latest release (naming the line to add), every registered name the tree does not define,
every agent CLI pin that differs from a runtime pack that bakes its harness in (the agent pin is
the source) and, given a base commit, any release the base registered that was removed.
Re-hashing a registered version in place is allowed: definitions are edited in place and the
affected experiments are relocked and rerun. ``release_base`` picks that base: the merge base
with ``main``, or ``HEAD``'s parent when ``HEAD`` is on ``main`` itself.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from raidar.contract.paths import (
    REPO_ROOT,
    agents_root,
    releases_file,
    workflows_root,
)
from raidar.contract.releases import (
    ReleaseKind,
    load_register,
    parse_register,
    registration_issues,
    removed_releases,
)
from raidar.subject.agent.definition import load_agent
from raidar.subject.agent_checks import agent_pin_issues
from raidar.subject.imports import import_issues
from raidar.subject.package.release_check import stage_release_issues
from raidar.subject.releases import tree_workflow_digest
from raidar_contract.agent import agent_names
from raidar_contract.references import ReleaseError

MAIN_REFS = ("main", "origin/main")


GitRunner = Callable[..., str | None]


def _git(repo_root: Path, *args: str) -> str | None:
    """Stdout of ``git -C repo_root args``; ``None`` on a non-zero exit."""
    done = subprocess.run(
        ["git", "-C", str(repo_root), *args], capture_output=True, text=True, check=False
    )
    return done.stdout.strip() if done.returncode == 0 else None


def release_base(repo_root: Path = REPO_ROOT, *, git: GitRunner = _git) -> str | None:
    """The commit whose register the tree's must extend; ``None`` when there is nothing before.

    Off ``main``: the merge base with ``main`` (local, else ``origin/main``). On ``main``'s tip
    (merge base is ``HEAD``): ``HEAD``'s parent. ``ReleaseError`` when no ``main`` is found.
    """
    head = git(repo_root, "rev-parse", "HEAD")
    if head is None:
        return None
    base = next((b for ref in MAIN_REFS if (b := git(repo_root, "merge-base", "HEAD", ref))), None)
    if base is None:
        raise ReleaseError(
            "no main branch to check released versions against: fetch main or set RELEASES_BASE"
        )
    return git(repo_root, "rev-parse", "HEAD~1") if base == head else base


def history_issues(base: str, repo_root: Path = REPO_ROOT, *, git: GitRunner = _git) -> list[str]:
    """Releases registered at commit ``base`` that the tree's register removed."""
    path = releases_file(repo_root)
    relative = path.relative_to(repo_root).as_posix()
    text = git(repo_root, "show", f"{base}:{relative}")
    if text is None:
        if git(repo_root, "cat-file", "-e", f"{base}^{{commit}}") is None:
            return [f"release base {base!r} is not a commit"]
        return []  # the register did not exist yet at the base
    try:
        earlier = parse_register(text, f"{base}:{relative}")
        now = load_register(path)
    except ReleaseError as exc:
        return [str(exc)]
    return removed_releases(earlier, now)


def release_issues(
    repo_root: Path = REPO_ROOT, *, base: str | None = None, git: GitRunner = _git
) -> list[str]:
    """Every register, definition, pin and (given ``base``) history problem; empty when sound."""
    try:
        register = load_register(releases_file(repo_root))
    except ReleaseError as exc:
        return [str(exc)]
    a_root = agents_root(repo_root)
    issues = registration_issues(
        ReleaseKind.AGENT,
        agent_names(a_root),
        lambda name: load_agent(a_root, name).digest,
        register,
    )
    issues += stage_release_issues(repo_root, register)
    issues += registration_issues(
        ReleaseKind.WORKFLOW,
        sorted(
            path.parent.relative_to(workflows_root(repo_root)).as_posix()
            for path in workflows_root(repo_root).glob("*/*/workflow.yaml")
        ),
        lambda name: tree_workflow_digest(name, repo_root, register),
        register,
    )
    issues += agent_pin_issues(a_root)
    issues += import_issues(repo_root)
    if base is not None:
        issues += history_issues(base, repo_root, git=git)
    return issues
