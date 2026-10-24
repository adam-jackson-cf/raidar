"""N1 phase timings of one trial (plan D4), with the Phase 0 phase names.

Definitions follow the Phase 0 overhead baseline (docs/reboot/overhead-baseline.md
§1-§2), so the accepted budgets apply unchanged:

- ``environment_start``: Harbor ``environment_setup`` (condition warm/cold).
- ``component_apply``: host-side compile of the arm's condition plus writing
  its inputs into the trial task (measured by the job). This is not the
  Phase 0 stub's quantity (an in-container bundle install, p95 2.1 s): the
  in-container share, ``setup.sh`` materialising support files (about 20 ms of
  a ~250 ms script, the rest being the reset), runs in Harbor's step
  preparation and is therefore inside ``per_step_reset``. The accepted 2.5 s
  budget thus bounds only host-side work here (overhead-baseline.md §8).
- ``per_step_reset``: previous step end (or agent setup end) to agent start:
  Harbor's workdir upload, ``setup.sh`` and healthcheck. The pre-agent leak
  scan that runs in that gap is recorded separately (``pre_agent_leak_check``)
  and subtracted, because the Phase 0 baseline had no such scan there.
- ``setup_sh``: the reset script's own duration (record-only sub-phase).
- ``artifact_collection``: agent end to verifier start: Harbor's agent-log sync
  (the mounted-log ``chown``, the agent's trial conversion) and the
  ``/app`` transfer to the host (``raidar.engine.trials.environment`` runs both on
  the engine fast path).
- ``snapshot_transfer``, ``verifier_plumbing``, ``grader_image``,
  ``verifier_leak_check``: from the verifier step record's timings.
- ``step_archive_gap``: verifier end to the next step's agent start, less its
  leak scan. As in Phase 0 this interval overlaps the next step's
  ``per_step_reset``; both keep the baseline definitions.
- ``teardown_result_write``: last step end to trial end (condition init/no-init).
- ``raidar_result_write``: writing the trial record (measured by the job).
- Aggregates ``per_step_overhead`` and ``per_trial_fixed_overhead`` sum the
  budgeted per-step and per-trial phases.

Agent execution, oracle and coverage runs are recorded but never budgeted.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from raidar.engine.trials.collect import read_result
from raidar.engine.trials.models import PhaseSample
from raidar.engine.trials.timestamps import diff_s, parse_ts, span_s
from raidar.grading.records import (
    RECORD_FILE,
    load_pre_agent_leak,
    load_step_record,
    step_record_dir,
)

RESET_REPORT = "raidar-reset.json"
STEP_OVERHEAD_PHASES = (
    "per_step_reset",
    "artifact_collection",
    "snapshot_transfer",
    "verifier_plumbing",
    "step_archive_gap",
)
TRIAL_FIXED_PHASES = ("environment_start", "component_apply", "teardown_result_write")
_RECORD_TIMINGS = {
    "snapshot_transfer": "snapshot_transfer_s",
    "verifier_plumbing": "verifier_plumbing_s",
    "grader_image": "grader_image_s",
    "verifier_leak_check": "leak_check_s",
    "verifier_run": "oracle_run_s",
    "coverage_run": "coverage_run_s",
}


def _sample(
    phase: str, seconds: float | None, step: str | None = None, condition: str | None = None
) -> list[PhaseSample]:
    if seconds is None:
        return []
    return [PhaseSample(phase=phase, seconds=max(seconds, 0.0), step=step, condition=condition)]


def _pre_agent_leak_s(trial_dir: Path, step: str) -> float:
    report = load_pre_agent_leak(step_record_dir(trial_dir, step))
    return report.seconds if report is not None else 0.0


def _record_samples(trial_dir: Path, step: str) -> list[PhaseSample]:
    record_dir = step_record_dir(trial_dir, step)
    if not (record_dir / RECORD_FILE).is_file():
        return []
    record = load_step_record(record_dir)
    conditions = {
        "snapshot_transfer": record.snapshot.used,
        "grader_image": "cold" if record.grader.image_built else "warm",
    }
    samples: list[PhaseSample] = []
    for phase, key in _RECORD_TIMINGS.items():
        samples += _sample(phase, record.timings.get(key), step, conditions.get(phase))
    return samples


def _setup_sh_s(trial_dir: Path, step: str) -> float | None:
    path = trial_dir / "steps" / step / "agent" / RESET_REPORT
    if not path.is_file():
        return None
    try:
        report = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    duration = report.get("duration_ms") if isinstance(report, dict) else None
    return duration / 1000 if isinstance(duration, int | float) else None


def _step_samples(
    trial_dir: Path, steps: Sequence[Mapping[str, Any]], agent_setup_end: Any
) -> tuple[list[PhaseSample], Any]:
    samples: list[PhaseSample] = []
    previous_end = parse_ts(agent_setup_end)
    for index, item in enumerate(steps):
        name = item["step_name"]
        agent, verifier = item.get("agent_execution") or {}, item.get("verifier") or {}
        a_start, a_end = parse_ts(agent.get("started_at")), parse_ts(agent.get("finished_at"))
        v_end = parse_ts(verifier.get("finished_at"))
        leak_s = _pre_agent_leak_s(trial_dir, name)
        gap = diff_s(a_start, previous_end)
        samples += _sample("per_step_reset", gap - leak_s if gap is not None else None, name)
        samples += _sample("pre_agent_leak_check", leak_s or None, name)
        samples += _sample("setup_sh", _setup_sh_s(trial_dir, name), name)
        samples += _sample("agent_execution", span_s(agent), name)
        samples += _sample(
            "artifact_collection", diff_s(parse_ts(verifier.get("started_at")), a_end), name
        )
        samples += _record_samples(trial_dir, name)
        if index + 1 < len(steps):
            following = steps[index + 1]
            next_start = parse_ts((following.get("agent_execution") or {}).get("started_at"))
            archive = diff_s(next_start, v_end)
            leak_next = _pre_agent_leak_s(trial_dir, following["step_name"])
            samples += _sample(
                "step_archive_gap", archive - leak_next if archive is not None else None, name
            )
        previous_end = v_end or a_end
    return samples, previous_end


def _aggregates(samples: Iterable[PhaseSample], condition: dict[str, str]) -> list[PhaseSample]:
    per_step: dict[str, float] = {}
    trial_total = 0.0
    trial_parts = set()
    for sample in samples:
        if sample.phase in STEP_OVERHEAD_PHASES and sample.step is not None:
            per_step[sample.step] = per_step.get(sample.step, 0.0) + sample.seconds
        if sample.phase in TRIAL_FIXED_PHASES:
            trial_total += sample.seconds
            trial_parts.add(sample.phase)
    aggregates = [
        PhaseSample(phase="per_step_overhead", seconds=value, step=step)
        for step, value in per_step.items()
    ]
    if trial_parts == set(TRIAL_FIXED_PHASES):
        aggregates.append(
            PhaseSample(
                phase="per_trial_fixed_overhead",
                seconds=trial_total,
                condition="cold" if condition["environment"] == "cold" else condition["teardown"],
            )
        )
    return aggregates


def trial_phases(
    trial_dir: Path,
    *,
    component_apply_s: float,
    environment_cold: bool,
    compose_init: bool,
) -> list[PhaseSample]:
    """Every N1 phase this trial exposes (raidar_result_write is added by the job)."""
    result = read_result(trial_dir) or {}
    condition = {"environment": "cold" if environment_cold else "warm"}
    condition["teardown"] = "init" if compose_init else "no-init"
    samples = _sample(
        "environment_start", span_s(result.get("environment_setup")), None, condition["environment"]
    )
    samples += _sample("agent_setup", span_s(result.get("agent_setup")))
    samples += _sample("component_apply", component_apply_s)
    steps = [item for item in result.get("step_results") or [] if isinstance(item, dict)]
    step_samples, last_end = _step_samples(
        trial_dir, steps, (result.get("agent_setup") or {}).get("finished_at")
    )
    samples += step_samples
    samples += _sample(
        "teardown_result_write",
        diff_s(parse_ts(result.get("finished_at")), last_end),
        None,
        condition["teardown"],
    )
    return samples + _aggregates(samples, condition)
