"""Span assembly for the experiment-epoch review projection producer."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from raidar.schemas.review import ReviewSpan, ReviewSpanType, ReviewStatus

REPO_ROOT = Path(__file__).resolve().parents[4]
PAYLOAD_CAP = 32_000
_UNSAFE_ID_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def repo_path(path: Path) -> str:
    """A path as shown in the surface: relative to the repository root."""
    return os.path.relpath(path, REPO_ROOT)


def json_text(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def payload(value: object) -> str | None:
    """A span payload as text, capped so one span cannot dominate the projection."""
    if value is None:
        return None
    text = value if isinstance(value, str) else json_text(value)
    if len(text) <= PAYLOAD_CAP:
        return text
    return f"{text[:PAYLOAD_CAP]}\n...[truncated]"


def to_ms(value: str | int | float | None) -> int | None:
    """Epoch milliseconds from a number or an ISO timestamp (None when unparseable)."""
    if isinstance(value, (int, float)):
        return int(value)
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def safe_id(value: str) -> str:
    """A review id the surface can address in a URL path segment."""
    return _UNSAFE_ID_CHARS.sub("-", value).strip("-") or "run"


class SpanBuilder:
    """Appends spans with run-scoped sequential ids."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.spans: list[ReviewSpan] = []

    def add(
        self,
        *,
        name: str,
        span_type: ReviewSpanType,
        status: ReviewStatus,
        parent: str | None = None,
        start: int | None = None,
        duration: int | None = None,
        model: str | None = None,
        input_value: object = None,
        output_value: object = None,
        attributes: object = None,
        tokens: tuple[int | None, int | None] = (None, None),
    ) -> str:
        span_id = f"{self.run_id}:s{len(self.spans) + 1}"
        self.spans.append(
            ReviewSpan(
                id=span_id,
                run_id=self.run_id,
                parent_span_id=parent,
                name=name,
                span_type=span_type,
                status=status,
                start_time_ms=start,
                end_time_ms=(
                    start + duration if start is not None and duration is not None else None
                ),
                duration_ms=duration,
                model=model,
                input_tokens=tokens[0],
                output_tokens=tokens[1],
                input_payload=payload(input_value),
                output_payload=payload(output_value),
                attributes=payload(attributes),
            )
        )
        return span_id

    def span_id(self, name: str) -> str | None:
        return next((span.id for span in self.spans if span.name == name), None)
