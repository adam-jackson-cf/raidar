"""Read-only access to executed runs and their experiment lock.

A run directory holds ``run.json``, ``plan.json`` and ``trajectories/*.json``;
the lock is checked against its own digest and against every run. Several runs
of one lock can be analysed together (for example blocks run on different
days). A trial id that appears in more than one run is resolved by the
replacement rule: a valid trajectory replaces an invalid one, and the replaced
ids are reported; two valid trajectories for one trial are refused.

Runs executed with a development option are not arm evidence: a
``max_checkpoints`` truncation stops every trajectory early, and an
``agent_override`` replaces the arm's agent with a no-LLM Harbor agent. Such
runs are refused unless the caller opts in, and runs with different options
are never pooled (or swapped for each other by the replacement rule).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from raidar.analysis.report import PlannedTrial
from raidar.execution.models import TrajectoryRecord
from raidar.spec.canonical import digest_json
from raidar.spec.lock import default_lock_path


class AnalysisInputError(ValueError):
    """The runs, lock or store cannot be analysed together."""


@dataclass(frozen=True)
class DevOptions:
    """The execution options that make a run's trajectories something other than arm data."""

    max_checkpoints: int | None = None
    agent_override: str | None = None

    @property
    def active(self) -> bool:
        return self.max_checkpoints is not None or self.agent_override is not None

    def describe(self) -> str:
        parts = []
        if self.max_checkpoints is not None:
            parts.append(f"truncated to the first {self.max_checkpoints} checkpoint(s)")
        if self.agent_override is not None:
            parts.append(f"agent replaced by the no-LLM override {self.agent_override!r}")
        return "; ".join(parts) or "no development options"


@dataclass(frozen=True)
class RunDir:
    path: Path
    manifest: Mapping[str, Any]
    plan: Mapping[str, Any]
    records: tuple[TrajectoryRecord, ...]

    @property
    def run_id(self) -> str:
        return str(self.manifest.get("run_id", self.path.name))

    @property
    def options(self) -> DevOptions:
        declared = self.plan.get("options") or {}
        maximum, override = declared.get("max_checkpoints"), declared.get("agent_override")
        return DevOptions(
            max_checkpoints=int(maximum) if maximum is not None else None,
            agent_override=str(override) if override is not None else None,
        )

    def trial_dir(self, record: TrajectoryRecord) -> Path:
        stored = Path(record.trial_dir)
        return stored if stored.is_absolute() else self.path / stored


def _read_json(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisInputError(f"cannot read {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise AnalysisInputError(f"{path} is not a JSON object")
    return loaded


def read_run(run_dir: Path) -> RunDir:
    records = []
    for path in sorted((run_dir / "trajectories").glob("*.json")):
        try:
            records.append(TrajectoryRecord.model_validate_json(path.read_bytes()))
        except ValidationError as exc:
            raise AnalysisInputError(f"invalid trajectory record {path}: {exc}") from exc
    return RunDir(
        path=run_dir,
        manifest=_read_json(run_dir / "run.json"),
        plan=_read_json(run_dir / "plan.json"),
        records=tuple(records),
    )


def read_lock(path: Path) -> dict[str, Any]:
    """The lock document, refused when its body does not hash to ``lock_sha256``."""
    lock = _read_json(path)
    body = {key: value for key, value in lock.items() if key != "lock_sha256"}
    if digest_json(body) != lock.get("lock_sha256"):
        raise AnalysisInputError(f"{path}: lock_sha256 does not match the lock body")
    return lock


def default_lock(run: RunDir) -> Path:
    experiment_file = run.manifest.get("experiment_file")
    if not isinstance(experiment_file, str):
        raise AnalysisInputError(f"{run.path}/run.json names no experiment_file; pass --lock")
    return default_lock_path(Path(experiment_file))


def _record_options(record: TrajectoryRecord) -> DevOptions:
    return DevOptions(max_checkpoints=record.truncated_to, agent_override=record.agent_override)


def run_options(runs: Sequence[RunDir], *, allow_dev_runs: bool) -> DevOptions:
    """The development options shared by every run and record, or a refusal.

    Records carry the options they were executed with; a record that disagrees
    with its run's plan is refused, as are runs with different options. Runs
    with any option active are refused unless ``allow_dev_runs``.
    """
    for run in runs:
        for record in run.records:
            if _record_options(record) != run.options:
                raise AnalysisInputError(
                    f"{run.path}: trajectory {record.trial.trial_id} was executed with "
                    f"{_record_options(record).describe()}, but the plan declares "
                    f"{run.options.describe()}"
                )
    options = {run.options for run in runs}
    if len(options) != 1:
        described = "; ".join(f"{run.path}: {run.options.describe()}" for run in runs)
        raise AnalysisInputError(
            f"the runs were executed with different development options ({described}); "
            "analyse them apart"
        )
    (shared,) = options
    if shared.active and not allow_dev_runs:
        raise AnalysisInputError(
            f"the runs were executed with {shared.describe()}: their trajectories are not arm "
            "evidence. Pass --allow-dev-runs to analyse them as a labelled development run"
        )
    return shared


def check_runs(runs: Sequence[RunDir], lock: Mapping[str, Any]) -> None:
    for run in runs:
        if run.manifest.get("lock_sha256") != lock["lock_sha256"]:
            raise AnalysisInputError(
                f"run {run.path} was executed under lock {run.manifest.get('lock_sha256')}, "
                f"not {lock['lock_sha256']}"
            )
    trial_sets = {tuple(t["trial_id"] for t in run.plan.get("trials", ())) for run in runs}
    if len(trial_sets) != 1:
        raise AnalysisInputError("the runs' plans list different trials; analyse them apart")


def planned_trials(plan: Mapping[str, Any]) -> tuple[PlannedTrial, ...]:
    return tuple(
        PlannedTrial(
            trial_id=str(trial["trial_id"]),
            arm=str(trial["arm"]),
            block=int(trial["block"]),
            problem=str(trial["problem"]),
        )
        for trial in plan.get("trials", ())
    )


def select_records(
    runs: Sequence[RunDir],
) -> tuple[dict[str, tuple[RunDir, TrajectoryRecord]], tuple[str, ...]]:
    """One record per trial id under the replacement rule, plus the replaced ones."""
    chosen: dict[str, tuple[RunDir, TrajectoryRecord]] = {}
    replaced: list[str] = []
    for run in runs:
        for record in run.records:
            trial = record.trial.trial_id
            previous = chosen.get(trial)
            if previous is not None and previous[1].valid:
                if record.valid:
                    raise AnalysisInputError(
                        f"trial {trial} is valid in both {previous[0].path} and {run.path}"
                    )
                replaced.append(f"{run.run_id}/{trial}")
                continue
            if previous is not None:
                replaced.append(f"{previous[0].run_id}/{trial}")
            chosen[trial] = (run, record)
    return chosen, tuple(replaced)
