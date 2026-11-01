"""Experiment-epoch producer: job directories + Assessment stores -> the review projection.

Concept -> source (design memo §12.1):

- ``problem`` -> the problem; ``arm`` -> the arm id; the agent labels (harness, model,
  reasoning, workflow release) come from the lock's ``execution.arms[<arm>]``;
- one experiment record per (experiment, lock, problem, arm, agent override,
  ``max_checkpoints`` truncation): development runs are never pooled with arm
  evidence, and their headline is withheld (``headline.withheld_reasons``);
- ``composite_score`` -> the headline metric's S/100 (``headline``);
- gates -> one gate per checkpoint plus the validity gates (``gates``);
- spans -> Harbor ATIF trajectories, one parent span per checkpoint (``atif``).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from raidar.contract.review import ReviewExperimentRecord, ReviewRunDetail
from raidar.contract.trial import TrialRecord
from raidar.evaluation.review.projection.assessments import TrialAssessmentLookup
from raidar.evaluation.review.projection.experiments import ArmGroup, experiment_record
from raidar.evaluation.review.projection.headline import headline, withheld_reasons
from raidar.evaluation.review.projection.sources import JobSource
from raidar.evaluation.review.projection.spans import TextReader, read_disk, safe_id
from raidar.evaluation.review.projection.trials import ArmLabels, TrialInput, run_detail

_OVERRIDE_MODELS = {"oracle": "reference solution", "nop": "no agent"}
GroupKey = tuple[str, str, str, str, str, str]


@dataclass
class EpochProjection:
    experiments: list[ReviewExperimentRecord] = field(default_factory=list)
    details: list[ReviewRunDetail] = field(default_factory=list)


@dataclass(frozen=True)
class _Trial:
    source: JobSource
    record: TrialRecord

    @property
    def arm(self) -> str:
        return self.record.trial.arm

    @property
    def problem(self) -> str:
        return self.record.trial.problem

    @property
    def group_key(self) -> GroupKey:
        source = self.source
        override = source.agent_override or ""
        limit = self.record.truncated_to
        truncated = f"first-{limit}" if limit is not None else ""
        return (source.experiment, source.lock.sha256, self.problem, self.arm, override, truncated)


def _review_ids(trials: Sequence[_Trial]) -> dict[int, str]:
    """``<job>-<trial>``, qualified by the experiment only where two runs share a job id."""
    short = [safe_id(f"{t.source.job_id}-{t.record.trial.trial_id}") for t in trials]
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


def _labels(trial: _Trial) -> ArmLabels:
    arm = trial.source.lock.arms[trial.arm]
    override = trial.source.agent_override
    if override is not None:
        harness, model, effort = override, _OVERRIDE_MODELS.get(override, override), None
        agent_spec = f"{override} override · {model}"
    else:
        harness, effort = str(arm["adapter"]), arm.get("reasoning")
        model = f"{arm['provider']}/{arm['model']}"
        agent_spec = f"{harness} · {model}" + (f" [{effort}]" if effort else "")
        agent_spec += f" · {arm['workflow']}"
    truncated = trial.record.truncated_to
    if truncated is not None:
        agent_spec += f" · dev job, first {truncated} checkpoint(s)"
    return ArmLabels(
        experiment_id=_experiment_id(trial.group_key),
        problem=trial.problem,
        arm=trial.arm,
        harness=harness,
        model=model,
        reasoning_effort=effort,
        agent_spec=agent_spec,
    )


def _trial_input(
    trial: _Trial,
    review_id: str,
    lookup: TrialAssessmentLookup,
    read: TextReader,
) -> TrialInput:
    source, record = trial.source, trial.record
    trial_dir = source.job_dir / record.trial_dir
    scorers = source.lock.spec.assessment.scorers
    assessments = lookup.for_trial(
        trial_dir, scorers, source.assessment, config_path=source.assessment_path
    )
    return TrialInput(
        review_id=review_id,
        job_dir=source.job_dir,
        run_started=source.manifest.get("started_at"),
        record=record,
        labels=_labels(trial),
        assessments=assessments,
        headline=headline(
            source.lock.metric,
            record.trial.steps,
            assessments,
            withheld_reasons(record),
            declared=scorers,
        ),
        synthetic=source.synthetic,
        integrity_note=source.integrity_note,
        read=read,
    )


def _group(
    key: GroupKey, members: Sequence[tuple[_Trial, TrialInput, ReviewRunDetail]]
) -> ArmGroup:
    trial = members[0][0]
    return ArmGroup(
        experiment_id=_experiment_id(key),
        spec=trial.source.lock.spec,
        arm=trial.arm,
        metric=members[0][1].headline.policy,
        trials=[view for _, view, _ in members],
        details=[detail for _, _, detail in members],
    )


def project_sources(
    sources: Sequence[JobSource],
    lookup: TrialAssessmentLookup,
    read: TextReader = read_disk,
) -> EpochProjection:
    """Project the jobs admitted by the source loader's integrity/provenance gate."""
    trials = [_Trial(source, record) for source in sources for record in source.records]
    ids = _review_ids(trials)
    inputs = [
        (trial, _trial_input(trial, ids[index], lookup, read)) for index, trial in enumerate(trials)
    ]
    grouped: dict[GroupKey, list[tuple[_Trial, TrialInput, ReviewRunDetail]]] = defaultdict(list)
    for trial, view in inputs:
        grouped[trial.group_key].append((trial, view, run_detail(view)))
    projection = EpochProjection()
    for key in sorted(grouped):
        group = _group(key, grouped[key])
        projection.experiments.append(experiment_record(group))
        projection.details.extend(group.details)
    return projection
