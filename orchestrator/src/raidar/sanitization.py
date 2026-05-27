"""Shared sanitization helpers for persisted evidence."""

from __future__ import annotations

import json
import re
from typing import Any

SECRET_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(r"\b(?:sk|pk|rk|xox[abprs])-[A-Za-z0-9_-]{12,}\b"),
    re.compile(
        r"\b(?:api[_-]?key|token|secret|password|passwd|pwd)\s*[:=]\s*['\"]?[^'\"\s,;]+",
        re.IGNORECASE,
    ),
)
SECRET_ENV_ASSIGNMENT_PATTERN = re.compile(
    r"(?P<prefix>['\"]?\b[A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD)\b"
    r"['\"]?\s*[:=]\s*['\"]?)(?P<value>[^'\"\s,;}]+)",
    re.IGNORECASE,
)


def sanitize_evidence_text(text: str, *, max_chars: int = 180) -> str:
    """Return bounded evidence text without secret-shaped values."""

    sanitized = re.sub(r"\s+", " ", text).strip()
    sanitized = _redact_secret_values(sanitized)
    if len(sanitized) > max_chars:
        sanitized = sanitized[: max_chars - 3] + "..."
    return sanitized


def sanitize_persisted_text(text: str, *, max_chars: int = 4000) -> str:
    """Return persisted evidence text with whitespace intact and secrets redacted."""

    sanitized = text.strip()
    sanitized = _redact_secret_values(sanitized)
    if len(sanitized) > max_chars:
        sanitized = sanitized[: max_chars - 3] + "..."
    return sanitized


def sanitize_evidence_payload(value: Any, *, max_chars: int = 180) -> Any:
    """Recursively sanitize secret-shaped strings in JSON-compatible values."""

    if isinstance(value, str):
        return sanitize_evidence_text(value, max_chars=max_chars)
    if isinstance(value, list):
        return [sanitize_evidence_payload(item, max_chars=max_chars) for item in value]
    if isinstance(value, dict):
        return {
            key: sanitize_evidence_payload(item, max_chars=max_chars) for key, item in value.items()
        }
    return value


def sanitized_model_dump_json(value: Any, *, indent: int = 2, max_chars: int = 4000) -> str:
    """Serialize a model-like value after recursively sanitizing evidence strings."""

    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
    elif hasattr(value, "model_dump_json"):
        payload = json.loads(value.model_dump_json(indent=indent))
    else:
        payload = value
    return json.dumps(sanitize_evidence_payload(payload, max_chars=max_chars), indent=indent)


def _masked_secret(value: str) -> str:
    if len(value) <= 12:
        return "<redacted>"
    return f"{value[:4]}...{value[-4:]}"


def _redact_secret_values(text: str) -> str:
    redacted = text
    for pattern in SECRET_VALUE_PATTERNS:
        redacted = pattern.sub(lambda match: _masked_secret(match.group(0)), redacted)
    return SECRET_ENV_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group('prefix')}<redacted>",
        redacted,
    )
