"""Run-health family ``G``: the verify gate (design section 6), over the verify stage's records.

The checks read only what the workflow runner recorded per attempted step: ``verify`` in
``workflow/workflow.json`` (the seals' records: verdict, gated tree, commit, the repository's
``core.hooksPath`` and every commit since the checkpoint's base) and the hook's run log the
runner copied to the step's logs (``verify.hook_log``). A trial whose records name no verify
stage has no ``G`` checks; a verifying step whose evidence is missing makes a check
``unmeasured``, never ``pass``.

- ``G1`` hook and seal decisions for the same Git tree, checked workspace and workflow
  prerequisite inputs agree, and every hook line reads;
- ``G2`` no bypass: every commit the agent made since the checkpoint's base holds a tree its
  hook passed (a ``--no-verify`` commit, or one made with the hooks pointed elsewhere, has
  none), and ``core.hooksPath`` was the stage's hooks directory at every seal. A base the agent
  rewrote out of ``HEAD``'s history cannot be read and is ``unmeasured``;
- ``G3`` the gate's tools are unchanged: the tool directory's digest is the same before the
  first turn and after the last of every step.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.engine.health.base import Audit, make_check
from raidar.engine.health.model import PASS, UNMEASURED, VIOLATION, HealthCheck

WORKFLOW_RECORD = "workflow/workflow.json"
DECIDED = ("passed", "refused")
_SHOWN = 3


@dataclass(frozen=True)
class StepGate:
    """One verifying step's records: the stage record and the hook's lines (``None``: unread)."""

    step: str
    record: dict[str, Any]
    hook_runs: list[dict[str, Any]] | None


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _hook_runs(agent: Path, record: dict[str, Any]) -> list[dict[str, Any]] | None:
    relative = record.get("hook_log")
    if not isinstance(relative, str):
        return None
    try:
        lines = (agent / relative).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    runs = []
    for line in filter(str.strip, lines):
        try:
            run = json.loads(line)
        except ValueError:
            return None
        if not isinstance(run, dict):
            return None
        runs.append(run)
    return runs


def step_gates(steps: Sequence[tuple[str, Path]]) -> tuple[list[StepGate], list[str]] | None:
    """(verifying steps' records, steps whose records cannot be read); ``None`` when the
    records show no verify stage at all (the G family does not apply)."""
    found: list[StepGate] = []
    unread: list[str] = []
    for step, agent in steps:
        workflow = _json(agent / WORKFLOW_RECORD)
        if not isinstance(workflow, dict):
            unread.append(step)
            continue
        if "verify" not in (workflow.get("stages") or []):
            continue
        record = workflow.get("verify")
        if not isinstance(record, dict):
            unread.append(step)
            continue
        found.append(StepGate(step, record, _hook_runs(agent, record)))
    if not found:
        return None
    return found, unread


def _gates(gate: StepGate) -> list[dict[str, Any]]:
    return [g for g in gate.record.get("gates") or [] if isinstance(g, dict)]


def _missing(gate: StepGate) -> str | None:
    """Why a step's G1/G2 evidence cannot be read, if it cannot."""
    if not _gates(gate):
        return f"{gate.step}: no seal record"
    if gate.hook_runs is None:
        return f"{gate.step}: the hook's run log is missing or unreadable"
    return None


def _gate_input(run: dict[str, Any]) -> tuple[str, str, tuple[str, ...]]:
    """The committed tree, checked workspace and non-tree workflow prerequisites."""
    return (
        str(run.get("tree")),
        str(run.get("worktree_sha256")),
        tuple(
            str(check.get("input_sha256"))
            for check in run.get("checks", [])
            if check.get("category") == "prerequisite"
        ),
    )


def _sealed_verdicts(gate: StepGate) -> dict[tuple[str, str, tuple[str, ...]], str]:
    """Each complete checked input a seal decided, with its verdict."""
    decided = (g for g in _gates(gate) if g.get("verdict") in DECIDED and g.get("tree"))
    return {_gate_input(g): g["verdict"] for g in decided}


def _missing_worktree(gate: StepGate) -> bool:
    return any(
        not isinstance(run.get("worktree_sha256"), str)
        for run in [*_gates(gate), *(gate.hook_runs or [])]
        if run.get("verdict") in DECIDED
    )


def _g1_step(gate: StepGate) -> tuple[list[str], str | None]:
    missing = _missing(gate)
    if missing is not None:
        return [], missing
    if _missing_worktree(gate):
        return [], f"{gate.step}: a decided run holds no checked workspace digest"
    seals = _sealed_verdicts(gate)
    if not seals:
        return [], f"{gate.step}: no seal ran its checks to a verdict"
    both = [
        (_gate_input(run), str(run.get("verdict")))
        for run in gate.hook_runs or []
        if run.get("verdict") in DECIDED and _gate_input(run) in seals
    ]
    return [
        f"{gate.step}: tree {key[0][:12]} hook {verdict}, seal {seals[key]}"
        for key, verdict in both
        if seals[key] != verdict
    ], None


def _moved_hooks(gate: StepGate) -> list[str]:
    expected = gate.record.get("hooks_path")
    return [
        f"{gate.step}: core.hooksPath was {g.get('hooks_path')!r} at seal {g.get('run')}"
        for g in _gates(gate)
        if "hooks_path" in g and g["hooks_path"] != expected
    ]


def _hook_passed(gate: StepGate) -> set[str]:
    return {str(run.get("tree")) for run in gate.hook_runs or [] if run.get("verdict") == "passed"}


def _unhooked_commits(gate: StepGate) -> list[str]:
    """Every commit since the base must hold a tree its hook passed; seals never commit."""
    gates = _gates(gate)
    commits = {c["commit"]: c for g in gates for c in g.get("commits") or []}
    passed = _hook_passed(gate)
    return [
        f"{gate.step}: commit {c['commit'][:12]} ({c.get('subject', '')[:40]!r}) holds a tree "
        "no hook run passed"
        for c in commits.values()
        if c.get("tree") not in passed
    ]


def _g2_step(gate: StepGate) -> tuple[list[str], str | None]:
    missing = _missing(gate)
    if missing is not None:
        return [], missing
    if any("base_is_ancestor" not in g for g in _gates(gate)):
        return [], f"{gate.step}: a seal failed before it read the repository"
    if any(g["base_is_ancestor"] is False for g in _gates(gate)):
        return [], f"{gate.step}: the checkpoint's base is no longer in HEAD's history"
    return _moved_hooks(gate) + _unhooked_commits(gate), None


def _tool_readings(gates: list[StepGate]) -> tuple[list[tuple[str, str]], list[str]]:
    """(where, digest) of the tool directory per step, before and after; what is missing."""
    readings: list[tuple[str, str]] = []
    missing: list[str] = []
    for gate in gates:
        tools = gate.record.get("tools") or {}
        for point in ("sha256_before", "sha256_after"):
            value = tools.get(point)
            if isinstance(value, str) and value:
                readings.append((f"{gate.step}/{point}", value))
            else:
                missing.append(f"{gate.step}: no {point}")
    return readings, missing


def _g3(gates: list[StepGate], unread: list[str]) -> HealthCheck:
    """One digest for the tool directory, before the first turn and after the last, every step."""
    readings, missing = _tool_readings(gates)
    unmeasured = [f"{step}: no readable verify record" for step in unread] + missing
    first = readings[0][1] if readings else ""
    changed = [f"{where}: {value[:12]}" for where, value in readings if value != first]
    if changed:
        detail = f"the tool directory was {first[:12]} first, then " + ", ".join(changed)
        return make_check("G3", VIOLATION, detail, readings=dict(readings))
    if unmeasured:
        return make_check("G3", UNMEASURED, "; ".join(unmeasured), unmeasured=unmeasured)
    return make_check("G3", PASS, f"tool directory {first[:12]} unchanged", sha256=first)


def _family(
    check_id: str,
    gates: list[StepGate],
    unread: list[str],
    per_step: Callable[[StepGate], tuple[list[str], str | None]],
    passed: str,
) -> HealthCheck:
    findings: list[str] = []
    unmeasured = [f"{step}: no readable verify record" for step in unread]
    for gate in gates:
        found, missing = per_step(gate)
        findings += found
        unmeasured += [missing] if missing else []
    if findings:
        more = f"; and {len(findings) - _SHOWN} more" if len(findings) > _SHOWN else ""
        detail = "; ".join(findings[:_SHOWN]) + more
        return make_check(check_id, VIOLATION, detail, findings=findings, unmeasured=unmeasured)
    if unmeasured:
        return make_check(check_id, UNMEASURED, "; ".join(unmeasured), unmeasured=unmeasured)
    return make_check(check_id, PASS, passed, steps=[gate.step for gate in gates])


def gate_family(steps: Sequence[tuple[str, Path]]) -> tuple[HealthCheck, ...]:
    """``G1``-``G3`` over the steps' agent logs; none when no step ran a verify stage."""
    read = step_gates(steps)
    if read is None:
        return ()
    gates, unread = read
    hook_runs = sum(len(g.hook_runs or []) for g in gates)
    return (
        _family("G1", gates, unread, _g1_step, f"{hook_runs} hook run(s) agree with the seals"),
        _family("G2", gates, unread, _g2_step, "every agent commit ran the hook; hooks unmoved"),
        _g3(gates, unread),
    )


def check_gate(a: Audit) -> tuple[HealthCheck, ...]:
    return gate_family([(s.step, a.trial_dir / "steps" / s.step / "agent") for s in a.attempted])
