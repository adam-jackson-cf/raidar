"""What a trial records for comparison: its parity record and the harness isolation applied.

The **parity record** (``contract.trial.ParityRecord``) holds the conditions every arm of a
problem must share, so that arms differ only in what an experiment varies: the image the
trial ran in (its Docker image id on the target), the seeded ``/app`` it started from (the
workspace seed's snapshot digest), its limits (agent and verifier timeouts and the liveness
settings), the agent phase's network policy, and the user, ``PATH`` and ``TMPDIR`` of each
agent turn's process. Pooling refuses runs whose parity records differ
(``raidar.evaluation.analysis.pooling``).

**Harness isolation** is what the agent harness applied to each turn's command
(``raidar.subject.agent.isolated_codex``): the workflow runner records it per turn in each
step's ``workflow/workflow.json``, and the trial record keeps each distinct one.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from raidar.contract.trial import AgentProcess, ParityRecord
from raidar.engine.health.liveness import LivenessConfig
from raidar.engine.trial.workspace import seeded_digest
from raidar.subject.workflow.runner import RECORD_FILE, WORKFLOW_DIR


def agent_network(task_dir: Path) -> tuple[dict[str, Any], ...]:
    """Each distinct agent-phase network policy the materialised ``task.toml`` sets."""
    document = tomllib.loads((task_dir / "task.toml").read_text())
    phases = [document.get("agent") or {}]
    phases += [step.get("agent") or {} for step in document.get("steps") or ()]
    policies = (
        {
            "network_mode": phase.get("network_mode"),
            "allowed_hosts": sorted(phase.get("allowed_hosts") or ()),
        }
        for phase in phases
    )
    return tuple(_distinct(policies))


def _turns(trial_dir: Path, steps: Sequence[str]) -> Iterator[Mapping[str, Any]]:
    for step in steps:
        path = trial_dir / "steps" / step / "agent" / WORKFLOW_DIR / RECORD_FILE
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        turns = record.get("turns") if isinstance(record, dict) else None
        for turn in turns or ():
            if isinstance(turn, Mapping):
                yield turn


def _distinct(values: Iterable[Any]) -> list[dict[str, Any]]:
    seen: dict[str, Any] = {}
    for value in values:
        if isinstance(value, Mapping):
            seen.setdefault(json.dumps(value, sort_keys=True), dict(value))
    return list(seen.values())


def harness_isolation(trial_dir: Path, steps: Sequence[str]) -> tuple[dict[str, Any], ...]:
    """Each distinct isolation the harness applied to a turn of ``steps``."""
    return tuple(_distinct(turn.get("harness_isolation") for turn in _turns(trial_dir, steps)))


def parity_record(
    trial_dir: Path,
    steps: Sequence[str],
    *,
    task_dir: Path,
    image_id: str | None,
    timeouts: Mapping[str, Any],
) -> ParityRecord:
    """The trial's parity record.

    The seed digest is the first step's provision's; the network policies are the
    materialised task's; the limits are ``timeouts`` plus the liveness settings in force.
    """
    processes = _distinct(turn.get("agent_process") for turn in _turns(trial_dir, steps))
    seed = seeded_digest(trial_dir / "steps" / steps[0] / "agent") if steps else None
    return ParityRecord(
        image_id=image_id,
        seed_digest=seed,
        limits={**timeouts, "liveness": LivenessConfig.from_env().as_json()},
        network=agent_network(task_dir),
        agent_process=tuple(AgentProcess.model_validate(item) for item in processes),
    )
