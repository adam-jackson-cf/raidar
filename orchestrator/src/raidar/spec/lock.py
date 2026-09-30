"""``raidar lock``: resolve every reference and file to content hashes (A2, N3).

The lock is key-sorted, indented JSON with no timestamps and only
experiment-relative paths, so relocking unchanged inputs is byte-identical.
It holds the effective spec (file + CLI overrides) and four hashed sections,
each with its own digest under ``sections``:

- ``execution``: what a trial runs: the experiment design (problems, runtime,
  agents, protocol, factors, conditions, arms, blocks, seed), every problem
  and component file and reference digest, one digest per arm over its agent
  and fully hashed condition, the Harbor and harness versions and any tools
  those references pin. Its digest **is** ``lock_sha256``, the identity every
  run records in ``run.json``: runs and baselines match a lock through it.
- ``assessment``: how runs are scored: the declared scorers with their
  manifests, implementation digests and pinned tools, the scorer config and
  the headline ScorePolicy (declared, or the default chosen by K). A change
  here means "rescore into the Assessment store", never "the runs are stale".
- ``analysis``: the baseline arm and the pinned verdict thresholds.
- ``toolchain``: Python, Raidar, LiteLLM and the spec/lock rules digest.

``document_sha256`` covers the whole document (sections, spec, overrides and
the experiment file digest). ``raidar lock --check`` compares section by
section and names the stale ones (``stale_sections``).

Missing resolvers, unknown references and missing files are collected and
reported together as field-level ``LockError`` issues; nothing is skipped.
"""

from __future__ import annotations

import json
import os
import platform
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

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
from raidar.spec.source_digest import SOURCE_DIGEST_SCHEME, python_source_digest
from raidar.spec.validation import FieldIssue

LOCK_VERSION = 2
LOCK_SUFFIX = ".lock.json"
IGNORED_TREE_NAMES = frozenset({".DS_Store", "__pycache__", ".git"})
TOOLCHAIN_PACKAGES = ("litellm", "raidar")
SECTIONS = ("execution", "assessment", "analysis", "toolchain")
DOCUMENT = "document"
"""Pseudo-section: the lock version, experiment file digests, overrides and effective spec."""
EXECUTION_REF_KINDS = (RefKind.PROBLEM, RefKind.COMPONENT)
ASSESSMENT_REF_KINDS = (RefKind.SCORER, RefKind.POLICY)
EXECUTION_SPEC_FIELDS = (
    "id",
    "problems",
    "runtime",
    "agents",
    "protocol",
    "factors",
    "conditions",
    "arms",
)
EXECUTION_ANALYSIS_FIELDS = ("blocks", "seed")
"""The analysis fields that shape the plan; ``concurrency`` is per machine (PlanOptions)."""
STALE_CONSEQUENCES = {
    "execution": "execution inputs changed; runs under the old lock_sha256 are another experiment",
    "assessment": "scorers or the headline policy changed; rescore runs (they still match)",
    "analysis": "baseline arm or verdict thresholds changed; re-analyse (runs still match)",
    "toolchain": "Python, Raidar, LiteLLM or the spec rules changed; relock (runs still match)",
    DOCUMENT: "the experiment file or overrides changed; relock (runs still match)",
}


class StaleLockError(Exception):
    """An existing lock file is missing, invalid, or differs from a fresh lock."""

    def __init__(self, message: str, stale: tuple[str, ...] = ()) -> None:
        self.stale = stale
        super().__init__(message)


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
    """Digest of this package's code, so a change to the spec/policy/lock rules relocks.

    The installed ``raidar`` version is static between releases; this digest is
    what ties a lock to the exact validation, policy and lock rules used. Each
    module is hashed as its normalised syntax tree, so docstring and comment
    edits do not relock.
    """
    package_dir = Path(__file__).resolve().parent
    return digest_json(
        {
            "scheme": SOURCE_DIGEST_SCHEME,
            "modules": {
                path.name: python_source_digest(path) for path in sorted(package_dir.glob("*.py"))
            },
        }
    )


def _package_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "not-installed"


def toolchain_versions() -> dict[str, str]:
    """Versions that are recorded (N3) but are not part of any run's identity."""
    versions = {"python": platform.python_version(), "raidar_spec": spec_source_digest()}
    versions.update({package: _package_version(package) for package in TOOLCHAIN_PACKAGES})
    return versions


def harbor_version() -> str:
    return _package_version("harbor")


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
    tools_by_kind: dict[str, dict[str, str]] = field(default_factory=dict)

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
            self.tools_by_kind.setdefault(resolved.ref.kind.value, {})[tool] = tool_version
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

    def grouped(self, kinds: tuple[RefKind, ...]) -> tuple[dict[str, object], dict[str, str]]:
        """(references, pinned tools) of the given reference kinds."""
        references: dict[str, object] = {
            kind.value: self.references.get(kind.value, {}) for kind in kinds
        }
        tools: dict[str, str] = {}
        for kind in kinds:
            tools.update(self.tools_by_kind.get(kind.value, {}))
        return references, dict(sorted(tools.items()))

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
    spec_json = spec.to_json()
    execution_refs, execution_tools = builder.grouped(EXECUTION_REF_KINDS)
    assessment_refs, assessment_tools = builder.grouped(ASSESSMENT_REF_KINDS)
    body: dict[str, object] = {
        "lock_version": LOCK_VERSION,
        "experiment": {
            "id": spec.id,
            "source": Path(os.path.relpath(loaded.path, lock_path.resolve().parent)).as_posix(),
            "source_sha256": loaded.source_sha256,
            "spec_sha256": digest_json(spec_json),
        },
        "overrides": loaded.overrides.to_json(),
        "spec": spec_json,
        "execution": execution_section(
            spec_json,
            problems=resolved_problems,
            files=builder.files,
            references=execution_refs,
            conditions=conditions,
            arms=builder.arms(conditions),
            versions={
                "harbor": harbor_version(),
                "harnesses": {
                    agent.id: {"harness": agent.harness.value, "version": agent.version}
                    for agent in spec.agents
                },
            },
            tools=execution_tools,
        ),
        "assessment": {
            "spec": spec_json["assessment"],
            "headline_policy": headline,
            "references": assessment_refs,
            "tools": assessment_tools,
        },
        "analysis": analysis_section(spec_json),
        "toolchain": toolchain_versions(),
    }
    return seal_lock(body)


def execution_section(
    spec_json: Mapping[str, Any],
    *,
    problems: object,
    files: object,
    references: object,
    conditions: object,
    arms: object,
    versions: object,
    tools: object,
) -> dict[str, object]:
    """The ``execution`` section: everything a run's identity (``lock_sha256``) covers."""
    analysis = spec_json["analysis"]
    return {
        "design": {
            **{name: spec_json[name] for name in EXECUTION_SPEC_FIELDS},
            **{name: analysis[name] for name in EXECUTION_ANALYSIS_FIELDS},
        },
        "problems": problems,
        "files": files,
        "references": references,
        "conditions": conditions,
        "arms": arms,
        "versions": versions,
        "tools": tools,
    }


def analysis_section(spec_json: Mapping[str, Any]) -> dict[str, object]:
    """Baseline arm and verdict thresholds: ``spec.analysis`` minus the plan-shaping fields."""
    excluded = {*EXECUTION_ANALYSIS_FIELDS, "concurrency"}
    return {key: value for key, value in spec_json["analysis"].items() if key not in excluded}


def seal_lock(body: Mapping[str, Any]) -> dict[str, Any]:
    """Add ``sections``, ``lock_sha256`` (the execution digest) and ``document_sha256``."""
    sections = {name: digest_json(body[name]) for name in SECTIONS}
    unsigned = {**body, "sections": sections}
    return {
        **unsigned,
        "lock_sha256": sections["execution"],
        "document_sha256": digest_json(unsigned),
    }


def _shape_issues(lock: Mapping[str, Any]) -> list[str]:
    if lock.get("lock_version") != LOCK_VERSION:
        return [f"lock_version is {lock.get('lock_version')!r}, expected {LOCK_VERSION}; relock"]
    missing = [name for name in SECTIONS if name not in lock]
    return [f"missing section(s) {', '.join(missing)}"] if missing else []


def lock_integrity_issues(lock: Mapping[str, Any]) -> list[str]:
    """Why ``lock`` is not a sealed lock of this version (empty when it is)."""
    shape = _shape_issues(lock)
    if shape:
        return shape
    recorded = lock.get("sections") or {}
    issues = [
        f"section {name} does not match its digest"
        for name in SECTIONS
        if recorded.get(name) != digest_json(lock[name])
    ]
    if lock.get("lock_sha256") != digest_json(lock["execution"]):
        issues.append("lock_sha256 is not the digest of the execution section")
    unsigned = {key: value for key, value in lock.items() if key != "document_sha256"}
    unsigned.pop("lock_sha256", None)
    if lock.get("document_sha256") != digest_json(unsigned):
        issues.append("document_sha256 does not match the lock document")
    return issues


def stale_sections(existing: Mapping[str, Any], current: Mapping[str, Any]) -> tuple[str, ...]:
    """The sections (and ``document``) where ``existing`` differs from a fresh ``current`` lock."""
    stale = [name for name in SECTIONS if existing.get(name) != current.get(name)]
    ignored = {*SECTIONS, "sections", "lock_sha256", "document_sha256"}
    keys = (set(existing) | set(current)) - ignored
    if any(existing.get(key) != current.get(key) for key in keys):
        stale.append(DOCUMENT)
    return tuple(stale)


def describe_staleness(stale: tuple[str, ...]) -> str:
    return "; ".join(f"{name}: {STALE_CONSEQUENCES[name]}" for name in stale)


def render_lock(lock: Mapping[str, object]) -> bytes:
    return pretty_canonical_json_bytes(lock)


def _unsealed(lock: Mapping[str, Any]) -> dict[str, Any]:
    sealed = {"sections", "lock_sha256", "document_sha256"}
    return {key: value for key, value in lock.items() if key not in sealed}


def check_lock_file(
    lock_path: Path, current: Mapping[str, Any], *, ignore_host_python: bool = False
) -> str | None:
    """Raise ``StaleLockError`` unless ``lock_path`` holds exactly ``current``.

    The error names every stale section and what its change means. With
    ``ignore_host_python`` the one machine-dependent field, ``toolchain.python``,
    may differ (a CI host on another Python); every other field of every
    section must still match, and the returned note says what was ignored.
    """
    if not lock_path.is_file():
        raise StaleLockError(f"{lock_path} does not exist; run `raidar lock` first")
    try:
        existing = json.loads(lock_path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise StaleLockError(f"{lock_path} is not readable JSON ({exc}); relock") from exc
    if not isinstance(existing, dict):
        raise StaleLockError(f"{lock_path} is not a JSON object; relock")
    issues = lock_integrity_issues(existing)
    if issues:
        raise StaleLockError(f"{lock_path} is not a valid lock ({'; '.join(issues)})")
    note = None
    locked_python = (existing.get("toolchain") or {}).get("python")
    host_python = current["toolchain"]["python"]
    if ignore_host_python and locked_python != host_python:
        body = _unsealed(current)
        body["toolchain"] = {**current["toolchain"], "python": locked_python}
        current = seal_lock(body)
        note = f"host Python {host_python} differs from the locked {locked_python} (ignored)"
    stale = stale_sections(existing, current)
    if stale:
        raise StaleLockError(
            f"{lock_path} is out of date; run `raidar lock` again. "
            f"Stale: {describe_staleness(stale)}",
            stale,
        )
    if lock_path.read_bytes() != render_lock(current):
        raise StaleLockError(f"{lock_path} is not in canonical form; run `raidar lock` again")
    return note
