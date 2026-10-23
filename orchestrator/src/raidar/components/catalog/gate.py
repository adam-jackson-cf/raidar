"""``workflow`` built-in: guardrail-gate@1, the commit gate a real repository enforces.

The component wraps the arm's Harbor agent in
``raidar.components.runtime.guardrail_gate:GuardrailGateAgent`` (harness-agnostic: the
same wrapper and kwargs on codex and claude-code, no harness hooks). It names
no checks itself: ``with.command`` is the single gate entrypoint the runtime's
guardrails pack supplies (a pre-commit entrypoint or a ``make check``-style
script), so the component stays scoped to a runtime, never to one problem.

``with``:

- ``command`` (required): the gate command, run by bash in the workspace;
- ``mode``: ``commit`` (default: a real ``git commit`` with the command as the
  pre-commit hook) or ``command`` (the command alone);
- ``max_rounds``: fix sessions at most, 1..5 (default 3);
- ``timeout_sec``: one gate run's limit, 10..3600 (default 600).

A command that names the hidden verifier's paths is refused (F4).

The discovery probe requires the gate record with at least one gate run that
decided. At the first step it also fails when the gate could not run at all
(not installed, not runnable): that is infrastructure, not agent behaviour. A
session that raised is classified by its exception, as for review-loop@2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from raidar.components.catalog._common import (
    BOTH_HARNESSES,
    COMMON_FINGERPRINT,
    require_file_count,
    require_with_keys,
)
from raidar.components.definition.contribution import AgentWrapper, Contribution
from raidar.components.definition.evidence import StepEvidence
from raidar.components.definition.manifest import (
    ComponentConfigError,
    ComponentKind,
    ComponentManifest,
    ComponentUseContext,
    DiscoveryProbe,
    ProbeOutcome,
)
from raidar.components.runtime.gate_run import GATE_RECORD, SIGNAL, VERIFIER_PATH_RE, GateMode

GUARDRAIL_GATE_IMPORT_PATH = "raidar.components.runtime.guardrail_gate:GuardrailGateAgent"
WITH_KEYS = ("command", "mode", "max_rounds", "timeout_sec")


def _bounded_int(use: ComponentUseContext, key: str, default: int, low: int, high: int) -> int:
    value = use.with_.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ComponentConfigError(f"'with.{key}' must be an integer {low}..{high}")
    return value


class GuardrailGate:
    manifest = ComponentManifest(
        name="guardrail-gate",
        version="1",
        kind=ComponentKind.WORKFLOW,
        description=(
            "Run the workspace's commit gate after every session of the arm's agent; "
            "a failing gate refuses the commit and starts a fix session with its output"
        ),
        supports=BOTH_HARNESSES,
        fingerprint=(
            *COMMON_FINGERPRINT,
            "raidar/components/catalog/gate.py",
            "raidar/components/runtime/gate_run.py",
            "raidar/components/runtime/guardrail_gate.py",
            "raidar/components/runtime/review_loop.py",  # usage sums, inner agent, prompt budget
            "raidar/core/snapshot.py",  # SNAPSHOT_EXCLUDES go to .git/info/exclude
        ),
        discovery_probe=DiscoveryProbe(
            id="guardrail-gate-ran",
            description=(
                "The gate record shows at least one decided gate run; at the first step the "
                "gate command was runnable"
            ),
            evidence=("guardrail-gate/guardrail-gate.json", "guardrail-gate/gate_run_*.log"),
        ),
    )

    def options(self, use: ComponentUseContext) -> dict[str, object]:
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
        if mode not in {item.value for item in GateMode}:
            raise ComponentConfigError("'with.mode' must be 'commit' or 'command'")
        return {
            "command": command,
            "mode": mode,
            "max_rounds": _bounded_int(use, "max_rounds", 3, 1, 5),
            "timeout_sec": _bounded_int(use, "timeout_sec", 600, 10, 3600),
            "component": self.manifest.ref,
        }

    def contribute(self, use: ComponentUseContext) -> Contribution:
        wrapper = AgentWrapper(import_path=GUARDRAIL_GATE_IMPORT_PATH, kwargs=self.options(use))
        return Contribution(agent_wrapper=wrapper)

    def probe(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> ProbeOutcome:
        del use
        records = evidence.json_reports(GATE_RECORD)
        if not records:
            return ProbeOutcome(False, "no guardrail-gate record in this step's logs")
        record = records[0]
        runs = record.get("gate_runs") or []
        reason = str(record.get("termination_reason"))
        refusal = _refusal(record, runs, step_index)
        if refusal is not None:
            return refusal
        return ProbeOutcome(
            True,
            f"{len(runs)} gate run(s), {record.get('fix_rounds_used')} fix round(s); {reason}; "
            f"{SIGNAL}={record.get(SIGNAL)}",
            tuple(str(run.get("log")) for run in runs),
        )


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
    manipulation-check signal for an arm that uses guardrail-gate@1.
    """
    records = StepEvidence(agent_dir).json_reports(GATE_RECORD)
    value = records[0].get(SIGNAL) if records else None
    return value if isinstance(value, bool) else None


GUARDRAIL_GATE = GuardrailGate()
