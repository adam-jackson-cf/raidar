"""``raidar lock``: resolve every reference and file to content hashes (A2, N3).

The lock is key-sorted, indented JSON with no timestamps and only
experiment-relative paths, so relocking unchanged inputs is byte-identical.
It records:

- the effective spec (file + CLI overrides) and the source file digest;
- sha256 of every referenced file (component files, local problem task dirs);
- every registry reference resolved through a ``RegistryResolver``;
- the headline ScorePolicy (declared, or the default chosen by K);
- one digest per arm over its agent and fully hashed condition;
- Harbor, Raidar, LiteLLM, Python, harness and scorer-tool versions.

Missing resolvers, unknown references and missing files are collected and
reported together as field-level ``LockError`` issues; nothing is skipped.
"""

from __future__ import annotations

import os
import platform
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from raidar.spec.canonical import digest_json, pretty_canonical_json_bytes, sha256_file
from raidar.spec.loader import LoadedExperiment
from raidar.spec.policies import DEFAULT_POLICY_REGISTRY, PolicyResolver, ScorePolicyRegistry
from raidar.spec.references import (
    RefKind,
    RegistryRef,
    RegistryReferenceError,
    ResolvedReference,
    ResolverSet,
)
from raidar.spec.schema import ExperimentSpec, ProblemEntry
from raidar.spec.validation import FieldIssue

LOCK_VERSION = 1
LOCK_SUFFIX = ".lock.json"
IGNORED_TREE_NAMES = frozenset({".DS_Store", "__pycache__", ".git"})
VERSIONED_PACKAGES = ("harbor", "litellm", "raidar")


class LockError(Exception):
    def __init__(self, source: str, issues: list[FieldIssue]) -> None:
        self.issues = tuple(sorted(issues))
        lines = "\n".join(f"  {issue.render()}" for issue in self.issues)
        super().__init__(f"{source}: cannot lock, {len(self.issues)} issue(s)\n{lines}")


def default_lock_path(experiment_path: Path) -> Path:
    """The lock next to an experiment file, keyed by the file's name.

    ``experiment.yaml`` -> ``experiment.lock.json``; ``oneshot.experiment.yaml``
    -> ``oneshot.experiment.lock.json``, so two experiments in one directory
    never overwrite each other's lock.
    """
    name = experiment_path.name
    stem = next(
        (name[: -len(suffix)] for suffix in (".yaml", ".yml") if name.endswith(suffix)), name
    )
    return experiment_path.with_name(f"{stem}{LOCK_SUFFIX}")


def default_resolvers(policies: ScorePolicyRegistry = DEFAULT_POLICY_REGISTRY) -> ResolverSet:
    """Built-in policy resolver plus entry-point resolvers for other kinds."""
    return ResolverSet([PolicyResolver(policies)])


def spec_source_digest() -> str:
    """sha256 over this package's sources, so spec/policy/lock code changes relock.

    The installed ``raidar`` version is static between releases; this digest is
    what ties a lock to the exact validation, policy and lock rules used.
    """
    package_dir = Path(__file__).resolve().parent
    return digest_json({path.name: sha256_file(path) for path in sorted(package_dir.glob("*.py"))})


def runtime_versions() -> dict[str, str]:
    versions = {"python": platform.python_version(), "raidar_spec": spec_source_digest()}
    for package in VERSIONED_PACKAGES:
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def _hash_tree(
    base_dir: Path, relative: str, *, exclude: frozenset[Path] = frozenset()
) -> dict[str, dict[str, object]]:
    """sha256 + size of a file, or of every file under a directory (sorted).

    ``exclude`` holds resolved paths never hashed (the lock file itself, so a
    directory that contains the lock still relocks byte-identically). Paths
    that resolve outside ``base_dir`` (for example through a symlink) are refused.
    """
    base = base_dir.resolve()
    root = base_dir / relative
    if not root.resolve().is_relative_to(base):
        raise ValueError(f"referenced path {relative!r} resolves outside the experiment directory")
    if root.is_file():
        candidates = [root]
    elif root.is_dir():
        candidates = sorted(
            path
            for path in root.rglob("*")
            if path.is_file() and not IGNORED_TREE_NAMES.intersection(path.relative_to(root).parts)
        )
    else:
        raise FileNotFoundError(f"referenced path {relative!r} does not exist")
    tree: dict[str, dict[str, object]] = {}
    for path in candidates:
        resolved = path.resolve()
        if resolved in exclude:
            continue
        key = Path(os.path.normpath(os.path.relpath(path, base_dir))).as_posix()
        if not resolved.is_relative_to(base):
            raise ValueError(f"{key!r} resolves outside the experiment directory")
        tree[key] = {"sha256": sha256_file(path), "size": path.stat().st_size}
    return tree


def task_checkpoint_count(task_dir: Path) -> int:
    """K of a Harbor task: the number of ``[[steps]]`` (1 for a single-step task)."""
    with (task_dir / "task.toml").open("rb") as handle:
        document = tomllib.load(handle)
    steps = document.get("steps")
    return len(steps) if isinstance(steps, list) and steps else 1


@dataclass
class _LockBuilder:
    loaded: LoadedExperiment
    resolvers: ResolverSet
    policies: ScorePolicyRegistry
    lock_path: Path
    issues: list[FieldIssue] = field(default_factory=list)
    files: dict[str, dict[str, object]] = field(default_factory=dict)
    references: dict[str, dict[str, dict[str, object]]] = field(default_factory=dict)
    tool_versions: dict[str, str] = field(default_factory=dict)

    @property
    def spec(self) -> ExperimentSpec:
        return self.loaded.spec

    def hash_path(self, loc: str, relative: str) -> dict[str, dict[str, object]]:
        try:
            tree = _hash_tree(
                self.loaded.base_dir, relative, exclude=frozenset({self.lock_path.resolve()})
            )
        except (FileNotFoundError, ValueError) as exc:
            self.issues.append(FieldIssue(loc, str(exc)))
            return {}
        if not tree:
            self.issues.append(FieldIssue(loc, f"referenced directory {relative!r} is empty"))
        self.files.update(tree)
        return tree

    def resolve(self, loc: str, kind: RefKind, text: str) -> ResolvedReference | None:
        try:
            ref = RegistryRef.parse(kind, text)
            resolver = self.resolvers.resolver_for(kind)
            if resolver is None:
                self.issues.append(
                    FieldIssue(loc, f"no resolver registered for {kind.value} reference {text!r}")
                )
                return None
            resolved = resolver.resolve(ref, base_dir=self.loaded.base_dir)
        except RegistryReferenceError as exc:
            self.issues.append(FieldIssue(loc, str(exc)))
            return None
        self._record(loc, resolved)
        return resolved

    def _record(self, loc: str, resolved: ResolvedReference) -> None:
        self.references.setdefault(resolved.ref.kind.value, {})[str(resolved.ref)] = {
            "digest": resolved.digest,
            "metadata": dict(resolved.metadata),
        }
        for tool, tool_version in resolved.tool_versions.items():
            recorded = self.tool_versions.setdefault(tool, tool_version)
            if recorded != tool_version:
                self.issues.append(
                    FieldIssue(loc, f"tool {tool!r} pinned to both {recorded} and {tool_version}")
                )

    def problem(self, index: int, entry: ProblemEntry) -> dict[str, object] | None:
        loc = f"problems.{index}"
        if entry.path is not None:
            tree = self.hash_path(f"{loc}.path", entry.path)
            if not tree:
                return None
            try:
                checkpoints = task_checkpoint_count(self.loaded.base_dir / entry.path)
            except (OSError, tomllib.TOMLDecodeError) as exc:
                self.issues.append(FieldIssue(f"{loc}.path", f"unreadable task.toml: {exc}"))
                return None
            return {"label": entry.label, "checkpoints": checkpoints, "digest": digest_json(tree)}
        resolved = self.resolve(f"{loc}.ref", RefKind.PROBLEM, entry.ref or "")
        if resolved is None:
            return None
        reported = resolved.metadata.get("checkpoints")
        if not isinstance(reported, int) or isinstance(reported, bool) or reported < 1:
            self.issues.append(
                FieldIssue(f"{loc}.ref", "problem resolver must report integer checkpoints >= 1")
            )
            return None
        return {"label": entry.label, "checkpoints": reported, "digest": resolved.digest}

    def headline_policy(self, problems: list[dict[str, object]]) -> dict[str, object] | None:
        loc = "assessment.headline_policy"
        counts = sorted({int(str(problem["checkpoints"])) for problem in problems})
        declared = self.spec.assessment.headline_policy
        if declared is not None:
            policy = self.policies.get(declared)
            unsupported = [k for k in counts if not policy.supports(k)]
            if unsupported:
                self.issues.append(
                    FieldIssue(loc, f"{declared} does not support problems with K={unsupported}")
                )
                return None
            selection = "declared"
        elif len(counts) != 1:
            self.issues.append(
                FieldIssue(
                    loc,
                    f"problems have mixed checkpoint counts {counts}; "
                    "declare a headline_policy that supports all of them",
                )
            )
            return None
        else:
            policy = self.policies.default_for_checkpoints(counts[0])
            selection = "default_by_k"
        if self.resolve(loc, RefKind.POLICY, policy.ref) is None:
            return None
        return {"ref": policy.ref, "selection": selection, "checkpoints": counts}

    def conditions(self) -> dict[str, object]:
        hashed: dict[str, object] = {}
        for c_index, condition in enumerate(self.spec.conditions):
            components = []
            for u_index, component in enumerate(condition.components):
                loc = f"conditions.{c_index}.components.{u_index}"
                resolved = self.resolve(f"{loc}.use", RefKind.COMPONENT, component.use)
                files: dict[str, dict[str, object]] = {}
                for f_index, relative in enumerate(component.files):
                    files.update(self.hash_path(f"{loc}.files.{f_index}", relative))
                components.append(
                    {
                        "use": component.use,
                        "use_digest": resolved.digest if resolved else None,
                        "with": component.with_,
                        "files": {path: entry["sha256"] for path, entry in files.items()},
                    }
                )
            hashed[condition.id] = components
        return hashed

    def arms(self, conditions: Mapping[str, object]) -> dict[str, object]:
        arms: dict[str, object] = {}
        for arm in self.spec.arms:
            agent = self.spec.agent(arm.agent).model_dump(mode="json", exclude={"id"})
            arms[arm.id] = {
                "agent": arm.agent,
                "condition": arm.condition,
                "digest": digest_json({"agent": agent, "condition": conditions[arm.condition]}),
            }
        return arms


def build_lock(
    loaded: LoadedExperiment,
    *,
    lock_path: Path,
    resolvers: ResolverSet | None = None,
    policies: ScorePolicyRegistry = DEFAULT_POLICY_REGISTRY,
) -> dict[str, object]:
    """Resolve the experiment into a lock document; raise ``LockError`` on any gap."""
    builder = _LockBuilder(
        loaded=loaded,
        resolvers=resolvers or default_resolvers(policies),
        policies=policies,
        lock_path=lock_path,
    )
    spec = loaded.spec
    problems = [builder.problem(index, entry) for index, entry in enumerate(spec.problems)]
    for index, scorer in enumerate(spec.assessment.scorers):
        builder.resolve(f"assessment.scorers.{index}", RefKind.SCORER, scorer)
    conditions = builder.conditions()
    resolved_problems = [problem for problem in problems if problem is not None]
    headline = (
        builder.headline_policy(resolved_problems)
        if len(resolved_problems) == len(problems)
        else None
    )
    if builder.issues:
        raise LockError(str(loaded.path), builder.issues)
    body: dict[str, object] = {
        "lock_version": LOCK_VERSION,
        "experiment": {
            "id": spec.id,
            "source": Path(os.path.relpath(loaded.path, lock_path.resolve().parent)).as_posix(),
            "source_sha256": loaded.source_sha256,
            "spec_sha256": digest_json(spec.to_json()),
        },
        "overrides": loaded.overrides.to_json(),
        "spec": spec.to_json(),
        "problems": resolved_problems,
        "files": builder.files,
        "references": builder.references,
        "conditions": conditions,
        "arms": builder.arms(conditions),
        "headline_policy": headline,
        "versions": {
            **runtime_versions(),
            "harnesses": {
                agent.id: {"harness": agent.harness.value, "version": agent.version}
                for agent in spec.agents
            },
            "tools": builder.tool_versions,
        },
    }
    return {**body, "lock_sha256": digest_json(body)}


def render_lock(lock: Mapping[str, object]) -> bytes:
    return pretty_canonical_json_bytes(lock)
