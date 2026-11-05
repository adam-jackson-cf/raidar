"""Lock-time checks of a package workflow: the ST family beside the runtime gate's RT0-RT6.

Refused when:

- **ST1** the package's contract is unsupported or the package is invalid (raised while the
  package is resolved, ``subject.releases``; a prompt that names a token the package never
  defines is the same refusal);
- **ST2** a required primitive is unsupported;
- **ST3** a declared input has no earlier producer;
- **ST4** the digest differs from the register (raised while the package is resolved);
- **ST5** ``uv.lock`` is stale, contains an sdist or a source, or lacks a wheel for a pack
  architecture;
- **ST6** the build prompt misses a package's frame slot, repeats one, or names an unknown one;
- **ST7** two packages claim the same command, skill, output path or token.

Everything here reads data; nothing a package ships is imported or run on the host (the one
subprocess is ``uv lock --check --offline`` over the package's ``pyproject.toml``).
"""

from __future__ import annotations

import subprocess
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from raidar_contract.package.manifest import StagePackage
from raidar_contract.package.tokens import PLACEHOLDER, TASK_SLOT, token_name
from raidar_contract.references import Release
from raidar_contract.workflow import PackageJudge

from raidar.contract.uv import locked_uv
from raidar.contract.wheels import uv_lock_refusals
from raidar.subject.primitive.registry import SUPPORTED_CONTRACTS

LOCK_PATH = "tool/uv.lock"
LockCheck = Callable[[Path], str | None]
"""``uv lock --check`` over a package's ``tool/`` folder: why the lock is stale, or ``None``."""


@dataclass(frozen=True)
class ResolvedStage:
    """A package of a workflow: its release, the package at that release and effective params."""

    release: Release
    package: StagePackage
    params: Mapping[str, object]

    @property
    def leaf(self) -> str:
        return self.package.name

    @property
    def entry(self) -> dict[str, object]:
        """What the workflow's identity folds in for this package."""
        return {
            "use": str(self.release),
            "sha256": self.package.digest,
            "params": dict(self.params),
        }


def uv_lock_stale(tool: Path) -> str | None:
    """Why ``uv lock --check --offline`` refuses ``tool`` (``None``: the lock is current)."""
    try:
        done = subprocess.run(
            [locked_uv(), "lock", "--check", "--offline"],
            cwd=tool,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"uv lock --check could not run: {error}"
    if done.returncode == 0:
        return None
    lines = (done.stderr or done.stdout).strip().splitlines()
    return lines[-1] if lines else f"uv lock --check exited {done.returncode}"


def _contract_issues(stage: ResolvedStage) -> list[str]:
    return [
        f"ST2: {stage.release} requires {contract!r} ({label}), which this Raidar does not support"
        for label, contract in sorted(stage.package.manifest.requires.items())
        if contract not in SUPPORTED_CONTRACTS
    ]


def _input_issues(stages: Sequence[ResolvedStage]) -> list[str]:
    issues = []
    for index, stage in enumerate(stages):
        earlier = {item.leaf: item.package.manifest.outputs for item in stages[:index]}
        for label, wanted in sorted(stage.package.manifest.inputs.items()):
            producer, _, output = wanted.from_.partition(".")
            declared = earlier.get(producer, {}).get(output)
            if declared is None:
                issues.append(
                    f"ST3: {stage.release} input {label!r} needs {wanted.from_}, which no "
                    "earlier package in the workflow produces"
                )
            elif declared.contract != wanted.contract:
                issues.append(
                    f"ST3: {stage.release} input {label!r} needs {wanted.contract} from "
                    f"{wanted.from_}, which declares {declared.contract}"
                )
    return issues


def _lock_issues(stage: ResolvedStage, lock_check: LockCheck) -> list[str]:
    files = {item.path: item for item in stage.package.files}
    lock = files.get(LOCK_PATH)
    if lock is None:
        if stage.package.manifest.tool is None:
            return []
        return [f"ST5: {stage.release} has a tool but no {LOCK_PATH}: run `make stage-lock`"]
    text = lock.data.decode("utf-8", errors="replace")
    refused = uv_lock_refusals(text)
    if refused:
        return [f"ST5: {stage.release} {LOCK_PATH}: {reason}" for reason in refused]
    stale = lock_check(stage.package.root / "tool")
    return [f"ST5: {stage.release} {LOCK_PATH} is stale: {stale}"] if stale else []


def _slot_issues(build: str, stages: Sequence[ResolvedStage]) -> list[str]:
    slots = [token_name(stage.leaf) for stage in stages]
    issues = [
        f"ST6: package slot %%%{TASK_SLOT}%%% is reserved for the task"
        for slot in slots
        if slot == TASK_SLOT
    ][:1]
    counts = Counter(PLACEHOLDER.findall(build))
    issues += [
        f"ST6: the build prompt names %%%{slot}%%%, which no enabled package owns"
        for slot in sorted(counts.keys() - {TASK_SLOT, *slots})
    ]
    issues.extend(
        f"ST6: the build prompt needs %%%{slot}%%% once, not {counts[slot]} times"
        for slot in dict.fromkeys([TASK_SLOT, *slots])
        if counts[slot] != 1
    )
    return issues


def _claims(stages: Sequence[ResolvedStage]) -> dict[tuple[str, str], list[str]]:
    claims: dict[tuple[str, str], list[str]] = {}
    for stage in stages:
        manifest = stage.package.manifest
        owned = [
            *(("command", name) for name in (manifest.tool.commands if manifest.tool else {})),
            *(("skill", name) for name in manifest.skills),
            *(("output path", output.path) for output in manifest.outputs.values()),
            *(("token", name) for name in manifest.tokens),
            ("slot token", token_name(stage.leaf)),
        ]
        for claim in owned:
            claims.setdefault(claim, []).append(str(stage.release))
    return claims


def _collision_issues(stages: Sequence[ResolvedStage]) -> list[str]:
    return [
        f"ST7: {kind} {name!r} is claimed by {' and '.join(owners)}"
        for (kind, name), owners in sorted(_claims(stages).items())
        if len(owners) > 1
    ]


def _prompt_issues(stage: ResolvedStage) -> list[str]:
    manifest = stage.package.manifest
    known = {*manifest.param_tokens(), *manifest.tokens}
    unknown = sorted(set(PLACEHOLDER.findall(stage.package.prompt)) - known)
    return [
        f"ST1: {stage.release} prompt.md names {name}, which is neither a param nor a token"
        for name in unknown
    ]


def _judge_issues(judge: PackageJudge, stages: Sequence[ResolvedStage]) -> list[str]:
    producer, _, output = judge.criteria.partition(".")
    declared = next(
        (stage.package.manifest.outputs.get(output) for stage in stages if stage.leaf == producer),
        None,
    )
    issues = []
    if declared is None or declared.contract != "criteria/1":
        issues.append(
            f"ST3: judge needs criteria/1 from {judge.criteria}, which no package produces"
        )
    if (
        sum(check.kind == "pytest" for stage in stages for check in stage.package.manifest.gate)
        != 1
    ):
        issues.append("ST3: judge requires exactly one pytest-kind gate check")
    return issues


@cache
def _cached_lock_stale(tool: Path) -> str | None:
    return uv_lock_stale(tool)


def package_issues(
    build_prompt: str,
    stages: Sequence[ResolvedStage],
    *,
    lock_check: LockCheck | None = None,
    judge: PackageJudge | None = None,
) -> list[str]:
    """Every ST2, ST3, ST5, ST6, ST7 (and prompt-token ST1) refusal of one workflow's packages."""
    issues: list[str] = []
    for stage in stages:
        issues += _contract_issues(stage)
        issues += _prompt_issues(stage)
        issues += _lock_issues(stage, lock_check or _cached_lock_stale)
    issues += _input_issues(stages)
    if judge is not None:
        issues += _judge_issues(judge, stages)
    issues += _slot_issues(build_prompt, stages)
    issues += _collision_issues(stages)
    return issues
