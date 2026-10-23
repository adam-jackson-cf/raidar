"""``workflow`` built-in: guardrail-gate@2, guardrail-gate@1 with a shadow mode and git evidence.

guardrail-gate@1 (``raidar.components.catalog.gate``) is frozen: its bytes are
in released locks. @2 takes the same ``with`` keys and the same discovery probe,
and its wrapper is ``raidar.components.runtime.guardrail_gate_v2:GuardrailGateV2Agent``:

- ``mode`` adds ``shadow``: the same commit check as ``commit`` mode, run once at
  each step's end against a git directory outside the workspace; the full gate
  run is recorded, but the gate never refuses and never starts a fix session,
  and the workspace is never written (no ``.git``, no commit, no hook). A
  baseline arm gets the gate's first-run findings while its agent's experience
  stays unchanged; pair it with a guardrails pack that puts nothing in the
  workspace (``python-guardrails@2`` with ``workspace_configs: false``);
- every commit attempt records git evidence (``HEAD`` before and after,
  ``git log --oneline``, ``git status --porcelain``, the hook's and
  ``git commit``'s exit codes) in the gate record, outside the workspace.
"""

from __future__ import annotations

from raidar.components.catalog._common import (
    COMMON_FINGERPRINT,
    require_file_count,
    require_with_keys,
)
from raidar.components.catalog.gate import WITH_KEYS, GuardrailGate, _bounded_int
from raidar.components.definition.contribution import AgentWrapper, Contribution
from raidar.components.definition.manifest import (
    ComponentConfigError,
    ComponentKind,
    ComponentManifest,
    ComponentUseContext,
)
from raidar.components.runtime.gate_run import VERIFIER_PATH_RE
from raidar.components.runtime.gate_run_v2 import GateModeV2

GUARDRAIL_GATE_V2_IMPORT_PATH = "raidar.components.runtime.guardrail_gate_v2:GuardrailGateV2Agent"
MODES = tuple(item.value for item in GateModeV2)


class GuardrailGateV2(GuardrailGate):
    manifest = ComponentManifest(
        name="guardrail-gate",
        version="2",
        kind=ComponentKind.WORKFLOW,
        description=(
            "Run the workspace's commit gate after every session of the arm's agent; a "
            "failing gate refuses the commit and starts a fix session with its output; or, "
            "in shadow mode, record the same check at each step's end without enforcing it; "
            "every commit attempt records git evidence"
        ),
        supports=GuardrailGate.manifest.supports,
        fingerprint=(
            *COMMON_FINGERPRINT,
            "raidar/components/catalog/gate.py",
            "raidar/components/catalog/gate_v2.py",
            "raidar/components/runtime/gate_run.py",
            "raidar/components/runtime/gate_run_v2.py",
            "raidar/components/runtime/guardrail_gate.py",
            "raidar/components/runtime/guardrail_gate_v2.py",
            "raidar/components/runtime/review_loop.py",  # usage sums, inner agent, prompt budget
            "raidar/core/snapshot.py",  # SNAPSHOT_EXCLUDES go to the repository's exclude
        ),
        discovery_probe=GuardrailGate.manifest.discovery_probe,
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
        mode = use.with_.get("mode", GateModeV2.COMMIT.value)
        if mode not in MODES:
            raise ComponentConfigError(f"'with.mode' must be one of {', '.join(MODES)}")
        return {
            "command": command,
            "mode": mode,
            "max_rounds": _bounded_int(use, "max_rounds", 3, 1, 5),
            "timeout_sec": _bounded_int(use, "timeout_sec", 600, 10, 3600),
            "component": self.manifest.ref,
        }

    def contribute(self, use: ComponentUseContext) -> Contribution:
        wrapper = AgentWrapper(import_path=GUARDRAIL_GATE_V2_IMPORT_PATH, kwargs=self.options(use))
        return Contribution(agent_wrapper=wrapper)


GUARDRAIL_GATE_V2 = GuardrailGateV2()
