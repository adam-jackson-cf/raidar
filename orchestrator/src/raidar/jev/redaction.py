"""Redaction and clipping for Jev state, ported from ``jev.ts``.

Agent snapshots and transcripts can contain credentials. Everything sent to
Jev, cached or audited passes through ``redact_value`` first, and the caller's
own API key is always removed. Long text is clipped to a head and a short tail
with a visible marker, and the clip is reported so input sufficiency can say
the state was partial.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

REDACTED = "[REDACTED]"
_TOKEN = re.compile(
    r"\b(?:sk-[\w-]{8,}|gh[pousr]_[\w-]{8,}|github_pat_[\w-]{8,}|AKIA[0-9A-Z]{16})\b"
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
)
_BEARER = re.compile(r"\b(Bearer\s+)[\w~+/-]+(?:\.[\w~+/-]+)*", re.IGNORECASE)
_JWT = re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+\b")
_ASSIGNMENT = re.compile(
    r"\b((?:[\w.-]*(?:api[_-]?key|token|secret|password|passwd|authorization))"
    r"\s*(?:[:=]|\bis\b)\s*['\"]?)[^\s'\",;]+",
    re.IGNORECASE,
)
_TAIL_CAP = 2_000


def redact(text: str, api_key: str | None = None) -> str:
    """Remove the caller's key and known credential shapes from ``text``."""
    out = text.replace(api_key, REDACTED) if api_key else text
    out = _TOKEN.sub(REDACTED, out)
    out = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", out)
    out = _BEARER.sub(rf"\g<1>{REDACTED}", out)
    out = _JWT.sub(REDACTED, out)
    return _ASSIGNMENT.sub(rf"\g<1>{REDACTED}", out)


def redact_value(value: Any, api_key: str | None = None) -> Any:
    """Redact every string inside a JSON value; keys are kept, finite numbers pass."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, list | tuple):
        return [redact_value(item, api_key) for item in value]
    if isinstance(value, dict):
        return {key: redact_value(item, api_key) for key, item in value.items()}
    if isinstance(value, int | float) and math.isfinite(value):
        return value
    return redact(str(value), api_key)


@dataclass(frozen=True)
class Clipped:
    text: str
    omitted: int

    @property
    def clipped(self) -> bool:
        return self.omitted > 0


def clip(text: str, limit: int) -> Clipped:
    """Keep the head and a short tail of ``text`` with a visible omission marker."""
    if len(text) <= limit:
        return Clipped(text, 0)
    tail = min(limit // 5, _TAIL_CAP)
    head = limit - tail
    omitted = len(text) - head - tail
    marker = f"\n…[{omitted} characters omitted]…\n"
    return Clipped(f"{text[:head]}{marker}{text[len(text) - tail :]}", omitted)
