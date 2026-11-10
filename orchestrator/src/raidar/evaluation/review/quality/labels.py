"""Read Jev decisions/current labels and attach only unambiguous trial identities.

Decisions come from ``<store>/jev-audit`` and name their trial by a free string
(``raidar jev shadow-rubric --trial``). Full ``<experiment>.<job>.<trial>`` or
``<job>/<trial>`` names attach directly. A bare trial id attaches only when
exactly one projected trial has it; otherwise every affected trial explains
the ambiguity so a reviewer never labels a decision against another run.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from raidar.evaluation.review.quality.sources import TrialSource
from raidar.scoring.judge.audit import AUDIT_DIR, AuditLog, DecisionRecord, LabelRecord
from raidar.scoring.judge.ledger import current_labels


@dataclass(frozen=True)
class JevAttachment:
    """The decisions attached to one trial, and why others naming it were not."""

    decisions: tuple[DecisionRecord, ...] = ()
    note: str | None = None


@dataclass(frozen=True)
class JevAudit:
    decisions: dict[str, list[DecisionRecord]]
    labels: dict[str, LabelRecord]

    @classmethod
    def open(cls, store_root: Path) -> JevAudit:
        audit = AuditLog.for_store(store_root)
        root = store_root / AUDIT_DIR
        by_trial: dict[str, list[DecisionRecord]] = defaultdict(list)
        labels: dict[str, LabelRecord] = {}
        policies = (
            sorted(path.name for path in root.iterdir() if path.is_dir()) if root.is_dir() else []
        )
        for policy in policies:
            for record in audit.decisions(policy):
                if record.trial is not None:
                    by_trial[record.trial].append(record)
            labels.update(current_labels(audit, policy))
        return cls(decisions=dict(by_trial), labels=labels)

    def attach(self, trials: list[TrialSource]) -> dict[str, JevAttachment]:
        """Attach full review ids or job/trial names; bare trial ids must be unique."""
        by_bare: dict[str, list[TrialSource]] = defaultdict(list)
        for trial in trials:
            by_bare[trial.record.trial.trial_id].append(trial)
        attached: dict[str, JevAttachment] = {}
        for trial in trials:
            bare = trial.record.trial.trial_id
            names = (trial.review_id, f"{trial.job_id}/{bare}")
            decisions: list[DecisionRecord] = []
            for name in names:
                decisions.extend(self.decisions.get(name, []))
            unresolved = self.decisions.get(bare, [])
            note = None
            if len(by_bare[bare]) == 1:
                decisions.extend(unresolved)
            elif unresolved:
                note = (
                    f"{len(unresolved)} Jev decision(s) name trial {bare!r}, which "
                    f"{len(by_bare[bare])} runs share; not attached. Record them with "
                    f"--trial {trial.review_id} to review them here"
                )
            attached[trial.review_id] = JevAttachment(decisions=tuple(decisions), note=note)
        return attached
