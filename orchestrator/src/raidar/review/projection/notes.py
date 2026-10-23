"""Run findings for the experiment epoch, rendered as review annotations.

Findings use the review surface's existing categories where the meaning is the
same (``failed-gate``, ``clean-verification``, ``unscored-run``) and new,
self-describing categories otherwise. Each finding names the span that holds
its evidence, so the surface can jump to it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from raidar.core.review_contract import ReviewAnnotation
from raidar.review.projection.spans import SpanBuilder

FindingKind = Literal["issue", "good", "note"]


@dataclass(frozen=True)
class EvidenceRef:
    source: str
    reference: str
    detail: str = ""

    def as_json(self) -> dict[str, str]:
        return {"source": self.source, "reference": self.reference, "detail": self.detail}


@dataclass(frozen=True)
class Finding:
    id: str
    kind: FindingKind
    category: str
    title: str
    detail: str
    span: str | None
    evidence: tuple[EvidenceRef, ...] = field(default_factory=tuple)

    def as_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "category": self.category,
            "title": self.title,
            "detail": self.detail,
            "evidence": [item.as_json() for item in self.evidence],
        }


def to_annotations(
    findings: Sequence[Finding], builder: SpanBuilder, created_at: int
) -> list[ReviewAnnotation]:
    return [
        ReviewAnnotation(
            id=f"{builder.run_id}:{finding.id}",
            run_id=builder.run_id,
            span_id=builder.span_id(finding.span) if finding.span else None,
            kind=finding.kind,
            note=f"{finding.title} - {finding.detail}" if finding.detail else finding.title,
            created_at=created_at,
            category=finding.category,
            evidence=[item.as_json() for item in finding.evidence],
        )
        for finding in findings
    ]


def finding_counts(
    items: Sequence[ReviewAnnotation],
) -> tuple[dict[str, int], dict[str, int]]:
    kinds: Counter[str] = Counter(item.kind for item in items)
    issues = Counter(item.category for item in items if item.kind == "issue")
    return {kind: kinds[kind] for kind in ("issue", "good", "note")}, dict(issues)
