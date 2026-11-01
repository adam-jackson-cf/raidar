"""Build an ``AssessContext`` from a stored Harbor trial directory (read-only).

The planned checkpoints come from the retained trial record (before smoke/dev
truncation), or the configured task for standalone Harbor trials. The oracle
bundle maps those steps to checkpoint identities; it does not select the plan.
Executed steps come from Harbor's ``result.json`` ``step_results`` in order
and must be a prefix of the plan. Planned checkpoints the trial never reached
are kept with ``reached=False``.

Correctness inputs come from the oracle's ``raidar-oracle/steps/<step>/record.json``;
Harbor's trial-level ``verifier_result`` is never read. Each stored snapshot
is re-hashed and must still match the digest the oracle graded, so an
Assessment can never be keyed on a snapshot that changed after grading.
Harbor's ``agent_result`` is blinded before scorers see it; the step's
``agent_execution`` duration is passed through and, like ``agent_result``, is
part of the trace digest. Nothing in the
trial directory is written.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from raidar_contract.canonical import digest_json

from raidar.contract.job_layout import TRIAL_RECORDS_DIR
from raidar.contract.snapshot import tree_digest
from raidar.contract.trial import TrialRecord
from raidar.grading.bundle import BundleError, VerifierBundle, load_bundle
from raidar.grading.records import (
    FORK_EVIDENCE_FILE,
    RECORD_FILE,
    VERIFIER_RECORD_DIR,
    StepRecord,
    coverage_identity,
    load_step_record,
    step_record_dir,
    stored_snapshot_path,
)
from raidar.scoring.framework.context import AssessContext, StepInputs
from raidar.scoring.framework.inventory import InventoryPolicy

RESULT_FILE = "result.json"
_VERIFIER_CONFIG_FILES = ("lock.json", "config.json")
_USAGE_FIELDS = ("n_input_tokens", "n_cache_tokens", "n_output_tokens", "cost_usd")


class TrialInputError(ValueError):
    """The trial directory cannot be assessed as stored."""


class BundleUnavailableError(TrialInputError):
    """The bundle directory cannot be loaded (moved, missing or malformed)."""


@dataclass
class BundleCache:
    """Loaded bundles and their digests, shared across the trials of one rescore."""

    loaded: dict[Path, tuple[VerifierBundle, str]] = field(default_factory=dict)
    loader: Callable[[Path], VerifierBundle] = load_bundle

    def get(self, root: Path) -> tuple[VerifierBundle, str]:
        key = root.resolve()
        if key not in self.loaded:
            try:
                bundle = self.loader(key)
            except BundleError as exc:
                raise BundleUnavailableError(f"oracle bundle unusable: {exc}") from exc
            self.loaded[key] = (bundle, bundle.digest())
        return self.loaded[key]


@dataclass(frozen=True)
class _Executed:
    step: str
    multi_step: bool
    agent_result: Any
    agent_execution: Any = None


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        loaded = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise TrialInputError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise TrialInputError(f"{path} is not a JSON object")
    return loaded


def _read_result(trial_dir: Path) -> Mapping[str, Any]:
    path = trial_dir / RESULT_FILE
    if not path.is_file():
        raise TrialInputError(f"{path} is missing; step order is unknown")
    return _read_json(path)


def _executed_steps(trial_dir: Path, result: Mapping[str, Any]) -> list[_Executed]:
    """The steps Harbor ran, in execution order."""
    names = sorted(path.name for path in (trial_dir / VERIFIER_RECORD_DIR / "steps").glob("*"))
    return _executed_from(trial_dir, result, names)


def _executed_from(
    trial_dir: Path, result: Mapping[str, Any], oracle_steps: Sequence[str]
) -> list[_Executed]:
    """The executed steps of a result; a one-shot trial's only step is its oracle record's."""
    step_results = result.get("step_results")
    if step_results:
        if not isinstance(step_results, list):
            raise TrialInputError(f"{trial_dir}: step_results is not a list")
        executed = []
        for index, item in enumerate(step_results):
            name = item.get("step_name") if isinstance(item, dict) else None
            if not isinstance(name, str) or not name:
                raise TrialInputError(f"{trial_dir}: step_results[{index}] has no step_name")
            executed.append(
                _Executed(name, True, item.get("agent_result"), item.get("agent_execution"))
            )
        return executed
    if len(oracle_steps) != 1:
        raise TrialInputError(
            f"{trial_dir}: a trial without step_results needs exactly one oracle record, "
            f"found {list(oracle_steps)}"
        )
    return [
        _Executed(oracle_steps[0], False, result.get("agent_result"), result.get("agent_execution"))
    ]


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # Harbor writes UTC; a zone-less stamp is read as UTC so both ends compare.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def agent_execution_seconds(timing: Any) -> float | None:
    """Duration of a Harbor ``agent_execution`` TimingInfo, or None if either end is missing."""
    if not isinstance(timing, Mapping):
        return None
    started = _timestamp(timing.get("started_at"))
    finished = _timestamp(timing.get("finished_at"))
    if started is None or finished is None or finished < started:
        return None
    return (finished - started).total_seconds()


def ran_judge(agent_result: Any) -> bool:
    """Whether Harbor's own metadata declares the judge primitive.

    Presence, not a successful verdict, determines applicability: missing or malformed retained
    judge evidence must not silently reduce a judged step's measured cost.
    """
    metadata = agent_result.get("metadata") if isinstance(agent_result, Mapping) else None
    workflow = metadata.get("workflow") if isinstance(metadata, Mapping) else None
    return isinstance(workflow, Mapping) and "jev-judge/1" in workflow


def blind_agent_result(raw: Any) -> dict[str, Any] | None:
    """Harbor ``agent_result`` without arm or model labels.

    Keeps the usage totals and per-model usage as an anonymous, ordered list.
    ``model_usage`` keys (model names), ``metadata`` and ``rollout_details``
    are dropped.
    """
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise TrialInputError("agent_result is not a JSON object")
    blinded: dict[str, Any] = {name: raw.get(name) for name in _USAGE_FIELDS}
    usage = raw.get("model_usage")
    blinded["model_usage"] = (
        sorted(
            (
                {name: entry.get(name) for name in _USAGE_FIELDS}
                for entry in usage.values()
                if isinstance(entry, Mapping)
            ),
            key=lambda entry: json.dumps(entry, sort_keys=True),
        )
        if isinstance(usage, Mapping)
        else None
    )
    return blinded


def _load_records(trial_dir: Path, executed: Sequence[_Executed]) -> dict[str, StepRecord]:
    return {
        item.step: load_step_record(step_record_dir(trial_dir, item.step))
        for item in executed
        if (step_record_dir(trial_dir, item.step) / RECORD_FILE).is_file()
    }


def _configured_bundle(trial_dir: Path) -> Path | None:
    """The ``oracle_bundle`` the trial's verifier was configured with, if recorded."""
    for name in _VERIFIER_CONFIG_FILES:
        path = trial_dir / name
        if not path.is_file():
            continue
        root = _oracle_bundle_of(_read_json(path))
        if root is not None:
            return root
    return None


def _oracle_bundle_of(config: Mapping[str, Any]) -> Path | None:
    verifier = config.get("verifier")
    kwargs = verifier.get("kwargs") if isinstance(verifier, dict) else None
    root = kwargs.get("oracle_bundle") if isinstance(kwargs, dict) else None
    return Path(root) if isinstance(root, str) and root else None


def _bundle_root(
    trial_dir: Path,
    records: Mapping[str, StepRecord],
    override: Path | None,
    configured: Callable[[], Path | None],
) -> Path:
    """Where the planned checkpoints' bundle is: override, else the records', else configured."""
    if len({record.bundle.digest for record in records.values()}) > 1:
        raise TrialInputError(f"{trial_dir}: oracle records name several bundles")
    first = next(iter(records.values()), None)
    root = override or (Path(first.bundle.root) if first else configured())
    if root is None:
        raise TrialInputError(
            f"{trial_dir}: cannot locate the oracle bundle for the planned checkpoints; "
            "pass --bundle"
        )
    return root


def _bundle(
    trial_dir: Path,
    records: Mapping[str, StepRecord],
    override: Path | None,
    cache: BundleCache,
) -> VerifierBundle:
    root = _bundle_root(trial_dir, records, override, lambda: _configured_bundle(trial_dir))
    bundle, digest = cache.get(root)
    recorded = {record.bundle.digest for record in records.values()}
    if recorded and digest not in recorded:
        raise TrialInputError(
            f"bundle at {root} does not match the digest recorded for {trial_dir}"
        )
    return bundle


def _snapshot(
    trial_dir: Path, relative: str, expected: str | None
) -> tuple[Path | None, str | None]:
    path = trial_dir / relative
    return _checked_snapshot(path, tree_digest(path) if path.is_dir() else None, expected)


def _checked_snapshot(
    path: Path, digest: str | None, expected: str | None
) -> tuple[Path | None, str | None]:
    """The stored snapshot (None when absent and never graded) if it is the one graded."""
    if digest is None:
        if expected is not None:
            raise TrialInputError(f"graded snapshot missing: {path}")
        return None, None
    if expected is not None and digest != expected:
        raise TrialInputError(f"snapshot changed since grading: {path}")
    return path, digest


def _record_digest(record_dir: Path, record: StepRecord) -> str:
    """Content digest of an oracle record; the bundle's host location is not content.

    Retained grading evidence and the current offline grading execution identity
    participate even before an offline answer exists. Otherwise a finalized
    Assessment could hide a coverage-cache invalidation caused by changed raw
    evidence, grader image, timeout or plugin bytes. The optional fork-shaped G
    input participates too; missing or changed input must change the key.
    """
    payload = record.model_dump(mode="json")
    payload["bundle"].pop("root")
    fork = record_dir / FORK_EVIDENCE_FILE
    return digest_json(
        {
            "record": payload,
            "grading_evidence": coverage_identity(record, record_dir),
            FORK_EVIDENCE_FILE: hashlib.sha256(fork.read_bytes()).hexdigest()
            if fork.is_file()
            else None,
        }
    )


def _step_inputs(
    trial_dir: Path, executed: _Executed, checkpoint: str, record: StepRecord | None
) -> StepInputs:
    step = executed.step
    relative = (
        record.snapshot.stored_path
        if record is not None
        else stored_snapshot_path(step if executed.multi_step else None)
    )
    snapshot, snapshot_digest = _snapshot(
        trial_dir, relative, record.snapshot.digest if record is not None else None
    )
    trace = trial_dir / "steps" / step / "agent" if executed.multi_step else trial_dir / "agent"
    trace_dir = trace if trace.is_dir() else None
    trace_digest = digest_json(
        {
            "files": tree_digest(trace_dir) if trace_dir is not None else None,
            "agent_result": executed.agent_result,
            "agent_execution": executed.agent_execution,
        }
    )
    return StepInputs(
        step=step,
        reached=True,
        checkpoint=record.checkpoint if record is not None else checkpoint,
        snapshot=snapshot,
        snapshot_digest=snapshot_digest,
        record=record,
        record_digest=(
            _record_digest(step_record_dir(trial_dir, step), record) if record is not None else None
        ),
        trace_dir=trace_dir,
        trace_digest=trace_digest,
        agent_result=blind_agent_result(executed.agent_result),
        agent_execution_s=agent_execution_seconds(executed.agent_execution),
        judged=ran_judge(executed.agent_result),
    )


def _unreached(step: str, checkpoint: str) -> StepInputs:
    return StepInputs(
        step=step,
        reached=False,
        checkpoint=checkpoint,
        snapshot=None,
        snapshot_digest=None,
        record=None,
        record_digest=None,
        trace_dir=None,
        trace_digest=None,
        agent_result=None,
    )


def _recorded_steps(trial_dir: Path) -> tuple[str, ...]:
    """Read the evaluation plan, not just the steps Harbor attempted.

    TrialRecord retains the bound plan, including ``not_attempted`` results
    beyond MAX_CHECKPOINTS. A standalone Harbor trial instead retains its
    configured task; it has no Raidar smoke truncation or problem binding.
    """
    record_path = trial_dir.parent.parent / TRIAL_RECORDS_DIR / f"{trial_dir.name}.json"
    if record_path.is_file():
        return _trial_record_steps(record_path, _read_json(record_path))
    for name in _VERIFIER_CONFIG_FILES:
        config_path = trial_dir / name
        if not config_path.is_file():
            continue
        planned = _task_plan(trial_dir, _read_json(config_path), Path.read_text)
        if planned is not None:
            return planned
    raise TrialInputError(f"{trial_dir}: no retained trial record or configured task plan")


def _trial_record_steps(record_path: Path, document: Mapping[str, Any]) -> tuple[str, ...]:
    try:
        return TrialRecord.model_validate(document).planned_steps
    except ValueError as exc:
        raise TrialInputError(f"{record_path}: invalid trial record: {exc}") from exc


def _task_plan(
    trial_dir: Path, config: Mapping[str, Any], read_text: Callable[[Path], str]
) -> tuple[str, ...] | None:
    """The step names of the task a Harbor config points at; None when it names no task."""
    task = config.get("task")
    root = task.get("path") if isinstance(task, Mapping) else None
    if not (isinstance(root, str) and root):
        return None
    try:
        document = tomllib.loads(read_text(Path(root) / "task.toml"))
        return tuple(step["name"] for step in document.get("steps", ()))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise TrialInputError(f"{trial_dir}: cannot read planned task steps: {exc}") from exc


def _plan(
    trial_dir: Path,
    names: Sequence[str],
    available: Sequence[tuple[str, str]],
    ran: Sequence[str],
) -> list[tuple[str, str]]:
    """The planned (step, checkpoint) pairs; recorded and executed steps must be prefixes."""
    planned = list(available[: len(names)])
    if not names or list(names) != [name for name, _ in planned]:
        raise TrialInputError(
            f"{trial_dir}: recorded steps {tuple(names)} are not a prefix of the planned "
            f"checkpoints {[name for name, _ in available]}"
        )
    if list(ran) != [name for name, _ in planned[: len(ran)]]:
        raise TrialInputError(
            f"{trial_dir}: executed steps {list(ran)} are not a prefix of the planned "
            f"checkpoints {[name for name, _ in planned]}"
        )
    return planned


def _planned_steps(
    trial_dir: Path,
    executed: Sequence[_Executed],
    records: Mapping[str, StepRecord],
    bundle: VerifierBundle,
) -> tuple[StepInputs, ...]:
    """Every planned checkpoint in order; the executed ones must be a prefix of the plan."""
    available = [(step.name, step.checkpoint) for step in bundle.manifest.steps]
    ran = [item.step for item in executed]
    planned = _plan(trial_dir, _recorded_steps(trial_dir), available, ran)
    reached = tuple(
        _step_inputs(trial_dir, item, checkpoint, records.get(item.step))
        for item, (_, checkpoint) in zip(executed, planned, strict=False)
    )
    return reached + tuple(_unreached(name, checkpoint) for name, checkpoint in planned[len(ran) :])


def _check_problems(trial_dir: Path, recorded: set[str], bundle: VerifierBundle) -> None:
    """Every oracle record must name the bundle's problem."""
    if recorded - {bundle.manifest.problem}:
        raise TrialInputError(f"{trial_dir}: oracle records name other problems {recorded}")


def load_trial_context(
    trial_dir: Path,
    *,
    config: Mapping[str, Any] | None = None,
    inventory_policy: InventoryPolicy | None = None,
    bundle_override: Path | None = None,
    bundle_cache: BundleCache | None = None,
) -> AssessContext:
    result = _read_result(trial_dir)
    executed = _executed_steps(trial_dir, result)
    records = _load_records(trial_dir, executed)
    bundle = _bundle(trial_dir, records, bundle_override, bundle_cache or BundleCache())
    _check_problems(trial_dir, {record.bundle.problem for record in records.values()}, bundle)
    return AssessContext(
        problem=bundle.manifest.problem,
        steps=_planned_steps(trial_dir, executed, records, bundle),
        config=dict(config or {}),
        problem_config=problem_config(bundle),
        inventory_policy=inventory_policy or InventoryPolicy(),
    )


def problem_config(bundle: VerifierBundle) -> dict[str, Any]:
    """Problem facts scorers take as config defaults (``AssessContext.problem_config``)."""
    entry_file = bundle.manifest.entry_file
    return {"entry_file": entry_file} if entry_file is not None else {}


def context_inputs(ctx: AssessContext) -> dict[str, object]:
    """The content identity of a context's run inputs (part of the Assessment key)."""
    return {
        "problem": ctx.problem,
        "starter": tree_digest(ctx.starter) if ctx.starter is not None else None,
        "inventory_policy": ctx.inventory_policy.model_dump(mode="json"),
        "steps": [
            {
                "step": item.step,
                "checkpoint": item.checkpoint,
                "reached": item.reached,
                "snapshot": item.snapshot_digest,
                "oracle_record": item.record_digest,
                "trace": item.trace_digest,
            }
            for item in ctx.steps
        ],
    }
