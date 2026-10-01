"""``workflow`` built-in: review-loop (C4).

The component wraps the arm's Harbor agent in
``raidar.components.review_loop:ReviewLoopAgent``. The probe requires the loop
record the wrapper writes, with at least one completed review stage. What the
reviewer said (approve, changes, or no usable verdict) is the loop's outcome,
recorded in that record, not evidence about whether the component took effect.
"""

from __future__ import annotations

from raidar.components.builtins._common import (
    BOTH_HARNESSES,
    COMMON_FINGERPRINT,
    require_file_count,
    require_with_keys,
)
from raidar.components.contribution import AgentWrapper, Contribution
from raidar.components.evidence import StepEvidence
from raidar.components.manifest import (
    ComponentConfigError,
    ComponentKind,
    ComponentManifest,
    ComponentUseContext,
    DiscoveryProbe,
    ProbeOutcome,
)
from raidar.components.review_loop import LOOP_RECORD

REVIEW_LOOP_IMPORT_PATH = "raidar.components.review_loop:ReviewLoopAgent"


class ReviewLoop:
    manifest = ComponentManifest(
        name="review-loop",
        version="1",
        kind=ComponentKind.WORKFLOW,
        description="Implement/review rounds in fresh sessions of the arm's agent",
        supports=BOTH_HARNESSES,
        fingerprint=(
            *COMMON_FINGERPRINT,
            "raidar/components/builtins/workflow.py",
            "raidar/components/review_loop.py",
            "raidar/oracle/snapshot.py",  # the reviewer diff leaves out SNAPSHOT_EXCLUDES
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
        del step_index
        records = evidence.json_reports(LOOP_RECORD)
        if not records:
            return ProbeOutcome(False, "no review-loop record in this step's logs")
        record = records[0]
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
