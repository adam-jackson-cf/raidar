"""``workflow`` built-in: guardrail-gate@4, the commit gate a real repository enforces.

The component wraps the arm's Harbor agent in
``raidar.subject.treatment.runtime.guardrail_gate:GuardrailGateAgent`` (harness-agnostic: the
same wrapper and kwargs on codex and claude-code, no harness hooks). It names
no checks itself: ``with.command`` is the single gate entrypoint the runtime's
guardrails pack supplies (a pre-commit entrypoint or a ``make check``-style
script), so the component stays scoped to a runtime, never to one problem.

``with``:

- ``command`` (required): the gate command, run by bash in the workspace;
- ``mode``: ``commit`` (default: a real ``git commit`` with the command as the
  pre-commit hook), ``command`` (the command alone) or ``shadow`` (the same
  commit check, run once at each step's end against a git directory outside the
  workspace; the full gate run is recorded, but the gate never refuses and never
  starts a fix session, and the workspace is never written: no ``.git``, no
  commit, no hook. A baseline arm gets the gate's first-run findings while its
  agent's experience stays unchanged; pair it with a guardrails pack that puts
  nothing in the workspace, ``python-guardrails`` with ``workspace_configs: false``);
- ``max_rounds``: fix sessions at most, 1..5 (default 3);
- ``timeout_sec``: one gate run's limit, 10..3600 (default 600);
- ``autofix_before_check`` (bool, default false): run the pack's safe
  auto-fixers before each commit check (``commit`` mode), or on a throwaway
  copy beside the shadow check (``shadow`` mode); not allowed in ``command``
  mode;
- ``autofix_command`` (required when ``autofix_before_check`` is true, refused
  otherwise): the pack's safe-fix command, for example python-guardrails@8's
  ``guardrail-check --autofix``. The component names no fixer itself.

A command that names the hidden verifier's paths is refused (F4).

Every commit attempt records git evidence (``HEAD`` before and after,
``git log --oneline``, ``git status --porcelain``, the hook's and ``git
commit``'s exit codes) in the gate record, outside the workspace. Before every
fix session the workspace is snapshotted (the verifier's artifact mechanism and
excludes) under the step's agent logs and its tree digest recorded, so offline
grading can compare a fix round's starting tree with the step's final snapshot.
The inner agent may be a session wrapper (``continue-on-stall``), nested by the
execution layer, so a stalled session is continued before the gate runs.

The discovery probe requires the gate record with at least one gate run that
decided. At the first step it also fails when the gate could not run at all
(not installed, not runnable): that is infrastructure, not agent behaviour. A
session that raised is classified by its exception, as for review-loop@2. With
autofix on, the probe also requires the record's ``autofix`` block to say it was
enabled (an autofix pass that failed is recorded as a typed error and the check
runs without it; it does not fail the step).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import JsonValue

from raidar.subject.treatment.catalog._common import (
    BOTH_HARNESSES,
    COMMON_FINGERPRINT,
    require_file_count,
    require_with_keys,
)
from raidar.subject.treatment.definition.contribution import AgentWrapper, Contribution
from raidar.subject.treatment.definition.evidence import StepEvidence
from raidar.subject.treatment.definition.manifest import (
    ComponentConfigError,
    ComponentKind,
    ComponentManifest,
    ComponentUseContext,
    DiscoveryProbe,
    ProbeOutcome,
)
from raidar.subject.treatment.runtime.gate_run import (
    GATE_RECORD,
    SIGNAL,
    VERIFIER_PATH_RE,
    GateMode,
)

GUARDRAIL_GATE_IMPORT_PATH = "raidar.subject.treatment.runtime.guardrail_gate:GuardrailGateAgent"
AUTOFIX_KEYS = ("autofix_before_check", "autofix_command")
WITH_KEYS = ("command", "mode", "max_rounds", "timeout_sec", *AUTOFIX_KEYS)
MODES = tuple(item.value for item in GateMode)


def _bounded_int(use: ComponentUseContext, key: str, default: int, low: int, high: int) -> int:
    value = use.with_.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ComponentConfigError(f"'with.{key}' must be an integer {low}..{high}")
    return value


def autofix_options(use: ComponentUseContext, mode: str) -> dict[str, JsonValue]:
    """The validated autofix keys (``{}`` when autofix is off)."""
    enabled = use.with_.get("autofix_before_check", False)
    if not isinstance(enabled, bool):
        raise ComponentConfigError("'with.autofix_before_check' must be true or false")
    command = use.with_.get("autofix_command")
    if not enabled:
        if command is not None:
            raise ComponentConfigError(
                "'with.autofix_command' is set but 'with.autofix_before_check' is not true"
            )
        return {}
    if mode == GateMode.COMMAND.value:
        raise ComponentConfigError("autofix runs before a commit check: not in 'command' mode")
    if not isinstance(command, str) or not command.strip():
        raise ComponentConfigError(
            "'with.autofix_command' must be the pack's safe-fix command (a non-empty string)"
        )
    if VERIFIER_PATH_RE.search(command):
        raise ComponentConfigError(
            "'with.autofix_command' names a hidden verifier path; the gate never touches them"
        )
    return {"autofix_before_check": True, "autofix_command": command}


class GuardrailGate:
    manifest = ComponentManifest(
        name="guardrail-gate",
        version="4",
        kind=ComponentKind.WORKFLOW,
        description=(
            "Run the workspace's commit gate after every session of the arm's agent; a "
            "failing gate refuses the commit and starts a fix session with its output, after "
            "snapshotting the workspace the fix starts from; or, in shadow mode, record the "
            "same check at each step's end without enforcing it; every commit attempt "
            "records git evidence; optionally (autofix_before_check) the pack's safe "
            "auto-fixers run before each commit check, or, in shadow mode, on a throwaway "
            "copy checked beside the raw tree, with what they changed recorded"
        ),
        supports=BOTH_HARNESSES,
        fingerprint=(
            *COMMON_FINGERPRINT,
            "raidar/subject/treatment/catalog/gate.py",
            "raidar/subject/treatment/runtime/gate_run.py",
            "raidar/subject/treatment/runtime/autofix_run.py",
            "raidar/subject/treatment/runtime/guardrail_gate.py",
            "raidar/subject/treatment/runtime/session_chain.py",
            "raidar/subject/treatment/runtime/review_loop.py",  # usage sums, budget, step logs
            "raidar/contract/snapshot.py",  # SNAPSHOT_EXCLUDES, the pre-fix tree digest
        ),
        discovery_probe=DiscoveryProbe(
            id="guardrail-gate-ran",
            description=(
                "The gate record shows at least one decided gate run; at the first step the "
                "gate command was runnable; with autofix_before_check, the record's autofix "
                "block says autofix was enabled"
            ),
            evidence=(
                "guardrail-gate/guardrail-gate.json",
                "guardrail-gate/gate_run_*.log",
                "guardrail-gate/autofix_run_*.log",
            ),
        ),
    )

    def options(self, use: ComponentUseContext) -> dict[str, JsonValue]:
        require_with_keys(use, allowed=WITH_KEYS, required=("command",))
        require_file_count(use, minimum=0, maximum=0)
        command = use.with_["command"]
        if not isinstance(command, str) or not command.strip():
            raise ComponentConfigError("'with.command' must be a non-empty string")
        if VERIFIER_PATH_RE.search(command):
            raise ComponentConfigError(
                "'with.command' names a hidden verifier path; the gate never runs verifier tests"
            )
        mode = use.with_.get("mode", GateMode.COMMIT.value)
        if mode not in MODES:
            raise ComponentConfigError(f"'with.mode' must be one of {', '.join(MODES)}")
        return {
            "command": command,
            "mode": mode,
            "max_rounds": _bounded_int(use, "max_rounds", 3, 1, 5),
            "timeout_sec": _bounded_int(use, "timeout_sec", 600, 10, 3600),
            **autofix_options(use, str(mode)),
            "component": self.manifest.ref,
        }

    def contribute(self, use: ComponentUseContext) -> Contribution:
        wrapper = AgentWrapper(import_path=GUARDRAIL_GATE_IMPORT_PATH, kwargs=self.options(use))
        return Contribution(agent_wrapper=wrapper)

    def probe(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> ProbeOutcome:
        records = evidence.json_reports(GATE_RECORD)
        if not records:
            return ProbeOutcome(False, "no guardrail-gate record in this step's logs")
        record = records[0]
        runs = record.get("gate_runs") or []
        reason = str(record.get("termination_reason"))
        refusal = _refusal(record, runs, step_index)
        if refusal is not None:
            return refusal
        detail = (
            f"{len(runs)} gate run(s), {record.get('fix_rounds_used')} fix round(s); {reason}; "
            f"{SIGNAL}={record.get(SIGNAL)}"
        )
        logs = tuple(str(run.get("log")) for run in runs)
        if not use.with_.get("autofix_before_check", False):
            return ProbeOutcome(True, detail, logs)
        block = record.get("autofix")
        if not isinstance(block, dict) or block.get("enabled") is not True:
            return ProbeOutcome(False, "autofix_before_check is set but the record has no autofix")
        passes = block.get("runs") or []
        detail += f"; autofix {len(passes)} pass(es), {block.get('errors')} error(s)"
        return ProbeOutcome(True, detail, logs)


def _refusal(
    record: dict[str, Any], runs: list[dict[str, Any]], step_index: int
) -> ProbeOutcome | None:
    """The probe's outcome when the record does not show a decided gate run."""
    reason, error = record.get("termination_reason"), record.get("error")
    if reason in ("stage_failed", "cancelled") and not runs:
        # The step carries the inner agent's own exception (as for review-loop@2).
        return ProbeOutcome(True, f"session ended early ({error}); classified by its exception")
    if step_index == 0 and runs and runs[0].get("error"):
        return ProbeOutcome(False, f"the gate could not run at the first step: {runs[0]['error']}")
    if not any(run.get("error") is None for run in runs):
        return ProbeOutcome(False, f"no gate run decided ({reason}: {error})")
    return None


def gate_signal(agent_dir: Path) -> bool | None:
    """The step's ``gate_clean_at_step_end`` from its archived agent logs (``None``: no gate).

    Deterministic, read from the record the wrapper wrote; the per-step
    manipulation-check signal for an arm that uses guardrail-gate.
    """
    records = StepEvidence(agent_dir).json_reports(GATE_RECORD)
    value = records[0].get(SIGNAL) if records else None
    return value if isinstance(value, bool) else None


GUARDRAIL_GATE = GuardrailGate()
