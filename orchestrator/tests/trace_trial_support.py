"""Builders for stored trials with Harbor usage, timing and ATIF trajectories.

They extend ``tests.scorer_support.write_trial`` (oracle bundle, records and
snapshots) with what the resource-usage and process-compliance scorers read.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from raidar.oracle.models import CaseStatus
from raidar.scorers.sdk.context import AssessContext
from raidar.scorers.trial_inputs import load_trial_context
from tests.scorer_support import APP_V1, StepFixture, write_trial

PASS = (CaseStatus.PASSED,)


def tool_call(call_id: str, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {"tool_call_id": call_id, "function_name": name, "arguments": dict(arguments)}


def agent_step(step_id: int, *calls: dict[str, Any], message: str = "") -> dict[str, Any]:
    return {"step_id": step_id, "source": "agent", "message": message, "tool_calls": list(calls)}


def codex_exec(call_id: str, cmd: str) -> dict[str, Any]:
    """Codex's JavaScript ``exec`` wrapper around a shell command."""
    escaped = cmd.replace("\n", "\\n").replace('"', '\\"')
    script = f'const r = await tools.exec_command({{cmd:"{escaped}",workdir:"/app"}});\n'
    return tool_call(call_id, "exec", {"input": script})


def codex_patch(call_id: str, path: str, body: str = "+x = 1") -> dict[str, Any]:
    patch = f"*** Begin Patch\\n*** Add File: {path}\\n{body}\\n*** End Patch"
    return tool_call(call_id, "exec", {"input": f'const p = "{patch}"; tools.apply_patch(p);'})


AGENT = {"name": "codex", "version": "1", "model_name": "arm-model-label"}


def _numbered(steps: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Harbor requires step ids sequential from 1 and a message on every step."""
    return [{"message": "", **step, "step_id": index} for index, step in enumerate(steps, 1)]


def trajectory(
    *steps: dict[str, Any], subagents: Sequence[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    """A Harbor-valid root ATIF document: a user prompt, then ``steps`` renumbered."""
    document: dict[str, Any] = {
        "schema_version": "ATIF-v1.7",
        "session_id": "main",
        "agent": dict(AGENT),
        "steps": _numbered(
            [{"source": "user", "message": "Complete the checkpoint."}, *steps],
        ),
    }
    if subagents:
        document["subagent_trajectories"] = [dict(sub) for sub in subagents]
    return document


def session(
    trajectory_id: str, *steps: dict[str, Any], subagents: Sequence[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    """An embedded session; it may embed sessions of its own."""
    document: dict[str, Any] = {
        "trajectory_id": trajectory_id,
        "session_id": trajectory_id,
        "agent": dict(AGENT),
        "steps": _numbered(steps),
    }
    if subagents:
        document["subagent_trajectories"] = [dict(sub) for sub in subagents]
    return document


def delegate(*trajectory_ids: str, source: str = "system") -> dict[str, Any]:
    """A step that hands work to embedded sessions, in order."""
    refs = [{"trajectory_id": name} for name in trajectory_ids]
    return {"source": source, "observation": {"results": [{"subagent_trajectory_ref": refs}]}}


@dataclass(frozen=True)
class TracedStep:
    name: str
    trace: Mapping[str, Any] | None = None
    usage: Mapping[str, Any] | None = None
    execution: Mapping[str, Any] | None = None
    files: Mapping[str, str | bytes] = field(default_factory=lambda: dict(APP_V1))
    statuses: tuple[CaseStatus, ...] | None = PASS


def write_traced_trial(
    trial_dir: Path, steps: Sequence[TracedStep], *, planned: Sequence[str] = ()
) -> Path:
    """A stored trial whose steps carry the given usage, timing and trajectory."""
    write_trial(
        trial_dir,
        [StepFixture(step.name, step.files, step.statuses) for step in steps],
        planned=planned,
    )
    result_path = trial_dir / "result.json"
    result = json.loads(result_path.read_text())
    for step, stored in zip(steps, result["step_results"], strict=True):
        stored["agent_result"] = dict(step.usage) if step.usage is not None else None
        stored["agent_execution"] = dict(step.execution) if step.execution is not None else None
        path = trial_dir / "steps" / step.name / "agent" / "trajectory.json"
        if step.trace is None:
            path.unlink()
        else:
            path.write_text(json.dumps(step.trace, indent=2) + "\n")
    result_path.write_text(json.dumps(result, indent=2) + "\n")
    return trial_dir


def traced_context(
    tmp_path: Path, steps: Sequence[TracedStep], *, planned: Sequence[str] = (), **config: Any
) -> AssessContext:
    trial = write_traced_trial(tmp_path / "trial", steps, planned=planned)
    return load_trial_context(trial, config=config)


def usage(input_tokens: int, output_tokens: int, cost: float, cache: int = 0) -> dict[str, Any]:
    return {
        "n_input_tokens": input_tokens,
        "n_cache_tokens": cache,
        "n_output_tokens": output_tokens,
        "cost_usd": cost,
        "model_usage": {"arm-model-label": {"n_input_tokens": input_tokens}},
        "metadata": {"review_loop": {"stages": []}},
    }


def execution(start: str, finish: str) -> dict[str, str]:
    return {"started_at": f"2026-09-30T00:{start}Z", "finished_at": f"2026-09-30T00:{finish}Z"}
