"""Apply analysis' provenance and record-integrity gate before review aggregation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from raidar.evaluation.analysis.pooling import check_pooling
from raidar.evaluation.analysis.sources import JobDir


def check_review_jobs(jobs: Sequence[JobDir]) -> dict[Path, str]:
    """Refuse incompatible jobs within each experiment/lock; label historical inputs.

    The review may show several experiments and locks at once. Each is checked
    separately, and a job supplied by several roots is checked only once.
    Record integrity is checked even for a single modern job, as in analysis.
    """
    groups: dict[tuple[str, str], dict[Path, JobDir]] = defaultdict(dict)
    for job in jobs:
        key = (str(job.manifest.get("experiment")), str(job.manifest.get("lock_sha256")))
        groups[key][job.path.resolve()] = job
    notes = {}
    for members in groups.values():
        report = check_pooling(list(members.values()))
        for path, job in members.items():
            if job.job_id in report.unmeasured:
                notes[path] = "provenance unmeasured: historical job has no mechanics epoch"
    return notes
