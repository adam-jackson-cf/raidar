"""Content-addressed Jev decision cache (design memo §3.5, plan H1).

A decision is keyed on ``hash(state, questions, resolved_model)``. Only
decisions from the pinned model are ever accepted, so a lookup uses the pin as
the resolved model: a pin change therefore misses every earlier entry instead
of replaying another model's answers. The cached ``state`` is the redacted,
clipped state that was actually sent.

Entries are written once. Jev is not guaranteed to be deterministic, so the
first stored answer is authoritative: a later write for an existing key keeps
the stored entry. Replaying from the cache makes no provider call and costs $0;
the original call's cost stays on the entry as ``original_cost_usd``.

An entry is re-checked on every read: it must parse, name the key it is stored
under, carry the pinned resolved model and hold well-formed answers. Anything
else raises ``CacheInvalidError``; the runtime audits that as a failed
decision rather than replaying it or silently re-asking.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from raidar.design.experiments.canonical import digest_json, pretty_canonical_json_bytes
from raidar.scoring.judge.client import JevSuccess
from raidar.scoring.judge.protocol import (
    JEV_PINNED_MODEL,
    JevQuestions,
    answers_wire,
    parse_answers,
    questions_wire,
)

CACHE_DIR = "decisions"


def decision_key(state: Any, questions: JevQuestions, resolved_model: str) -> str:
    return digest_json(
        {"state": state, "questions": questions_wire(questions), "resolved_model": resolved_model}
    )


class CacheInvalidError(ValueError):
    """A stored entry is unreadable, misfiled or not from the pinned model."""


class CachedDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = "raidar-jev-decision"
    schema_version: int = 1
    key: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_model: str
    provider_response_id: str
    answers: dict[str, Any]
    original_cost_usd: float = Field(ge=0.0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)

    def replay(self) -> JevSuccess:
        """The cached decision as a zero-cost, zero-latency success."""
        return JevSuccess(
            answers=parse_answers(self.answers),
            resolved_model=self.resolved_model,
            provider_response_id=self.provider_response_id,
            cost_usd=0.0,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            latency_ms=0,
        )


class DecisionCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, key: str) -> Path:
        return self.root / CACHE_DIR / key[:2] / f"{key}.json"

    def get(self, key: str) -> CachedDecision | None:
        """The entry stored under ``key``, None when absent; raises ``CacheInvalidError``."""
        path = self.path_for(key)
        if not path.is_file():
            return None
        try:
            entry = CachedDecision.model_validate_json(path.read_bytes())
            parse_answers(entry.answers)
        except (OSError, ValidationError) as error:
            raise CacheInvalidError(f"cache entry {key} is unreadable") from error
        if entry.key != key:
            raise CacheInvalidError(f"cache entry {key} is filed under the wrong key")
        if entry.resolved_model != JEV_PINNED_MODEL:
            raise CacheInvalidError(f"cache entry {key} is not from the pinned Jev model")
        return entry

    def put(self, key: str, success: JevSuccess) -> CachedDecision:
        """Store ``success`` under ``key`` unless an entry exists; return the stored entry."""
        existing = self.get(key)
        if existing is not None:
            return existing
        entry = CachedDecision(
            key=key,
            resolved_model=success.resolved_model,
            provider_response_id=success.provider_response_id,
            answers=answers_wire(success.answers),
            original_cost_usd=success.cost_usd,
            input_tokens=success.input_tokens,
            output_tokens=success.output_tokens,
        )
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = pretty_canonical_json_bytes(entry.model_dump(mode="json"))
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix=".tmp") as handle:
            handle.write(data)
            temporary = Path(handle.name)
        try:
            os.link(temporary, path)
        except FileExistsError:
            stored = self.get(key)
            if stored is None:
                raise
            return stored
        finally:
            temporary.unlink(missing_ok=True)
        return entry
