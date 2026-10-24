"""Content-addressed Assessment store (plan F2, design memo §3.3).

An Assessment is ``scorer@version × run inputs → validated AssessResult``,
stored at a key ``hash(scorer identity, config, tool lock, input hashes)``.
The store is add-only:

- a new key is written once, atomically;
- writing an existing key succeeds only when the bytes are identical (a
  deterministic replay), otherwise it raises ``AssessmentConflictError``;
- nothing is ever overwritten or deleted, and job directories are never
  touched (the store lives outside them).

Replay-varying facts (duration) go to an append-only ``log.jsonl`` beside
the objects, never into an Assessment.

Raw payloads (a scorer's unparsed tool output, kept for audit) live in a
content-addressed ``raw/`` slot beside the objects; an Assessment names them
by sha256 in ``raw_payloads``, so a replay proves their bytes too.

Whether an offline answer exists (a deferred step's coverage run, a pre-fix grade)
is not in the key, because analysis must find one Assessment per key. Instead an
Assessment made while some answer had not been produced yet is stored as
``provisional``, with one fixed reason for every such gap, so its bytes do not
depend on the environment. A provisional Assessment is the one exception to
add-only:

- a rescore runs it again rather than reusing it;
- a different, complete Assessment for the same key supersedes it (the old
  bytes are kept under ``superseded/``, never deleted);
- a different provisional one is still a conflict, as is any change to a
  complete Assessment.

Assessments without raw payloads or a provisional mark serialise without those
fields.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterator, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator

from raidar.scorers.sdk.manifest import SdkModel
from raidar.scorers.sdk.result import AssessResult, RunStats
from raidar.spec.canonical import canonical_json_bytes, digest_json, pretty_canonical_json_bytes

OBJECTS_DIR = "assessments"
RAW_DIR = "raw"
SUPERSEDED_DIR = "superseded"
LOG_FILE = "log.jsonl"
_SHA256 = r"^[0-9a-f]{64}$"
RAW_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._/-]*$"


class AssessmentConflictError(RuntimeError):
    """A replay produced different bytes for an existing key (nondeterministic scorer)."""


class PutOutcome(StrEnum):
    ADDED = "added"
    IDENTICAL = "identical"
    SUPERSEDED = "superseded"


class ScorerIdentity(SdkModel):
    ref: str = Field(min_length=1)
    manifest_digest: str = Field(min_length=1)
    implementation_digest: str | None


class Assessment(SdkModel):
    kind: Literal["raidar-assessment"] = "raidar-assessment"
    schema_version: Literal[1] = 1
    key: str = Field(pattern=_SHA256)
    scorer: ScorerIdentity
    config: dict[str, Any]
    tool_lock: dict[str, Any]
    inputs: dict[str, Any]
    result: AssessResult
    # name -> sha256 of a payload in the store's raw/ slot
    raw_payloads: dict[str, Annotated[str, Field(pattern=_SHA256)]] = Field(default_factory=dict)
    # Some external decision had no answer yet; a complete Assessment may supersede it.
    provisional: bool = False

    @field_validator("raw_payloads")
    @classmethod
    def _raw_names(cls, value: dict[str, str]) -> dict[str, str]:
        bad = sorted(name for name in value if not re.fullmatch(RAW_NAME_PATTERN, name))
        if bad:
            raise ValueError(f"raw payload names must match {RAW_NAME_PATTERN}: {bad}")
        return value

    def render(self) -> bytes:
        dumped = self.model_dump(mode="json")
        if not self.raw_payloads:
            dumped.pop("raw_payloads")
        if not self.provisional:
            dumped.pop("provisional")
        return pretty_canonical_json_bytes(dumped)


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

    def raw_path(self, digest: str) -> Path:
        return self.root / RAW_DIR / digest[:2] / digest

    def put_raw(self, data: bytes) -> str:
        """Store a raw payload once, content-addressed; returns its sha256."""
        digest = hashlib.sha256(data).hexdigest()
        path = self.raw_path(digest)
        if path.is_file():
            return digest
        self._write_new(path, data)
        return digest

    def get_raw(self, digest: str) -> bytes | None:
        path = self.raw_path(digest)
        return path.read_bytes() if path.is_file() else None

    def stored_keys(self) -> Iterator[str]:
        for path in sorted((self.root / OBJECTS_DIR).glob("*/*.json")):
            yield path.stem

    def superseded_path(self, key: str, data: bytes) -> Path:
        digest = hashlib.sha256(data).hexdigest()
        return self.root / SUPERSEDED_DIR / key[:2] / f"{key}.{digest}.json"

    def put(self, assessment: Assessment) -> PutOutcome:
        data = assessment.render()
        path = self.path_for(assessment.key)
        if path.is_file():
            stored = path.read_bytes()
            if stored == data:
                return PutOutcome.IDENTICAL
            if Assessment.model_validate_json(stored).provisional and not assessment.provisional:
                self._supersede(path, stored, data, assessment.key)
                return PutOutcome.SUPERSEDED
            raise AssessmentConflictError(
                f"assessment {assessment.key} ({assessment.scorer.ref}) replayed with different "
                f"bytes; stored copy kept at {path}"
            )
        if not self._write_new(path, data):
            return self.put(assessment)
        return PutOutcome.ADDED

    def _supersede(self, path: Path, stored: bytes, data: bytes, key: str) -> None:
        """Keep the provisional bytes under ``superseded/``, then swap in the complete ones."""
        self._write_new(self.superseded_path(key, stored), stored)
        temporary = self._temporary(path.parent, data)
        try:
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _temporary(directory: Path, data: bytes) -> Path:
        """A sibling temp file holding ``data``; removed again if the write fails."""
        with tempfile.NamedTemporaryFile(dir=directory, delete=False, suffix=".tmp") as handle:
            temporary = Path(handle.name)
            try:
                handle.write(data)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        return temporary

    @classmethod
    def _write_new(cls, path: Path, data: bytes) -> bool:
        """Atomically create ``path``; False when another writer created it first."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cls._temporary(path.parent, data)
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        finally:
            temporary.unlink()
        return True

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
