"""``workflow`` built-in: continue-on-stall@1, one continuation when a session stalls on a question.

No human answers an agent during a trial. When a session of the arm's agent
ends having changed no workspace file and with a final message that asks a
question or for clarification or confirmation, the wrapper
(``raidar.components.continue_on_stall:ContinueOnStallAgent``) starts one
continuation session of the same agent: no human is available, proceed with
the specification and reasonable assumptions, record them in
``ASSUMPTIONS.md``. Detection is deterministic and recorded per session.

It is a *session* wrapper: it contributes no workflow wrapper (a condition has
at most one), and the execution layer nests it around the native agent, inside
the condition's workflow wrapper if there is one
(``raidar.components.session_chain``). With ``guardrail-gate@3`` every session
the gate starts runs through it, so a stalled work session is continued before
the gate runs. Put it in ``shared_components`` to apply it identically to every
arm. It takes no ``with`` keys and no files; nothing in it names a problem.

The discovery probe requires at least one continue-on-stall record in the
step's agent logs (one is written for every session, stalled or not).
"""

from __future__ import annotations

import json

from raidar.components.builtins._common import (
    BOTH_HARNESSES,
    COMMON_FINGERPRINT,
    require_file_count,
    require_with_keys,
)
from raidar.components.continue_on_stall import COMPONENT, STALL_RECORD
from raidar.components.contribution import AgentWrapper, Contribution
from raidar.components.evidence import StepEvidence
from raidar.components.manifest import (
    ComponentKind,
    ComponentManifest,
    ComponentUseContext,
    DiscoveryProbe,
    ProbeOutcome,
)

CONTINUE_ON_STALL_IMPORT_PATH = "raidar.components.continue_on_stall:ContinueOnStallAgent"


class ContinueOnStall:
    manifest = ComponentManifest(
        name="continue-on-stall",
        version="1",
        kind=ComponentKind.WORKFLOW,
        description=(
            "When a session of the arm's agent changes no workspace file and ends asking a "
            "question or for clarification, start one continuation session: no human is "
            "available, proceed with the specification and reasonable assumptions recorded "
            "in ASSUMPTIONS.md"
        ),
        supports=BOTH_HARNESSES,
        fingerprint=(
            *COMMON_FINGERPRINT,
            "raidar/components/builtins/stall.py",
            "raidar/components/continue_on_stall.py",
            "raidar/components/session_chain.py",
            "raidar/components/review_loop.py",  # final message, usage sums, prompt budget
            "raidar/oracle/snapshot.py",  # the workspace and its snapshot excludes
        ),
        discovery_probe=DiscoveryProbe(
            id="continue-on-stall-recorded",
            description=(
                "The step's agent logs hold a continue-on-stall record for its sessions "
                "(stalled or not)"
            ),
            evidence=(f"continue-on-stall/{STALL_RECORD}",),
        ),
    )

    def _validate(self, use: ComponentUseContext) -> None:
        require_with_keys(use, allowed=())
        require_file_count(use, minimum=0, maximum=0)

    def contribute(self, use: ComponentUseContext) -> Contribution:
        self._validate(use)
        return Contribution()

    def session_wrapper(self, use: ComponentUseContext) -> AgentWrapper:
        self._validate(use)
        return AgentWrapper(
            import_path=CONTINUE_ON_STALL_IMPORT_PATH, kwargs={"component": COMPONENT}
        )

    def probe(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> ProbeOutcome:
        del step_index
        self._validate(use)
        records = stall_records(evidence)
        if not records:
            return ProbeOutcome(False, "no continue-on-stall record in this step's logs")
        stalled = sum(1 for record in records if record.get("stalled") is True)
        continued = sum(1 for record in records if record.get("continued") is True)
        return ProbeOutcome(
            True,
            f"{len(records)} session(s) recorded; {stalled} stalled, {continued} continued",
            tuple(
                f"stalled={record.get('stalled')} continued={record.get('continued')}"
                for record in records
            ),
        )


def stall_records(evidence: StepEvidence) -> list[dict[str, object]]:
    """Every continue-on-stall record in a step's agent logs, in path order."""
    found: list[dict[str, object]] = []
    for path in sorted(evidence.agent_dir.rglob(STALL_RECORD)):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict):
            found.append(payload)
    return found


CONTINUE_ON_STALL = ContinueOnStall()
