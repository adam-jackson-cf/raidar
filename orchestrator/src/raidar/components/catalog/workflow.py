"""``workflow`` built-in: review-loop@2.

The component wraps the arm's Harbor agent in
``raidar.components.runtime.review_loop:ReviewLoopAgent``. Verdict feedback is
read before or after the ``VERDICT`` line, an unusable review is re-asked once,
and every parse is recorded in the loop record. The probe requires the loop
record the wrapper writes, with at least one completed review stage. What the
reviewer said (approve, changes, or no usable verdict) is the loop's outcome,
recorded in that record, not evidence about whether the component took effect.
"""

from __future__ import annotations

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
from raidar.components.runtime.review_loop import LOOP_RECORD

REVIEW_LOOP_IMPORT_PATH = "raidar.components.runtime.review_loop:ReviewLoopAgent"


class ReviewLoop:
    manifest = ComponentManifest(
        name="review-loop",
        version="2",
        kind=ComponentKind.WORKFLOW,
        description=(
            "Implement/review rounds in fresh sessions of the arm's agent; verdict "
            "feedback read before or after the VERDICT line, one re-ask when unusable"
        ),
        supports=BOTH_HARNESSES,
        fingerprint=(
            *COMMON_FINGERPRINT,
            "raidar/components/catalog/workflow.py",
            "raidar/components/runtime/review_loop.py",
            "raidar/core/snapshot.py",  # the reviewer diff leaves out SNAPSHOT_EXCLUDES
        ),
        discovery_probe=DiscoveryProbe(
            id="review-loop-reviewed",
            description="The loop record shows at least one completed review stage",
            evidence=("review-loop/review-loop.json", "review-loop/stage_*/trajectory.json"),
        ),
    )

    def rounds(self, use: ComponentUseContext) -> int:
        require_with_keys(use, allowed=("max_review_rounds",))
        require_file_count(use, minimum=0, maximum=0)
        rounds = use.with_.get("max_review_rounds", 3)
        if not isinstance(rounds, int) or isinstance(rounds, bool) or not 1 <= rounds <= 5:
            raise ComponentConfigError("'with.max_review_rounds' must be an integer 1..5")
        return rounds

    def contribute(self, use: ComponentUseContext) -> Contribution:
        wrapper = AgentWrapper(
            import_path=REVIEW_LOOP_IMPORT_PATH,
            kwargs={"max_review_rounds": self.rounds(use), "component": self.manifest.ref},
        )
        return Contribution(agent_wrapper=wrapper)

    def probe(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> ProbeOutcome:
        """A stage failure is not a component failure.

        The wrapper re-raises a failed stage's exception, so the step carries
        the inner agent's own error and ``execution.collect`` classifies it as
        for the unwrapped agent; failing the probe as well would turn a graded
        agent failure (a context-window overflow) into infra in this arm only. A
        stage cancelled by the agent timeout is the same case (AgentTimeoutError).
        """
        del step_index
        records = evidence.json_reports(LOOP_RECORD)
        if not records:
            return ProbeOutcome(False, "no review-loop record in this step's logs")
        record = records[0]
        stages = record.get("stages") or [{}]
        cancelled = str(stages[-1].get("error") or "").startswith("CancelledError")
        if record.get("termination_reason") == "stage_failed" or cancelled:
            error = record.get("error") or stages[-1].get("error")
            return ProbeOutcome(True, f"stage ended early ({error}); classified by its exception")
        reviews = [
            stage
            for stage in record.get("stages", [])
            if stage.get("stage") == "review" and stage.get("error") is None
        ]
        reason = str(record.get("termination_reason"))
        if not reviews:
            return ProbeOutcome(False, f"no completed review stage ({reason})")
        # A reviewer reply without a usable verdict is model behaviour: the loop
        # took effect and records the outcome, so the trajectory stays valid
        # (failing it here would exclude trajectories of this arm only).
        return ProbeOutcome(
            True,
            f"{len(reviews)} review stage(s); {reason}",
            tuple(str(stage.get("stage_dir")) for stage in reviews),
        )


REVIEW_LOOP = ReviewLoop()
