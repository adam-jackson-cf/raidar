"""``workflow`` built-in: review-loop@2 (gate packet F3, pilot v2).

Same contract, configuration and discovery probe as review-loop@1
(``raidar.components.builtins.workflow``, left unchanged because its semantics
are locked). The wrapper is ``raidar.components.review_loop_v2:ReviewLoopV2Agent``:
verdict feedback is read before or after the ``VERDICT`` line, an unusable review
is re-asked once, and every parse is recorded in the loop record.
"""

from __future__ import annotations

from raidar.components.builtins._common import COMMON_FINGERPRINT
from raidar.components.builtins.workflow import ReviewLoop
from raidar.components.contribution import AgentWrapper, Contribution
from raidar.components.evidence import StepEvidence
from raidar.components.manifest import (
    ComponentKind,
    ComponentManifest,
    ComponentUseContext,
    DiscoveryProbe,
    ProbeOutcome,
)
from raidar.components.review_loop import LOOP_RECORD

REVIEW_LOOP_V2_IMPORT_PATH = "raidar.components.review_loop_v2:ReviewLoopV2Agent"


class ReviewLoopV2(ReviewLoop):
    manifest = ComponentManifest(
        name="review-loop",
        version="2",
        kind=ComponentKind.WORKFLOW,
        description=(
            "Implement/review rounds in fresh sessions of the arm's agent; verdict "
            "feedback read before or after the VERDICT line, one re-ask when unusable"
        ),
        supports=ReviewLoop.manifest.supports,
        fingerprint=(
            *COMMON_FINGERPRINT,
            "raidar/components/builtins/workflow.py",
            "raidar/components/builtins/workflow_v2.py",
            "raidar/components/review_loop.py",
            "raidar/oracle/snapshot.py",  # the reviewer diff leaves out SNAPSHOT_EXCLUDES
            "raidar/components/review_loop_v2.py",
        ),
        discovery_probe=DiscoveryProbe(
            id="review-loop-reviewed",
            description="The loop record shows at least one completed review stage",
            evidence=("review-loop/review-loop.json", "review-loop/stage_*/trajectory.json"),
        ),
    )

    def probe(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> ProbeOutcome:
        """@1's probe, except that a stage failure is not a component failure.

        review-loop@2 re-raises a failed stage's exception, so the step carries
        the inner agent's own error and ``execution.collect`` classifies it as
        for the unwrapped agent; failing the probe as well would turn a graded
        agent failure (a context-window overflow) into infra in this arm only. A
        stage cancelled by the agent timeout is the same case (AgentTimeoutError).
        """
        records = evidence.json_reports(LOOP_RECORD)
        record = records[0] if records else {}
        stages = record.get("stages") or [{}]
        cancelled = str(stages[-1].get("error") or "").startswith("CancelledError")
        if record.get("termination_reason") == "stage_failed" or cancelled:
            error = record.get("error") or stages[-1].get("error")
            return ProbeOutcome(True, f"stage ended early ({error}); classified by its exception")
        return super().probe(use, evidence, step_index)

    def contribute(self, use: ComponentUseContext) -> Contribution:
        wrapper = AgentWrapper(
            import_path=REVIEW_LOOP_V2_IMPORT_PATH,
            kwargs={"max_review_rounds": self.rounds(use), "component": self.manifest.ref},
        )
        return Contribution(agent_wrapper=wrapper)


REVIEW_LOOP_V2 = ReviewLoopV2()
