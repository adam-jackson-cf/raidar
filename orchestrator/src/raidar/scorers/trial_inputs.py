"""Build an ``AssessContext`` from a stored Harbor trial directory (read-only).

The planned checkpoints come from the trial's oracle bundle (located through
its step records or the verifier's ``oracle_bundle`` argument, and checked
against the digest the records carry). Executed steps come from Harbor's
``result.json`` ``step_results`` in order and must be a prefix of the plan; a
one-step trial has no ``step_results`` and must have exactly one oracle
record. Planned checkpoints the trial never reached are kept with
``reached=False``, so every trajectory of a problem has the same subjects.

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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from raidar.oracle.bundle import BUNDLE_MANIFEST, BundleError, OracleBundle, load_bundle
from raidar.oracle.records import (
    FORK_EVIDENCE_FILE,
    ORACLE_DIR,
    RECORD_FILE,
    StepRecord,
    load_step_record,
    step_record_dir,
    stored_snapshot_path,
)
from raidar.oracle.snapshot import tree_digest
from raidar.scorers.inventory import InventoryPolicy
from raidar.scorers.sdk.context import AssessContext, StepInputs
from raidar.spec.canonical import digest_json

RESULT_FILE = "result.json"
_VERIFIER_CONFIG_FILES = ("lock.json", "config.json")
_USAGE_FIELDS = ("n_input_tokens", "n_cache_tokens", "n_output_tokens", "cost_usd")


class TrialInputError(ValueError):
    """The trial directory cannot be assessed as stored."""


@dataclass
class BundleCache:
    """Loaded bundles and their digests, shared across the trials of one rescore.

    ``search`` lists directories to look in, by digest, when a recorded bundle
    root no longer holds the bundle (the worktree moved, another host): the
    bundle is found again only if its content digest is the recorded one.
    """

    loaded: dict[Path, tuple[OracleBundle, str]] = field(default_factory=dict)
    search: tuple[Path, ...] = ()

    def relocate(self, digests: set[str]) -> OracleBundle | None:
        for directory in self.search:
            for marker in sorted(directory.rglob(BUNDLE_MANIFEST)):
                try:
                    bundle, digest = self.get(marker.parent)
                except TrialInputError:
                    continue
                if digest in digests:
                    return bundle
        return None

    def get(self, root: Path) -> tuple[OracleBundle, str]:
        key = root.resolve()
        if key not in self.loaded:
            try:
                bundle = load_bundle(key)
            except BundleError as exc:
                raise TrialInputError(f"oracle bundle unusable: {exc}") from exc
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
    records = sorted(path.name for path in (trial_dir / ORACLE_DIR / "steps").glob("*"))
    if len(records) != 1:
        raise TrialInputError(
            f"{trial_dir}: a trial without step_results needs exactly one oracle record, "
            f"found {records}"
        )
    return [_Executed(records[0], False, result.get("agent_result"), result.get("agent_execution"))]


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
        verifier = _read_json(path).get("verifier")
        kwargs = verifier.get("kwargs") if isinstance(verifier, dict) else None
        root = kwargs.get("oracle_bundle") if isinstance(kwargs, dict) else None
        if isinstance(root, str) and root:
            return Path(root)
    return None


def _bundle(
    trial_dir: Path,
    records: Mapping[str, StepRecord],
    override: Path | None,
    cache: BundleCache,
) -> OracleBundle:
    digests = {record.bundle.digest for record in records.values()}
    if len(digests) > 1:
        raise TrialInputError(f"{trial_dir}: oracle records name several bundles")
    first = next(iter(records.values()), None)
    root = override or (Path(first.bundle.root) if first else _configured_bundle(trial_dir))
    if root is None:
        raise TrialInputError(
            f"{trial_dir}: cannot locate the oracle bundle for the planned checkpoints; "
            "pass --bundle"
        )
    if override is None and digests:
        return _recorded_bundle(trial_dir, root, digests, cache)
    bundle, digest = cache.get(root)
    if digests and digest not in digests:
        raise TrialInputError(
            f"bundle at {root} does not match the digest recorded for {trial_dir}"
        )
    return bundle


def _recorded_bundle(
    trial_dir: Path, root: Path, digests: set[str], cache: BundleCache
) -> OracleBundle:
    """The bundle the records name: at its recorded root, else found by digest."""
    try:
        bundle, digest = cache.get(root)
    except TrialInputError:
        relocated = cache.relocate(digests)
        if relocated is None:
            raise
        return relocated
    if digest not in digests:
        raise TrialInputError(
            f"bundle at {root} does not match the digest recorded for {trial_dir}"
        )
    return bundle


def _snapshot(
    trial_dir: Path, relative: str, expected: str | None
) -> tuple[Path | None, str | None]:
    path = trial_dir / relative
    if not path.is_dir():
        if expected is not None:
            raise TrialInputError(f"graded snapshot missing: {path}")
        return None, None
    digest = tree_digest(path)
    if expected is not None and digest != expected:
        raise TrialInputError(f"snapshot changed since grading: {path}")
    return path, digest


def _record_digest(record_dir: Path, record: StepRecord) -> str:
    """Content digest of an oracle record; the bundle's host location is not content.

    The step's fork-shaped G run evidence (``g-kernel-input.json``), when the
    oracle wrote it, is part of the record's content: a trial whose G input
    differs or went missing must not share an Assessment key.
    """
    payload = record.model_dump(mode="json")
    payload["bundle"].pop("root")
    fork = record_dir / FORK_EVIDENCE_FILE
    if not fork.is_file():
        return digest_json(payload)
    return digest_json(
        {"record": payload, FORK_EVIDENCE_FILE: hashlib.sha256(fork.read_bytes()).hexdigest()}
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


def _planned_steps(
    trial_dir: Path,
    executed: Sequence[_Executed],
    records: Mapping[str, StepRecord],
    bundle: OracleBundle,
) -> tuple[StepInputs, ...]:
    """Every planned checkpoint in order; the executed ones must be a prefix of the plan."""
    planned = [(step.name, step.checkpoint) for step in bundle.manifest.steps]
    ran = [item.step for item in executed]
    if ran != [name for name, _ in planned[: len(ran)]]:
        raise TrialInputError(
            f"{trial_dir}: executed steps {ran} are not a prefix of the planned "
            f"checkpoints {[name for name, _ in planned]}"
        )
    reached = tuple(
        _step_inputs(trial_dir, item, checkpoint, records.get(item.step))
        for item, (_, checkpoint) in zip(executed, planned, strict=False)
    )
    return reached + tuple(_unreached(name, checkpoint) for name, checkpoint in planned[len(ran) :])


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
    problems = {record.bundle.problem for record in records.values()}
    if problems - {bundle.manifest.problem}:
        raise TrialInputError(f"{trial_dir}: oracle records name other problems {problems}")
    return AssessContext(
        problem=bundle.manifest.problem,
        steps=_planned_steps(trial_dir, executed, records, bundle),
        config=dict(config or {}),
        problem_config=problem_config(bundle),
        inventory_policy=inventory_policy or InventoryPolicy(),
    )


def problem_config(bundle: OracleBundle) -> dict[str, Any]:
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
