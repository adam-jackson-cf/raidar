"""``workflow`` built-in: guardrail-gate@4, guardrail-gate@3 with an optional autofix step.

guardrail-gate@1-@3 are frozen. @4 takes @3's ``with`` keys, modes and
discovery probe, plus:

- ``autofix_before_check`` (bool, default false): run the pack's safe
  auto-fixers before each commit check (``commit`` mode), or on a throwaway
  copy beside the shadow check (``shadow`` mode); not allowed in ``command``
  mode. False leaves @4 behaving exactly as @3;
- ``autofix_command`` (required when ``autofix_before_check`` is true, refused
  otherwise): the pack's safe-fix command, for example python-guardrails@8's
  ``guardrail-check --autofix``. The component names no fixer itself.

Its wrapper is ``raidar.components.runtime.guardrail_gate_v4:GuardrailGateV4Agent``.
With autofix on, the probe also requires the record's ``autofix`` block to say
it was enabled (an autofix pass that failed is recorded as a typed error and
falls back to @3's check; it does not fail the step).
"""

from __future__ import annotations

from dataclasses import replace

from raidar.components.catalog._common import COMMON_FINGERPRINT, require_with_keys
from raidar.components.catalog.gate import WITH_KEYS
from raidar.components.catalog.gate_v3 import GuardrailGateV3
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
from raidar.components.runtime.gate_run import GATE_RECORD, VERIFIER_PATH_RE
from raidar.components.runtime.gate_run_v2 import GateModeV2

GUARDRAIL_GATE_V4_IMPORT_PATH = "raidar.components.runtime.guardrail_gate_v4:GuardrailGateV4Agent"
AUTOFIX_KEYS = ("autofix_before_check", "autofix_command")
WITH_KEYS_V4 = (*WITH_KEYS, *AUTOFIX_KEYS)


def autofix_options(use: ComponentUseContext, mode: str) -> dict[str, object]:
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
    if mode == GateModeV2.COMMAND.value:
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


class GuardrailGateV4(GuardrailGateV3):
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
        supports=GuardrailGateV3.manifest.supports,
        fingerprint=(
            *COMMON_FINGERPRINT,
            "raidar/components/catalog/gate.py",
            "raidar/components/catalog/gate_v2.py",
            "raidar/components/catalog/gate_v3.py",
            "raidar/components/catalog/gate_v4.py",
            "raidar/components/runtime/gate_run.py",
            "raidar/components/runtime/gate_run_v2.py",
            "raidar/components/runtime/autofix_run.py",
            "raidar/components/runtime/guardrail_gate.py",
            "raidar/components/runtime/guardrail_gate_v2.py",
            "raidar/components/runtime/guardrail_gate_v3.py",
            "raidar/components/runtime/guardrail_gate_v4.py",
            "raidar/components/runtime/session_chain.py",
            "raidar/components/runtime/review_loop.py",  # usage sums, prompt budget, step logs
            "raidar/core/snapshot.py",  # SNAPSHOT_EXCLUDES, the pre-fix tree digest
        ),
        discovery_probe=DiscoveryProbe(
            id="guardrail-gate-ran",
            description=(
                f"{GuardrailGateV3.manifest.discovery_probe.description}; with "
                "autofix_before_check, the record's autofix block says autofix was enabled"
            ),
            evidence=(
                *GuardrailGateV3.manifest.discovery_probe.evidence,
                "guardrail-gate/autofix_run_*.log",
            ),
        ),
    )

    def options(self, use: ComponentUseContext) -> dict[str, object]:
        require_with_keys(use, allowed=WITH_KEYS_V4, required=("command",))
        base = {key: value for key, value in use.with_.items() if key not in AUTOFIX_KEYS}
        options = super().options(replace(use, with_=base))
        mode = str(options["mode"])
        return {**options, **autofix_options(use, mode), "component": self.manifest.ref}

    def contribute(self, use: ComponentUseContext) -> Contribution:
        wrapper = AgentWrapper(import_path=GUARDRAIL_GATE_V4_IMPORT_PATH, kwargs=self.options(use))
        return Contribution(agent_wrapper=wrapper)

    def probe(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> ProbeOutcome:
        outcome = super().probe(use, evidence, step_index)
        if not outcome.passed or not use.with_.get("autofix_before_check", False):
            return outcome
        records = evidence.json_reports(GATE_RECORD)
        block = records[0].get("autofix") if records else None
        if not isinstance(block, dict) or block.get("enabled") is not True:
            return ProbeOutcome(False, "autofix_before_check is set but the record has no autofix")
        runs = block.get("runs") or []
        detail = f"{outcome.detail}; autofix {len(runs)} pass(es), {block.get('errors')} error(s)"
        return ProbeOutcome(True, detail, outcome.evidence)


GUARDRAIL_GATE_V4 = GuardrailGateV4()
