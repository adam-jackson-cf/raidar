"""``raidar lock``: resolve every reference and file to content hashes (A2, N3).

The lock is key-sorted, indented JSON with no timestamps and only
experiment-relative paths, so relocking unchanged inputs is byte-identical.
It holds the effective spec (file + CLI overrides) and four hashed sections,
each with its own digest under ``sections``:

- ``execution``: what a trial runs: the experiment design (kind, problems,
  runtime, environment, agents, protocol, arms, attempts, seed), every problem
  file and the problem, agent and workflow reference digests, one identity per
  arm (design 5.4: agent release and CLI pin, model and reasoning, workflow
  release, the workflow's resolved content hash and a stage code digest per
  enabled stage type, plus a digest over all of it), the Harbor and agent
  versions and any tools those references pin. Its digest **is**
  ``lock_sha256``, the identity every job records in ``job.json``: jobs and
  baselines match a lock through it. The kind is part of it, so relabelling an
  experiment after data exists makes a new experiment.
- ``assessment``: how jobs are scored: the declared scorers with their
  manifests, implementation digests and pinned tools, the scorer config and
  the headline metric (declared, or the default chosen by K). A change
  here means "rescore into the Assessment store", never "the jobs are stale".
- ``analysis``: the baseline arm and the pinned verdict thresholds.
- ``toolchain``: Python, Raidar, LiteLLM and the spec/lock rules digest.

Before anything is written, the runtime-compatibility gate
(``raidar.design.experiment.runtime_gate``, ``RT0``-``RT6``) checks the effective spec against its
runtime pack, so no lock exists for a design its runtime cannot run.

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

from raidar.contract.canonical import (
    IGNORED_TREE_NAMES,
    digest_json,
    pretty_canonical_json_bytes,
    sha256_file,
)
from raidar.contract.references import (
    RefKind,
    RegistryRef,
    RegistryReferenceError,
    ResolvedReference,
    ResolverSet,
)
from raidar.contract.source_digest import SOURCE_DIGEST_SCHEME, python_source_digest
from raidar.design.experiment.arm import ArmResolutionError, resolve_arm
from raidar.design.experiment.loader import LoadedExperiment
from raidar.design.experiment.runtime_gate import GateInputs, PackLoader, runtime_gate_issues
from raidar.design.experiment.schema import ExperimentSpec, ProblemEntry
from raidar.design.experiment.validation import FieldIssue
from raidar.scoring.framework.metrics import (
    DEFAULT_METRIC_REGISTRY,
    MetricRegistry,
    MetricResolver,
)
from raidar.subject.releases import AgentResolver, WorkflowResolver

LOCK_VERSION = 4
"""4: arms are an agent release plus a workflow release, with their full identity."""
LOCK_SUFFIX = ".lock.json"
TOOLCHAIN_PACKAGES = ("litellm", "raidar")
SECTIONS = ("execution", "assessment", "analysis", "toolchain")
DOCUMENT = "document"
"""Pseudo-section: the lock version, experiment file digests, overrides and effective spec."""
EXECUTION_REF_KINDS = (RefKind.PROBLEM, RefKind.AGENT, RefKind.WORKFLOW)
ASSESSMENT_REF_KINDS = (RefKind.SCORER, RefKind.METRIC)
EXECUTION_SPEC_FIELDS = (
    "kind",
    "id",
    "problems",
    "runtime",
    "environment",
    "agents",
    "protocol",
    "arms",
    "varies",
)
EXECUTION_ANALYSIS_FIELDS = ("attempts", "seed")
"""The analysis fields that shape the plan; ``concurrency`` is per machine (PlanOptions)."""
STALE_CONSEQUENCES = {
    "execution": "execution inputs changed; jobs under the old lock_sha256 are another experiment",
    "assessment": "scorers or the headline metric changed; rescore jobs (they still match)",
    "analysis": "baseline arm or verdict thresholds changed; re-analyse (jobs still match)",
    "toolchain": "Python, Raidar, LiteLLM or the spec rules changed; relock (jobs still match)",
    DOCUMENT: "the experiment file or overrides changed; relock (jobs still match)",
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


def default_resolvers(metrics: MetricRegistry = DEFAULT_METRIC_REGISTRY) -> ResolverSet:
    """Built-in metric, agent and workflow resolvers plus entry-point resolvers for others."""
    return ResolverSet([MetricResolver(metrics), AgentResolver(), WorkflowResolver()])


SPEC_EXTERNAL_MODULES = (
    "contract/canonical.py",
    "contract/references.py",
    "contract/source_digest.py",
    "scoring/framework/metrics.py",
)
"""Modules outside this package that are part of the spec/metric/lock rules (below ``raidar/``)."""


def spec_source_digest() -> str:
    """Digest of this package's code and ``SPEC_EXTERNAL_MODULES``, so a change to the
    spec/metric/lock rules relocks.

    The installed ``raidar`` version is static between releases; this digest is
    what ties a lock to the exact validation, metric and lock rules used. Each
    module is hashed as its normalised syntax tree, so docstring and comment
    edits do not relock.
    """
    package_dir = Path(__file__).resolve().parent
    raidar_dir = package_dir.parents[1]
    modules = {path.name: python_source_digest(path) for path in sorted(package_dir.glob("*.py"))}
    modules.update(
        {
            relative: python_source_digest(raidar_dir / relative)
            for relative in SPEC_EXTERNAL_MODULES
        }
    )
    return digest_json({"scheme": SOURCE_DIGEST_SCHEME, "modules": modules})


def _package_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "not-installed"


def toolchain_versions() -> dict[str, str]:
    """Versions that are recorded (N3) but are not part of any job's identity."""
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
    metrics: MetricRegistry
    lock_path: Path
    issues: list[FieldIssue] = field(default_factory=list)
    files: dict[str, dict[str, object]] = field(default_factory=dict)
    references: dict[str, dict[str, dict[str, object]]] = field(default_factory=dict)
    tool_versions: dict[str, str] = field(default_factory=dict)
    tools_by_kind: dict[str, dict[str, str]] = field(default_factory=dict)
    resolved: dict[str, ResolvedReference] = field(default_factory=dict)

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
        self.resolved[loc] = resolved
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
        selected = self.problem_checkpoints(loc, entry, resolved.metadata)
        if selected is None:
            return None
        return {"label": entry.label, "checkpoints": selected, "digest": resolved.digest}

    def problem_checkpoints(
        self, loc: str, entry: ProblemEntry, metadata: Mapping[str, Any]
    ) -> int | None:
        reported = metadata.get("checkpoints")
        if not isinstance(reported, int) or isinstance(reported, bool) or reported < 1:
            self.issues.append(
                FieldIssue(f"{loc}.ref", "problem resolver must report integer checkpoints >= 1")
            )
            return None
        selected = reported
        if entry.checkpoints is not None:
            if entry.checkpoints > reported or metadata.get("mode") == "one_shot":
                self.issues.append(
                    FieldIssue(
                        f"{loc}.checkpoints",
                        f"cannot take the first {entry.checkpoints} checkpoint(s) of "
                        f"{entry.ref}, which has {reported}; select within a multi-checkpoint "
                        "reference (scb/<problem>@<version>)",
                    )
                )
            else:
                selected = entry.checkpoints
        return selected

    def metric(self, problems: list[dict[str, object]]) -> dict[str, object] | None:
        loc = "assessment.metric"
        counts = sorted({int(str(problem["checkpoints"])) for problem in problems})
        declared = self.spec.assessment.metric
        if declared is not None:
            metric = self.metrics.get(declared)
            unsupported = [k for k in counts if not metric.supports(k)]
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
                    "declare a headline metric that supports all of them",
                )
            )
            return None
        else:
            metric = self.metrics.default_for_checkpoints(counts[0])
            selection = "default_by_k"
        if self.resolve(loc, RefKind.METRIC, metric.ref) is None:
            return None
        return {"ref": metric.ref, "selection": selection, "checkpoints": counts}

    def grouped(self, kinds: tuple[RefKind, ...]) -> tuple[dict[str, object], dict[str, str]]:
        """(references, pinned tools) of the given reference kinds."""
        references: dict[str, object] = {
            kind.value: self.references.get(kind.value, {}) for kind in kinds
        }
        tools: dict[str, str] = {}
        for kind in kinds:
            tools.update(self.tools_by_kind.get(kind.value, {}))
        return references, dict(sorted(tools.items()))

    def releases(self) -> None:
        """Resolve every agent and workflow release (recorded under ``references``)."""
        for index, agent in enumerate(self.spec.agents):
            self.resolve(f"agents.{index}.release", RefKind.AGENT, agent.release)
        seen: set[str] = set()
        for index, arm in enumerate(self.spec.arms):
            if arm.workflow not in seen:
                seen.add(arm.workflow)
                self.resolve(f"arms.{index}.workflow", RefKind.WORKFLOW, arm.workflow)

    def arms(self) -> dict[str, dict[str, Any]]:
        """Each arm's full identity (``ResolvedArm.identity``)."""
        arms: dict[str, dict[str, Any]] = {}
        for index, arm in enumerate(self.spec.arms):
            try:
                arms[arm.id] = resolve_arm(self.spec, arm.id).identity()
            except ArmResolutionError as exc:
                self.issues.append(FieldIssue(f"arms.{index}", str(exc)))
        return arms


def build_lock(
    loaded: LoadedExperiment,
    *,
    lock_path: Path,
    resolvers: ResolverSet | None = None,
    metrics: MetricRegistry = DEFAULT_METRIC_REGISTRY,
    pack_loader: PackLoader | None = None,
) -> dict[str, object]:
    """Resolve the experiment into a lock document; raise ``LockError`` on any gap.

    Resolution gaps and the compatibility gate's ``RT*`` issues are reported
    together. ``pack_loader`` replaces the runtime pack registry (tests).
    """
    builder = _LockBuilder(
        loaded=loaded,
        resolvers=resolvers or default_resolvers(metrics),
        metrics=metrics,
        lock_path=lock_path,
    )
    spec = loaded.spec
    problems = [builder.problem(index, entry) for index, entry in enumerate(spec.problems)]
    for index, scorer in enumerate(spec.assessment.scorers):
        builder.resolve(f"assessment.scorers.{index}", RefKind.SCORER, scorer)
    builder.releases()
    # A release that did not resolve is already an issue; resolving its arms would repeat it.
    arms = builder.arms() if not builder.issues else {}
    resolved_problems = [problem for problem in problems if problem is not None]
    headline = (
        builder.metric(resolved_problems) if len(resolved_problems) == len(problems) else None
    )
    gate = GateInputs(spec=spec, base_dir=loaded.base_dir, resolved=builder.resolved)
    builder.issues.extend(runtime_gate_issues(gate, pack_loader=pack_loader))
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
            arms=arms,
            versions={
                "harbor": harbor_version(),
                "agents": {
                    arm["agent"]: {
                        "release": arm["agent_release"],
                        "version": arm["tool"]["version"],
                    }
                    for arm in arms.values()
                },
            },
            tools=execution_tools,
        ),
        "assessment": {
            "spec": spec_json["assessment"],
            "metric": headline,
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
    arms: object,
    versions: object,
    tools: object,
) -> dict[str, object]:
    """The ``execution`` section: everything a job's identity (``lock_sha256``) covers."""
    analysis = spec_json["analysis"]
    return {
        "design": {
            **{name: spec_json[name] for name in EXECUTION_SPEC_FIELDS if name in spec_json},
            **{name: analysis[name] for name in EXECUTION_ANALYSIS_FIELDS},
        },
        "problems": problems,
        "files": files,
        "references": references,
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
    """Why ``lock`` is not a sealed lock of the current ``LOCK_VERSION`` (empty when it is)."""
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
