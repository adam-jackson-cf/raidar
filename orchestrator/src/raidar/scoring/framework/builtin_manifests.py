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


def _metric(
    metric_id: str,
    description: str,
    level: MetricLevel,
    metric_type: MetricType,
    direction: Direction,
    exactness: Exactness,
    *,
    tier: Tier = _DET,
    unit: str | None = None,
) -> MetricSpec:
    return MetricSpec(
        id=metric_id,
        description=description,
        level=level,
        type=metric_type,
        direction=direction,
        tier=tier,
        exactness=exactness,
        unit=unit,
    )


def _group_rate(group: str) -> MetricSpec:
    return _metric(
        f"{group}_pass_rate",
        f"Passed / graded {group} cases at the checkpoint (SCB all-cases rule).",
        _CHECKPOINT,
        MetricType.RATIO,
        _HIGHER,
        Exactness.SCB_EXACT,
    )


CORRECTNESS = ScorerManifest(
    id="correctness",
    version="1",
    description="Oracle verdicts per checkpoint and trajectory, from the oracle step records.",
    runtimes=("*",),
    evidence_required=(EvidenceInput.ORACLE_CASES,),
    metrics=(
        _metric(
            "solved",
            "Strict solve: every graded case passed and grading had no infrastructure failure.",
            _CHECKPOINT,
            MetricType.BOOLEAN,
            _HIGHER,
            Exactness.SCB_EXACT,
        ),
        _metric(
            "strict_pass_rate",
            "Passed / graded cases across all groups at the checkpoint.",
            _CHECKPOINT,
            MetricType.RATIO,
            _HIGHER,
            Exactness.SCB_EXACT,
        ),
        _group_rate("core"),
        _group_rate("functionality"),
        _group_rate("error"),
        _group_rate("regression"),
        _metric(
            "regression_failures",
            "Graded regression cases (from earlier checkpoints) that did not pass.",
            _CHECKPOINT,
            MetricType.COUNT,
            _LOWER,
            Exactness.NATIVE,
        ),
        _metric(
            "solved_checkpoints",
            "Checkpoints solved in the trajectory (continue view); C = this / K.",
            _TRAJECTORY,
            MetricType.COUNT,
            _HIGHER,
            Exactness.SCB_EXACT,
        ),
        _metric(
            "reached",
            "Consecutive checkpoints solved from the first (strict view).",
            _TRAJECTORY,
            MetricType.COUNT,
            _HIGHER,
            Exactness.NATIVE,
        ),
        _metric(
            "solved_errata",
            "Errata-applied solve: the strict rule over the graded cases the lock's pinned "
            "problem errata keeps (exclude-from-solved cases left out); not_applicable "
            "without an errata.",
            _CHECKPOINT,
            MetricType.BOOLEAN,
            _HIGHER,
            Exactness.NATIVE,
        ),
        _metric(
            "solved_checkpoints_errata",
            "Errata-applied solved checkpoints in the trajectory (continue view).",
            _TRAJECTORY,
            MetricType.COUNT,
            _HIGHER,
            Exactness.NATIVE,
        ),
        _metric(
            "reached_errata",
            "Errata-applied consecutive checkpoints solved from the first (strict view).",
            _TRAJECTORY,
            MetricType.COUNT,
            _HIGHER,
            Exactness.NATIVE,
        ),
    ),
)


def _component(metric_id: str, name: str, level: MetricLevel) -> MetricSpec:
    return _metric(
        metric_id,
        f"SCB {name} component in [0, 1] (1 is best).",
        level,
        MetricType.RATIO,
        _HIGHER,
        Exactness.SCB_EXACT,
    )


CODE_QUALITY = ScorerManifest(
    id="code-quality",
    version="3",
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
        _metric(
            "lint_findings",
            "Ruff diagnostics under the SCB contract in production files.",
            _CHECKPOINT,
            MetricType.COUNT,
            _LOWER,
            Exactness.SCB_EXACT,
        ),
        _metric(
            "production_loc",
            "Production lines of code in the assessor-owned inventory.",
            _CHECKPOINT,
            MetricType.COUNT,
            Direction.NEUTRAL,
            Exactness.SCB_EXACT,
            unit="lines",
        ),
        _component("problem_verbosity", "problem-level verbosity mean (V)", _TRAJECTORY),
        _component("problem_erosion", "problem-level erosion mean (E)", _TRAJECTORY),
        _component("problem_architecture", "problem-level architecture mean (H)", _TRAJECTORY),
        _component("problem_rework", "problem-level rework mean (R)", _TRAJECTORY),
        _component("problem_regression", "problem-level regression-breadth mean (G)", _TRAJECTORY),
        _metric(
            "regression_parity_excluded_cases",
            "Grader cases G left out at the transition because their outcome differed "
            "between the grading run and the coverage run (0 when parity held).",
            _TRANSITION,
            MetricType.COUNT,
            Direction.NEUTRAL,
            Exactness.NATIVE,
            unit="cases",
        ),
    ),
)


def _usage(metric_id: str, description: str, level: MetricLevel, unit: str) -> MetricSpec:
    metric_type = MetricType.REAL if unit in {"usd", "seconds"} else MetricType.COUNT
    return _metric(metric_id, description, level, metric_type, _LOWER, Exactness.NATIVE, unit=unit)


RESOURCE_USAGE = ScorerManifest(
    id="resource-usage",
    version="1",
    description="Tokens, cost, turns and agent wall time from Harbor per-step usage and timing.",
    runtimes=("*",),
    evidence_required=(EvidenceInput.TRACE,),
    metrics=(
        _usage("input_tokens", "Input tokens used in the step.", _CHECKPOINT, "tokens"),
        _usage("output_tokens", "Output tokens used in the step.", _CHECKPOINT, "tokens"),
        _usage("cost_usd", "Model spend in the step.", _CHECKPOINT, "usd"),
        _usage("turns", "Agent turns in the step.", _CHECKPOINT, "turns"),
        _usage("agent_wall_s", "Agent execution wall time in the step.", _CHECKPOINT, "seconds"),
        _usage(
            "total_tokens", "Input plus output tokens over the trajectory.", _TRAJECTORY, "tokens"
        ),
        _usage("total_cost_usd", "Model spend over the trajectory.", _TRAJECTORY, "usd"),
        _usage("total_turns", "Agent turns over the trajectory.", _TRAJECTORY, "turns"),
        _usage(
            "total_agent_wall_s",
            "Agent execution wall time over the trajectory.",
            _TRAJECTORY,
            "seconds",
        ),
    ),
)

# Not registered: the metric base ``process-compliance@6`` derives its deterministic metrics
# from (``process_checks.manifest``); the Jev-tier metric is the fixture of the Jev gating tests.
PROCESS_COMPLIANCE = ScorerManifest(
    id="process-compliance",
    version="1",
    description="Deterministic trace checks of agent process, with a Jev shadow for ambiguity.",
    runtimes=("*",),
    evidence_required=(EvidenceInput.TRACE, EvidenceInput.SNAPSHOT_CURRENT),
    metrics=(
        _metric(
            "tests_run_before_finish",
            "The agent ran the project's tests after its last source edit in the step.",
            _CHECKPOINT,
            MetricType.BOOLEAN,
            _HIGHER,
            Exactness.NATIVE,
        ),
        _metric(
            "plan_written",
            "The agent wrote a plan before its first source edit in the step.",
            _CHECKPOINT,
            MetricType.BOOLEAN,
            _HIGHER,
            Exactness.NATIVE,
        ),
        _metric(
            "compliance_rate",
            "Share of deterministic checks passed over the trajectory.",
            _TRAJECTORY,
            MetricType.RATIO,
            _HIGHER,
            Exactness.NATIVE,
        ),
        _metric(
            "jev_ambiguous_compliance",
            "Jev probability that ambiguous steps complied (shadow; uncalibrated).",
            _CHECKPOINT,
            MetricType.PROBABILITY,
            _HIGHER,
            Exactness.NATIVE,
            tier=Tier.JEV,
        ),
    ),
)

BUILTIN_MANIFESTS: tuple[ScorerManifest, ...] = (
    CORRECTNESS,
    CODE_QUALITY,
    RESOURCE_USAGE,
)
