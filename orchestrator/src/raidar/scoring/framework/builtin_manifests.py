"""Manifests of the built-in reboot scorers (plan WS-G G1, G2/G3, G4, G5).

The manifests are the published contract the lock resolves and the Assessment
store keys on. Implementations are bound in ``catalog`` once WS-G lands them;
until then ``catalog`` reports each scorer as having no implementation rather
than producing any value.
"""

from __future__ import annotations

from raidar.scoring.framework.manifest import (
    Direction,
    EvidenceInput,
    Exactness,
    MetricLevel,
    MetricSpec,
    MetricType,
    ScorerManifest,
    Tier,
)
from raidar.subject.environment.python.pack_definition import ToolExecution

_CHECKPOINT = MetricLevel.CHECKPOINT
_TRANSITION = MetricLevel.TRANSITION
_TRAJECTORY = MetricLevel.TRAJECTORY
_HIGHER = Direction.HIGHER_BETTER
_LOWER = Direction.LOWER_BETTER
_DET = Tier.DETERMINISTIC


def _group_rate(group: str) -> MetricSpec:
    return MetricSpec(
        id=f"{group}_pass_rate",
        description=f"Passed / graded {group} cases at the checkpoint (SCB all-cases rule).",
        level=_CHECKPOINT,
        type=MetricType.RATIO,
        direction=_HIGHER,
        tier=_DET,
        exactness=Exactness.SCB_EXACT,
    )


CORRECTNESS = ScorerManifest(
    id="correctness",
    version="10",
    description=(
        "Reference-solvable SCB success and raw SCB verdicts per checkpoint and trajectory."
    ),
    runtimes=("*",),
    evidence_required=(EvidenceInput.ORACLE_CASES,),
    metrics=(
        MetricSpec(
            id="solved",
            description=(
                "Strict solve: every graded case passed and grading had no infrastructure failure."
            ),
            level=_CHECKPOINT,
            type=MetricType.BOOLEAN,
            direction=_HIGHER,
            tier=_DET,
            exactness=Exactness.SCB_EXACT,
        ),
        MetricSpec(
            id="strict_pass_rate",
            description="Passed / graded cases across all groups at the checkpoint.",
            level=_CHECKPOINT,
            type=MetricType.RATIO,
            direction=_HIGHER,
            tier=_DET,
            exactness=Exactness.SCB_EXACT,
        ),
        _group_rate("core"),
        _group_rate("functionality"),
        _group_rate("error"),
        _group_rate("regression"),
        MetricSpec(
            id="regression_failures",
            description="Graded regression cases (from earlier checkpoints) that did not pass.",
            level=_CHECKPOINT,
            type=MetricType.COUNT,
            direction=_LOWER,
            tier=_DET,
            exactness=Exactness.NATIVE,
        ),
        MetricSpec(
            id="solved_checkpoints",
            description="Checkpoints solved in the trajectory (continue view); C = this / K.",
            level=_TRAJECTORY,
            type=MetricType.COUNT,
            direction=_HIGHER,
            tier=_DET,
            exactness=Exactness.SCB_EXACT,
        ),
        MetricSpec(
            id="reached",
            description="Consecutive checkpoints solved from the first (strict view).",
            level=_TRAJECTORY,
            type=MetricType.COUNT,
            direction=_HIGHER,
            tier=_DET,
            exactness=Exactness.NATIVE,
        ),
        MetricSpec(
            id="solved_errata",
            description=(
                "Errata-applied solve: the strict rule over the graded cases the lock's pinned "
                "problem errata keeps (exclude-from-solved cases left out); not_applicable without "
                "an errata."
            ),
            level=_CHECKPOINT,
            type=MetricType.BOOLEAN,
            direction=_HIGHER,
            tier=_DET,
            exactness=Exactness.NATIVE,
        ),
        MetricSpec(
            id="solved_checkpoints_errata",
            description="Errata-applied solved checkpoints in the trajectory (continue view).",
            level=_TRAJECTORY,
            type=MetricType.COUNT,
            direction=_HIGHER,
            tier=_DET,
            exactness=Exactness.NATIVE,
        ),
        MetricSpec(
            id="reached_errata",
            description=(
                "Errata-applied consecutive checkpoints solved from the first (strict view)."
            ),
            level=_TRAJECTORY,
            type=MetricType.COUNT,
            direction=_HIGHER,
            tier=_DET,
            exactness=Exactness.NATIVE,
        ),
        MetricSpec(
            id="solved_solvable",
            description=(
                "Reference-solvable SCB success: every case the reference passes in every "
                "validation repeat passed; unavailable without a solvable set, never zero. Not "
                "specification compliance."
            ),
            level=_CHECKPOINT,
            type=MetricType.BOOLEAN,
            direction=_HIGHER,
            tier=_DET,
            exactness=Exactness.NATIVE,
        ),
        MetricSpec(
            id="solved_checkpoints_solvable",
            description=(
                "Reference-solvable SCB successful checkpoints in the trajectory (continue view)."
            ),
            level=_TRAJECTORY,
            type=MetricType.COUNT,
            direction=_HIGHER,
            tier=_DET,
            exactness=Exactness.NATIVE,
        ),
        MetricSpec(
            id="reached_solvable",
            description="Reference-solvable SCB consecutive successful checkpoints (strict view).",
            level=_TRAJECTORY,
            type=MetricType.COUNT,
            direction=_HIGHER,
            tier=_DET,
            exactness=Exactness.NATIVE,
        ),
    ),
)


def _component(metric_id: str, name: str, level: MetricLevel) -> MetricSpec:
    return MetricSpec(
        id=metric_id,
        description=f"SCB {name} component in [0, 1] (1 is best).",
        level=level,
        type=MetricType.RATIO,
        direction=_HIGHER,
        tier=_DET,
        exactness=Exactness.SCB_EXACT,
    )


CODE_QUALITY = ScorerManifest(
    id="code-quality",
    version="9",
    description=(
        "SCB-fork code-quality and inertia components over the per-step snapshots; a step "
        "graded without the in-trial coverage pass reads G's coverage run from the offline "
        "run in the Assessment store, and G leaves out at most 3 cases whose outcome differs "
        "between the grading and coverage runs instead of reading the step as ineligible."
    ),
    runtimes=("python",),
    evidence_required=(
        EvidenceInput.SNAPSHOT_CURRENT,
        EvidenceInput.SNAPSHOT_PRIOR,
        EvidenceInput.ORACLE_CASES,
    ),
    tools=("scb-python-quality", "scb-check"),
    execution=ToolExecution.HOST_PARSE,
    metrics=(
        _component("verbosity", "verbosity (V)", _CHECKPOINT),
        _component("erosion", "erosion (E)", _CHECKPOINT),
        _component("architecture", "architecture (H)", _CHECKPOINT),
        _component("rework", "rework (R)", _TRANSITION),
        _component("regression", "regression breadth (G)", _TRANSITION),
        MetricSpec(
            id="lint_findings",
            description="Ruff diagnostics under the SCB contract in production files.",
            level=_CHECKPOINT,
            type=MetricType.COUNT,
            direction=_LOWER,
            tier=_DET,
            exactness=Exactness.SCB_EXACT,
        ),
        MetricSpec(
            id="production_loc",
            description="Production lines of code in the assessor-owned inventory.",
            level=_CHECKPOINT,
            type=MetricType.COUNT,
            direction=Direction.NEUTRAL,
            tier=_DET,
            exactness=Exactness.SCB_EXACT,
            unit="lines",
        ),
        _component("problem_verbosity", "problem-level verbosity mean (V)", _TRAJECTORY),
        _component("problem_erosion", "problem-level erosion mean (E)", _TRAJECTORY),
        _component("problem_architecture", "problem-level architecture mean (H)", _TRAJECTORY),
        _component("problem_rework", "problem-level rework mean (R)", _TRAJECTORY),
        _component("problem_regression", "problem-level regression-breadth mean (G)", _TRAJECTORY),
        MetricSpec(
            id="regression_parity_excluded_cases",
            description=(
                "Grader cases G left out at the transition because their outcome differed between "
                "the grading run and the coverage run (0 when parity held)."
            ),
            level=_TRANSITION,
            type=MetricType.COUNT,
            direction=Direction.NEUTRAL,
            tier=_DET,
            exactness=Exactness.NATIVE,
            unit="cases",
        ),
    ),
)


def _usage(metric_id: str, description: str, level: MetricLevel, unit: str) -> MetricSpec:
    metric_type = MetricType.REAL if unit in {"usd", "seconds"} else MetricType.COUNT
    return MetricSpec(
        id=metric_id,
        description=description,
        level=level,
        type=metric_type,
        direction=_LOWER,
        tier=_DET,
        exactness=Exactness.NATIVE,
        unit=unit,
    )


RESOURCE_USAGE = ScorerManifest(
    id="resource-usage",
    version="8",
    description=(
        "Tokens, canonical-table agent cost plus host judge spend, turns and agent wall time."
    ),
    runtimes=("*",),
    evidence_required=(EvidenceInput.TRACE,),
    metrics=(
        _usage("input_tokens", "Input tokens used in the step.", _CHECKPOINT, "tokens"),
        _usage("output_tokens", "Output tokens used in the step.", _CHECKPOINT, "tokens"),
        _usage(
            "cost_usd", "Agent model spend plus host judge spend in the step.", _CHECKPOINT, "usd"
        ),
        _usage("turns", "Agent turns in the step.", _CHECKPOINT, "turns"),
        _usage("agent_wall_s", "Agent execution wall time in the step.", _CHECKPOINT, "seconds"),
        _usage(
            "total_tokens", "Input plus output tokens over the trajectory.", _TRAJECTORY, "tokens"
        ),
        _usage(
            "total_cost_usd",
            "Agent model spend plus host judge spend over the trajectory.",
            _TRAJECTORY,
            "usd",
        ),
        _usage("total_turns", "Agent turns over the trajectory.", _TRAJECTORY, "turns"),
        _usage(
            "total_agent_wall_s",
            "Agent execution wall time over the trajectory.",
            _TRAJECTORY,
            "seconds",
        ),
    ),
)

# Not registered: the metric base ``process-compliance@12`` derives its deterministic metrics
# from (``process_checks.manifest``); the Jev-tier metric is the fixture of the Jev gating tests.
PROCESS_COMPLIANCE = ScorerManifest(
    id="process-compliance",
    version="1",
    description="Deterministic trace checks of agent process, with a Jev shadow for ambiguity.",
    runtimes=("*",),
    evidence_required=(EvidenceInput.TRACE, EvidenceInput.SNAPSHOT_CURRENT),
    metrics=(
        MetricSpec(
            id="tests_run_before_finish",
            description="The agent ran the project's tests after its last source edit in the step.",
            level=_CHECKPOINT,
            type=MetricType.BOOLEAN,
            direction=_HIGHER,
            tier=_DET,
            exactness=Exactness.NATIVE,
        ),
        MetricSpec(
            id="plan_written",
            description="The agent wrote a plan before its first source edit in the step.",
            level=_CHECKPOINT,
            type=MetricType.BOOLEAN,
            direction=_HIGHER,
            tier=_DET,
            exactness=Exactness.NATIVE,
        ),
        MetricSpec(
            id="compliance_rate",
            description="Share of deterministic checks passed over the trajectory.",
            level=_TRAJECTORY,
            type=MetricType.RATIO,
            direction=_HIGHER,
            tier=_DET,
            exactness=Exactness.NATIVE,
        ),
        MetricSpec(
            id="jev_ambiguous_compliance",
            description="Jev probability that ambiguous steps complied (shadow; uncalibrated).",
            level=_CHECKPOINT,
            type=MetricType.PROBABILITY,
            direction=_HIGHER,
            tier=Tier.JEV,
            exactness=Exactness.NATIVE,
        ),
    ),
)

BUILTIN_MANIFESTS: tuple[ScorerManifest, ...] = (
    CORRECTNESS,
    CODE_QUALITY,
    RESOURCE_USAGE,
)
