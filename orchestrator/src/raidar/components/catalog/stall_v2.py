"""``workflow`` built-in: continue-on-stall@2, one continuation per step (first session only).

continue-on-stall@1 is frozen. @2
(``raidar.components.runtime.continue_on_stall_v2:ContinueOnStallV2Agent``) keeps @1's
session wrapper, record and probe, and changes two things:

- a no-edit session that ends saying it cannot proceed without something
  ("can't ... without", "not enough context", "... isn't defined") stalls,
  as a question does;
- only the step's first session can stall and be continued (at most one
  continuation per step), so an arm whose gate starts fix sessions has the
  same exposure as an arm with one session per step. Later sessions are
  recorded (``eligible: false``) and never continued.

Put it in ``shared_components`` to apply it identically to every arm. It takes
no ``with`` keys and no files; nothing in it names a problem.
"""

from __future__ import annotations

from raidar.components.catalog._common import BOTH_HARNESSES, COMMON_FINGERPRINT
from raidar.components.catalog.stall import ContinueOnStall
from raidar.components.definition.contribution import AgentWrapper
from raidar.components.definition.manifest import (
    ComponentKind,
    ComponentManifest,
    ComponentUseContext,
    DiscoveryProbe,
)
from raidar.components.runtime.continue_on_stall import STALL_RECORD
from raidar.components.runtime.continue_on_stall_v2 import COMPONENT

CONTINUE_ON_STALL_V2_IMPORT_PATH = (
    "raidar.components.runtime.continue_on_stall_v2:ContinueOnStallV2Agent"
)


class ContinueOnStallV2(ContinueOnStall):
    manifest = ComponentManifest(
        name="continue-on-stall",
        version="2",
        kind=ComponentKind.WORKFLOW,
        description=(
            "When the first session of a step changes no workspace file and ends asking a "
            "question or saying it cannot proceed without more information, start one "
            "continuation session: no human is available, proceed with the specification "
            "and reasonable assumptions recorded in ASSUMPTIONS.md (at most one per step)"
        ),
        supports=BOTH_HARNESSES,
        fingerprint=(
            *COMMON_FINGERPRINT,
            "raidar/components/catalog/stall.py",
            "raidar/components/catalog/stall_v2.py",
            "raidar/components/runtime/continue_on_stall.py",
            "raidar/components/runtime/continue_on_stall_v2.py",
            "raidar/components/runtime/session_chain.py",
            "raidar/components/runtime/review_loop.py",  # final message, usage sums, prompt budget
            "raidar/core/snapshot.py",  # the workspace and its snapshot excludes
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

    def session_wrapper(self, use: ComponentUseContext) -> AgentWrapper:
        self._validate(use)
        return AgentWrapper(
            import_path=CONTINUE_ON_STALL_V2_IMPORT_PATH, kwargs={"component": COMPONENT}
        )


CONTINUE_ON_STALL_V2 = ContinueOnStallV2()
