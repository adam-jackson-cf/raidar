"""Content-addressed Assessment store (plan F2, design memo §3.3).

An Assessment is ``scorer@version × run inputs → validated AssessResult``,
stored at a key ``hash(scorer identity, config, tool lock, input hashes)``.
The store is add-only:

- a new key is written once, atomically;
- writing an existing key succeeds only when the bytes are identical (a
  deterministic replay), otherwise it raises ``AssessmentConflictError``;
- nothing is ever overwritten or deleted, and run directories are never
  touched (the store lives outside them).

Replay-varying facts (duration) go to an append-only ``log.jsonl`` beside
the objects, never into an Assessment.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from raidar.scorers.sdk.manifest import SdkModel
from raidar.scorers.sdk.result import AssessResult, RunStats
from raidar.spec.canonical import canonical_json_bytes, digest_json, pretty_canonical_json_bytes

OBJECTS_DIR = "assessments"
LOG_FILE = "log.jsonl"


class AssessmentConflictError(RuntimeError):
    """A replay produced different bytes for an existing key (nondeterministic scorer)."""


class PutOutcome(StrEnum):
    ADDED = "added"
    IDENTICAL = "identical"


class ScorerIdentity(SdkModel):
    ref: str = Field(min_length=1)
    manifest_digest: str = Field(min_length=1)
    implementation_digest: str | None


class Assessment(SdkModel):
    kind: Literal["raidar-assessment"] = "raidar-assessment"
    schema_version: Literal[1] = 1
    key: str = Field(pattern=r"^[0-9a-f]{64}$")
    scorer: ScorerIdentity
    config: dict[str, Any]
    tool_lock: dict[str, Any]
    inputs: dict[str, Any]
    result: AssessResult

    def render(self) -> bytes:
        return pretty_canonical_json_bytes(self.model_dump(mode="json"))


def assessment_key(
    scorer: ScorerIdentity,
    *,
    config: Mapping[str, Any],
    tool_lock: Mapping[str, Any],
    inputs: Mapping[str, Any],
) -> str:
    return digest_json(
        {
            "scorer": scorer.model_dump(mode="json"),
            "config": dict(config),
            "tool_lock": dict(tool_lock),
            "inputs": dict(inputs),
        }
    )


class AssessmentStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, key: str) -> Path:
        return self.root / OBJECTS_DIR / key[:2] / f"{key}.json"

    def has(self, key: str) -> bool:
        return self.path_for(key).is_file()

    def get(self, key: str) -> Assessment | None:
        path = self.path_for(key)
        if not path.is_file():
            return None
        return Assessment.model_validate_json(path.read_bytes())

    def stored_keys(self) -> Iterator[str]:
        for path in sorted((self.root / OBJECTS_DIR).glob("*/*.json")):
            yield path.stem

    def put(self, assessment: Assessment) -> PutOutcome:
        data = assessment.render()
        path = self.path_for(assessment.key)
        if path.is_file():
            if path.read_bytes() == data:
                return PutOutcome.IDENTICAL
            raise AssessmentConflictError(
                f"assessment {assessment.key} ({assessment.scorer.ref}) replayed with different "
                f"bytes; stored copy kept at {path}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix=".tmp") as handle:
            handle.write(data)
            temporary = Path(handle.name)
        try:
            os.link(temporary, path)
        except FileExistsError:
            temporary.unlink()
            return self.put(assessment)
        temporary.unlink()
        return PutOutcome.ADDED

    def log(self, *, trial: str, key: str, scorer: str, outcome: str, stats: RunStats) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        entry = {
            "trial": trial,
            "key": key,
            "scorer": scorer,
            "outcome": outcome,
            "stats": stats.model_dump(mode="json"),
        }
        with (self.root / LOG_FILE).open("ab") as handle:
            handle.write(canonical_json_bytes(entry) + b"\n")

    def read_log(self) -> list[dict[str, Any]]:
        path = self.root / LOG_FILE
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]
