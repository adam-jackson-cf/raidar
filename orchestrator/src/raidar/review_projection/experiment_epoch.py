"""Experiment-epoch producer: run directories + Assessment stores -> the frozen projection.

Frozen concept -> source (design memo §12.1):

- ``scenario`` -> the problem; ``revision`` -> the arm's condition (suffixed with
  the digest of its locked contents when two experiments give one condition id
  different contents; the harness an arm compiles it for is not part of it, so
  arms of one condition on different harnesses share one revision);
- one experiment record per (experiment, lock, problem, arm, agent override,
  ``max_checkpoints`` truncation): development runs are never pooled with arm
  evidence, and their headline is withheld (``headline.withheld_reasons``);
- ``composite_score`` -> the headline ScorePolicy's S/100 (``headline``);
- gates -> one gate per checkpoint plus the validity gates (``gates``);
- spans -> Harbor ATIF trajectories, one parent span per checkpoint (``atif``);
- revision diffs -> condition diffs with arm-diff-guard warnings (``conditions``).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from raidar.execution.models import TrajectoryRecord
from raidar.review_projection.assessments import AssessmentLookup
from raidar.review_projection.conditions import ArmSide, condition_diff
from raidar.review_projection.experiments import ArmGroup, experiment_record
from raidar.review_projection.headline import headline, withheld_reasons
from raidar.review_projection.sources import RunSource
from raidar.review_projection.spans import safe_id
from raidar.review_projection.trials import ArmLabels, TrialInput, run_detail
from raidar.schemas.review import (
    ReviewExperimentRecord,
    ReviewRunDetail,
    RevisionChangeCategory,
    RevisionDiffArtifact,
)
from raidar.spec.schema import ExperimentSpec

_OVERRIDE_MODELS = {"oracle": "reference solution", "nop": "no agent"}
GroupKey = tuple[str, str, str, str, str, str]


@dataclass
class EpochProjection:
    experiments: list[ReviewExperimentRecord] = field(default_factory=list)
    details: list[ReviewRunDetail] = field(default_factory=list)
    diffs: list[RevisionDiffArtifact] = field(default_factory=list)


@dataclass(frozen=True)
class _Trial:
    source: RunSource
    record: TrajectoryRecord

    @property
    def arm(self) -> str:
        return self.record.trial.arm

    @property
    def problem(self) -> str:
        return self.record.trial.problem

    @property
    def condition(self) -> str:
        return next(arm.condition for arm in self.source.lock.spec.arms if arm.id == self.arm)

    @property
    def condition_digest(self) -> str:
        """The condition's locked contents; ``hashes.condition`` also covers the arm's harness."""
        return self.source.lock.condition_digest(self.condition)

    @property
    def group_key(self) -> GroupKey:
        source = self.source
        override = source.agent_override or ""
        limit = self.record.truncated_to
        truncated = f"first-{limit}" if limit is not None else ""
        return (source.experiment, source.lock.sha256, self.problem, self.arm, override, truncated)


def _condition_labels(trials: Sequence[_Trial]) -> dict[tuple[str, str, str], str]:
    """(problem, condition id, contents digest) -> label; an id reused with other contents
    gets its digest as a suffix."""
    digests: dict[tuple[str, str], set[str]] = defaultdict(set)
    for trial in trials:
        digests[(trial.problem, trial.condition)].add(trial.condition_digest)
    return {
        (problem, condition, digest): condition if len(seen) == 1 else f"{condition}-{digest[:8]}"
        for (problem, condition), seen in digests.items()
        for digest in seen
    }


def _review_ids(trials: Sequence[_Trial]) -> dict[int, str]:
    """``<run>-<trial>``, qualified by the experiment only where two runs share a run id."""
    short = [safe_id(f"{t.source.run_id}-{t.record.trial.trial_id}") for t in trials]
    counts = Counter(short)
    return {
        index: name if counts[name] == 1 else safe_id(f"{trial.source.experiment}-{name}")
        for index, (trial, name) in enumerate(zip(trials, short, strict=True))
    }


def _experiment_id(key: GroupKey) -> str:
    experiment, lock, problem, arm, override, truncated = key
    suffix = f":{override}" if override else ""
    suffix += f":{truncated}" if truncated else ""
    return f"{experiment}:{problem}:{arm}@{lock[:8]}{suffix}"


def _labels(trial: _Trial, revision: str) -> ArmLabels:
    spec = trial.source.lock.spec
    arm = next(item for item in spec.arms if item.id == trial.arm)
    agent = spec.agent(arm.agent)
    override = trial.source.agent_override
    if override is not None:
        harness, model, effort = override, _OVERRIDE_MODELS.get(override, override), None
        agent_spec = f"{override} override · {model}"
    else:
        harness, effort = agent.harness, agent.reasoning_effort
        model = f"{agent.provider}/{agent.model}"
        agent_spec = f"{harness} · {model}" + (f" [{effort}]" if effort else "")
    truncated = trial.record.truncated_to
    if truncated is not None:
        agent_spec += f" · dev run, first {truncated} checkpoint(s)"
    return ArmLabels(
        experiment_id=_experiment_id(trial.group_key),
        problem=trial.problem,
        revision=revision,
        harness=str(harness),
        model=model,
        reasoning_effort=effort,
        agent_spec=agent_spec,
    )


def _trial_input(
    trial: _Trial, review_id: str, revision: str, lookup: AssessmentLookup
) -> TrialInput:
    source, record = trial.source, trial.record
    trial_dir = source.run_dir / record.trial_dir
    scorers = source.lock.spec.assessment.scorers
    assessments = lookup.for_trial(
        trial_dir, scorers, source.assessment, config_path=source.assessment_path
    )
    return TrialInput(
        review_id=review_id,
        run_dir=source.run_dir,
        run_started=source.manifest.get("started_at"),
        record=record,
        labels=_labels(trial, revision),
        assessments=assessments,
        headline=headline(
            source.lock.headline_policy,
            len(record.attempts),
            assessments,
            withheld_reasons(record),
        ),
        synthetic=source.synthetic,
    )


def _diffs(
    inputs: Sequence[tuple[_Trial, TrialInput]],
) -> dict[tuple[str, str, str], RevisionDiffArtifact]:
    """Condition diffs by (lock, problem, arm), each from its baseline counterpart arm."""
    first: dict[tuple[str, str, str], tuple[_Trial, TrialInput]] = {}
    for trial, view in inputs:
        first.setdefault((trial.source.lock.sha256, trial.problem, trial.arm), (trial, view))
    diffs: dict[tuple[str, str, str], RevisionDiffArtifact] = {}
    for (lock, problem, arm), (trial, view) in sorted(first.items(), key=lambda item: item[0]):
        spec = trial.source.lock.spec
        baseline = first.get((lock, problem, baseline_counterpart(spec, arm)))
        if baseline is None or baseline[1].labels.revision == view.labels.revision:
            continue
        diffs[(lock, problem, arm)] = condition_diff(
            spec, problem, _side(*baseline), _side(trial, view)
        )
    return diffs


def baseline_counterpart(spec: ExperimentSpec, arm_id: str) -> str:
    """The arm to diff against: the baseline condition with the same agent, else the baseline arm.

    Pairing by agent keeps a condition diff about the condition when agents are also a factor.
    """
    baseline = next(arm for arm in spec.arms if arm.id == spec.analysis.baseline_arm)
    agent = next(arm.agent for arm in spec.arms if arm.id == arm_id)
    return next(
        (arm.id for arm in spec.arms if arm.condition == baseline.condition and arm.agent == agent),
        baseline.id,
    )


def _side(trial: _Trial, view: TrialInput) -> ArmSide:
    spec = trial.source.lock.spec
    arm = next(item for item in spec.arms if item.id == trial.arm)
    return ArmSide(
        arm=arm,
        label=view.labels.revision,
        manifest=trial.source.lock.conditions.get(trial.condition, []),
        task_dir=trial.source.run_dir / "tasks" / trial.record.trial.trial_id,
    )


def _rules_artifacts(manifest: object) -> dict[str, str]:
    if not isinstance(manifest, list):
        return {}
    return {
        path: str(digest)
        for item in manifest
        if isinstance(item, Mapping)
        for path, digest in (item.get("files") or {}).items()
    }


def _group(
    key: GroupKey,
    members: Sequence[tuple[_Trial, TrialInput, ReviewRunDetail]],
    diffs: Mapping[tuple[str, str, str], RevisionDiffArtifact],
) -> ArmGroup:
    trial = members[0][0]
    lock = trial.source.lock
    diff = diffs.get((lock.sha256, trial.problem, trial.arm))
    categories: list[RevisionChangeCategory] = (
        list(diff.change_categories) if diff is not None else ["initial-revision"]
    )
    manifest = lock.conditions.get(trial.condition, [])
    return ArmGroup(
        experiment_id=_experiment_id(key),
        spec=lock.spec,
        arm=trial.arm,
        condition_manifest=manifest,
        rules_artifacts=_rules_artifacts(manifest),
        parent_revision=diff.from_revision if diff is not None else None,
        change_categories=categories,
        headline_policy=members[0][1].headline.policy,
        trials=[view for _, view, _ in members],
        details=[detail for _, _, detail in members],
    )


def project_sources(sources: Sequence[RunSource], lookup: AssessmentLookup) -> EpochProjection:
    trials = [_Trial(source, record) for source in sources for record in source.records]
    labels = _condition_labels(trials)
    ids = _review_ids(trials)
    inputs = [
        (
            trial,
            _trial_input(
                trial,
                ids[index],
                labels[(trial.problem, trial.condition, trial.condition_digest)],
                lookup,
            ),
        )
        for index, trial in enumerate(trials)
    ]
    diffs = _diffs(inputs)
    grouped: dict[GroupKey, list[tuple[_Trial, TrialInput, ReviewRunDetail]]] = defaultdict(list)
    for trial, view in inputs:
        grouped[trial.group_key].append((trial, view, run_detail(view)))
    unique = {diff.key: diff for diff in reversed(list(diffs.values()))}
    projection = EpochProjection(diffs=sorted(unique.values(), key=lambda diff: diff.key))
    for key in sorted(grouped):
        group = _group(key, grouped[key], diffs)
        projection.experiments.append(experiment_record(group))
        projection.details.extend(group.details)
    return projection
