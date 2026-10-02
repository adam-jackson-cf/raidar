"""Runtime-compatibility gate: ``build_lock`` refuses a design its runtime cannot run.

Every experiment declares one runtime (a runtime pack id). The gate runs inside
``build_lock`` on the effective spec (file plus every ``--set`` override), so
``lock``, ``plan``, ``run`` and the smoke targets are all gated, and it reports
every issue at once as field-level errors with a stable code:

- ``RT0``: a problem binding has no passing verifier validation for its manifest.
- ``RT1``: a problem's binding runtime differs from the experiment's runtime.
  A ``ref`` binding's runtime is its manifest's ``pack.id`` (one-shot refs
  have their own manifests, so they are checked the same way); a local
  ``path`` task must declare ``[metadata] raidar_runtime`` in its ``task.toml``.
- ``RT2``: a binding was imported against another contract version of the
  runtime's pack (stale binding: re-import it).
- ``RT3``: a component used by an arm does not cover the runtime, or does not
  support the arm's agent.
- ``RT4``: a scorer, or the headline metric, does not cover the runtime.
- ``RT5``: an agent's version is not the pack's pin for that agent (or the
  pack bakes no such agent).
- ``RT6``: no runtime pack is registered for the runtime. ``plan``/``run`` also
  load the pack from the lock's runtime and refuse pack drift (a built image
  other than the locked one; ``raidar.packs.lock_runtime_pack``), and
  ``assess`` checks ``AS1`` against the job's lock (``raidar.commands.assess``).

The facts come from each resolved reference's ``binding`` and are never
recorded in the lock, so the gate changes no execution digest.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from raidar.spec.references import ResolvedReference
from raidar.spec.schema import ComponentUse, ExperimentSpec
from raidar.spec.validation import FieldIssue

if TYPE_CHECKING:
    from raidar.packs.python.models import PackSpec

ANY_RUNTIME = "*"
TASK_RUNTIME_KEY = "raidar_runtime"
PackLoader = Callable[[str], "PackSpec"]


def _default_pack_loader(runtime: str) -> PackSpec:
    from raidar.packs import load_runtime_pack

    return load_runtime_pack(runtime)


@dataclass(frozen=True)
class GateInputs:
    """What the lock resolved, by field location (unresolved locations are absent)."""

    spec: ExperimentSpec
    base_dir: Path
    resolved: Mapping[str, ResolvedReference]
    metric_loc: str = "assessment.metric"


def _covers(runtimes: object, runtime: str) -> bool:
    return isinstance(runtimes, (list, tuple)) and (ANY_RUNTIME in runtimes or runtime in runtimes)


def _listed(values: object) -> str:
    return ", ".join(str(value) for value in values) if isinstance(values, (list, tuple)) else "?"


def task_runtime(task_dir: Path) -> str | None:
    """``[metadata] raidar_runtime`` of a local Harbor task, if declared."""
    with (task_dir / "task.toml").open("rb") as handle:
        metadata = tomllib.load(handle).get("metadata") or {}
    value = metadata.get(TASK_RUNTIME_KEY) if isinstance(metadata, dict) else None
    return value if isinstance(value, str) else None


def _path_problem_issues(inputs: GateInputs, index: int, relative: str) -> list[FieldIssue]:
    runtime = inputs.spec.runtime
    loc = f"problems.{index}.path"
    try:
        declared = task_runtime(inputs.base_dir / relative)
    except (OSError, tomllib.TOMLDecodeError):
        return []  # the lock already reports an unreadable task.toml
    if declared is None:
        return [
            FieldIssue(
                loc,
                f"[RT1] local task {relative!r} declares no runtime; add "
                f'[metadata] {TASK_RUNTIME_KEY} = "{runtime}" to its task.toml',
            )
        ]
    if declared != runtime:
        return [
            FieldIssue(
                loc,
                f"[RT1] local task {relative!r} is for runtime {declared}; "
                f"experiment runtime is {runtime}",
            )
        ]
    return []


def _ref_problem_issues(
    inputs: GateInputs, index: int, ref: str, pack: PackSpec | None
) -> list[FieldIssue]:
    loc = f"problems.{index}.ref"
    resolved = inputs.resolved.get(loc)
    if resolved is None:
        return []
    binding, runtime = resolved.binding, inputs.spec.runtime
    issues = []
    bound = binding.get("runtime")
    if not isinstance(bound, str):
        issues.append(FieldIssue(loc, f"[RT1] {ref} reports no binding runtime (pack id)"))
    elif bound != runtime:
        issues.append(
            FieldIssue(
                loc, f"[RT1] {ref} is bound to pack {bound}; experiment runtime is {runtime}"
            )
        )
    if binding.get("validated") is not True:
        detail = binding.get("validation_issue") or "no validation report"
        issues.append(FieldIssue(loc, f"[RT0] {ref} has no passing verifier validation: {detail}"))
    version = binding.get("pack_version")
    if pack is not None and bound == runtime and version != pack.version:
        issues.append(
            FieldIssue(
                loc,
                f"[RT2] stale binding: {ref} was imported against pack {bound}@{version}, "
                f"the current contract is {pack.id}@{pack.version}; re-import it",
            )
        )
    return issues


def _arms_using(spec: ExperimentSpec, loc: str) -> list[tuple[str, str]]:
    """(arm id, agent CLI id) of every arm whose effective condition holds ``loc``."""
    if loc.startswith("shared_components."):
        conditions = {condition.id for condition in spec.conditions}
    else:
        conditions = {spec.conditions[int(loc.split(".")[1])].id}
    return [
        (arm.id, spec.agent(arm.agent).harness.value)
        for arm in spec.arms
        if arm.condition in conditions
    ]


def _component_issues(inputs: GateInputs, loc: str, use: ComponentUse) -> list[FieldIssue]:
    resolved = inputs.resolved.get(f"{loc}.use")
    if resolved is None:
        return []
    spec, binding = inputs.spec, resolved.binding
    issues = []
    runtimes = binding.get("runtimes")
    if not _covers(runtimes, spec.runtime):
        issues.append(
            FieldIssue(
                f"{loc}.use",
                f"[RT3] {use.use} supports runtimes [{_listed(runtimes)}]; experiment runtime "
                f"is {spec.runtime}",
            )
        )
    supports = binding.get("supports")
    reported: set[str] = set()
    for arm, cli in _arms_using(spec, loc):
        if isinstance(supports, (list, tuple)) and cli in supports or cli in reported:
            continue
        reported.add(cli)
        name = next(a.name.value for a in spec.agents if a.harness.value == cli)
        issues.append(
            FieldIssue(
                f"{loc}.use",
                f"[RT3] {use.use} does not support agent {name} (arm {arm} runs it)",
            )
        )
    return issues


def _scorer_issues(inputs: GateInputs) -> list[FieldIssue]:
    runtime, issues = inputs.spec.runtime, []
    for index, ref in enumerate(inputs.spec.assessment.scorers):
        resolved = inputs.resolved.get(f"assessment.scorers.{index}")
        if resolved is None:
            continue
        runtimes = resolved.binding.get("runtimes")
        if not _covers(runtimes, runtime):
            issues.append(
                FieldIssue(
                    f"assessment.scorers.{index}",
                    f"[RT4] {ref} supports runtimes [{_listed(runtimes)}]; experiment runtime "
                    f"is {runtime}",
                )
            )
    resolved = inputs.resolved.get(inputs.metric_loc)
    if resolved is not None and not _covers(resolved.binding.get("runtimes"), runtime):
        issues.append(
            FieldIssue(
                inputs.metric_loc,
                f"[RT4] headline metric {resolved.ref} measures quality components for "
                f"runtimes [{_listed(resolved.binding.get('runtimes'))}] only; experiment "
                f"runtime is {runtime}",
            )
        )
    return issues


def _agent_issues(spec: ExperimentSpec, pack: PackSpec) -> list[FieldIssue]:
    issues = []
    for index, agent in enumerate(spec.agents):
        pin = pack.image.harnesses.get(agent.harness.value)
        if pin is None:
            issues.append(
                FieldIssue(
                    f"agents.{index}.name",
                    f"[RT5] pack {pack.id}@{pack.version} bakes no agent {agent.name.value}",
                )
            )
        elif pin.version != agent.version:
            issues.append(
                FieldIssue(
                    f"agents.{index}.version",
                    f"[RT5] {agent.name.value} {agent.version} is not the pin in pack "
                    f"{pack.id}@{pack.version} ({pin.version})",
                )
            )
    return issues


def compatibility_issues(
    inputs: GateInputs, *, pack_loader: PackLoader = _default_pack_loader
) -> list[FieldIssue]:
    """Every ``RT*`` issue of the effective spec against its runtime's pack."""
    spec = inputs.spec
    issues: list[FieldIssue] = []
    try:
        pack: PackSpec | None = pack_loader(spec.runtime)
    except LookupError as exc:
        pack = None
        issues.append(FieldIssue("runtime", f"[RT6] {exc}"))
    for index, entry in enumerate(spec.problems):
        if entry.path is not None:
            issues.extend(_path_problem_issues(inputs, index, entry.path))
        elif entry.ref is not None:
            issues.extend(_ref_problem_issues(inputs, index, entry.ref, pack))
    for loc, use in spec.component_uses():
        issues.extend(_component_issues(inputs, loc, use))
    issues.extend(_scorer_issues(inputs))
    if pack is not None:
        issues.extend(_agent_issues(spec, pack))
    return issues
