"""``workflow`` built-in: guardrail-gate@3, guardrail-gate@2 with pre-fix snapshots.

guardrail-gate@1 and @2 are frozen. @3 takes @2's ``with`` keys, modes
(``commit``, ``command``, ``shadow``) and discovery probe, and its wrapper is
``raidar.components.runtime.guardrail_gate_v3:GuardrailGateV3Agent``:

- before every fix session it snapshots the workspace (the verifier's artifact
  mechanism and excludes) under the step's agent logs and records the tree
  digest, so offline grading can compare a fix round's starting tree with the
  step's final snapshot (``gate-effort@4`` ``fix_round_correctness_delta``);
- its inner agent may be a session wrapper (``continue-on-stall@1``), nested
  by the execution layer, so a stalled session is continued before the gate runs.
"""

from __future__ import annotations

from raidar.components.catalog._common import COMMON_FINGERPRINT
from raidar.components.catalog.gate_v2 import GuardrailGateV2
from raidar.components.definition.contribution import AgentWrapper, Contribution
from raidar.components.definition.manifest import (
    ComponentKind,
    ComponentManifest,
    ComponentUseContext,
)

GUARDRAIL_GATE_V3_IMPORT_PATH = "raidar.components.runtime.guardrail_gate_v3:GuardrailGateV3Agent"


class GuardrailGateV3(GuardrailGateV2):
    manifest = ComponentManifest(
        name="guardrail-gate",
        version="3",
        kind=ComponentKind.WORKFLOW,
        description=(
            "Run the workspace's commit gate after every session of the arm's agent; a "
            "failing gate refuses the commit and starts a fix session with its output, after "
            "snapshotting the workspace the fix starts from; or, in shadow mode, record the "
            "same check at each step's end without enforcing it; every commit attempt "
            "records git evidence"
        ),
        supports=GuardrailGateV2.manifest.supports,
        fingerprint=(
            *COMMON_FINGERPRINT,
            "raidar/components/catalog/gate.py",
            "raidar/components/catalog/gate_v2.py",
            "raidar/components/catalog/gate_v3.py",
            "raidar/components/runtime/gate_run.py",
            "raidar/components/runtime/gate_run_v2.py",
            "raidar/components/runtime/guardrail_gate.py",
            "raidar/components/runtime/guardrail_gate_v2.py",
            "raidar/components/runtime/guardrail_gate_v3.py",
            "raidar/components/runtime/session_chain.py",
            "raidar/components/runtime/review_loop.py",  # usage sums, prompt budget, step logs
            "raidar/core/snapshot.py",  # SNAPSHOT_EXCLUDES and the pre-fix tree digest
        ),
        discovery_probe=GuardrailGateV2.manifest.discovery_probe,
    )

    def contribute(self, use: ComponentUseContext) -> Contribution:
        wrapper = AgentWrapper(import_path=GUARDRAIL_GATE_V3_IMPORT_PATH, kwargs=self.options(use))
        return Contribution(agent_wrapper=wrapper)


GUARDRAIL_GATE_V3 = GuardrailGateV3()
