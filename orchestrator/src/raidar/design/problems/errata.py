"""Versioned problem errata: ``registry/<dataset>/<problem>/<version>.errata.yaml``.

An errata lists verifier cases of one problem version whose assertions the
specification does not determine (``raidar.design.problems.determinacy``), each with
a disposition:

- ``exclude-from-solved``: the case leaves the errata-applied solved verdict
  (``raidar.grading.errata``); its raw outcome is still graded and reported;
- ``informational``: recorded only; no verdict changes.

The file is pre-registered: it is committed, it names the manifest it is for
(``manifest_sha256``), and the lock pins the sha256 of its bytes in the problem
reference's metadata, which is part of the execution identity. Tests are never
patched: the raw SCB grade is computed and kept exactly as before.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from raidar.grading.errata import ErrataRef

ERRATA_SUFFIX = ".errata.yaml"
_CASE_ID = r"^[^:\s]+\.py::\S+$"


class ErrataError(ValueError):
    """An errata file is unreadable, invalid or for another manifest."""


class Disposition(StrEnum):
    EXCLUDE_FROM_SOLVED = "exclude-from-solved"
    INFORMATIONAL = "informational"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ErrataCase(_Model):
    """One verifier case. ``id`` is the case id the verifier records (``<file>::<test>``)."""

    id: str = Field(pattern=_CASE_ID)
    checkpoint_origin: str = Field(min_length=1)
    disposition: Disposition
    rule: str = Field(min_length=1)
    """The determinacy-audit rule that flagged it (``raidar.design.problems.determinacy``)."""
    assertion: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    evidence: tuple[str, ...] = Field(min_length=1)


class ProblemErrata(_Model):
    kind: Literal["raidar-problem-errata"] = "raidar-problem-errata"
    schema_version: Literal[1] = 1
    problem: str = Field(min_length=1)
    manifest_sha256: str = Field(min_length=64, max_length=64)
    registered: str = Field(min_length=1)
    """ISO date the errata was committed, before any experiment that uses it ran."""
    basis: str = Field(min_length=1)
    cases: tuple[ErrataCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_cases(self) -> ProblemErrata:
        ids = [case.id for case in self.cases]
        duplicates = sorted({case for case in ids if ids.count(case) > 1})
        if duplicates:
            raise ValueError(f"duplicate errata case ids: {duplicates}")
        return self

    def ids(self, disposition: Disposition) -> tuple[str, ...]:
        return tuple(sorted(case.id for case in self.cases if case.disposition is disposition))


class LoadedErrata:
    """An errata file and the sha256 of its exact bytes (what the lock pins)."""

    def __init__(self, path: Path, errata: ProblemErrata, sha256: str) -> None:
        self.path = path
        self.errata = errata
        self.sha256 = sha256

    def ref(self) -> ErrataRef:
        """What the verifier and the correctness scorer apply."""
        return ErrataRef(
            sha256=self.sha256,
            exclude_from_solved=self.errata.ids(Disposition.EXCLUDE_FROM_SOLVED),
        )

    def lock_metadata(self) -> dict[str, object]:
        return {
            "sha256": self.sha256,
            "exclude_from_solved": list(self.errata.ids(Disposition.EXCLUDE_FROM_SOLVED)),
            "informational": list(self.errata.ids(Disposition.INFORMATIONAL)),
        }


def errata_ref_from_metadata(metadata: object) -> ErrataRef | None:
    """The ``ErrataRef`` a lock's problem metadata pins, or None when it pins no errata."""
    if not isinstance(metadata, dict) or not isinstance(metadata.get("errata"), dict):
        return None
    pinned = metadata["errata"]
    return ErrataRef(
        sha256=str(pinned["sha256"]),
        exclude_from_solved=tuple(str(case) for case in pinned.get("exclude_from_solved", ())),
    )


DRAFT_REASON_PREFIX = "TODO"
"""Starts the placeholder reason of a drafted case; a committed errata may not carry it."""


def load_errata(path: Path, *, problem: str, manifest_sha256: str) -> LoadedErrata | None:
    """The errata at ``path`` (None when there is none), refused unless it is for this manifest."""
    if not path.is_file():
        return None
    raw = path.read_bytes()
    try:
        errata = ProblemErrata.model_validate(yaml.safe_load(raw) or {})
    except (yaml.YAMLError, ValidationError) as exc:
        raise ErrataError(f"invalid errata {path}: {exc}") from exc
    if errata.problem != problem:
        raise ErrataError(f"{path} is the errata of {errata.problem}, not {problem}")
    if errata.manifest_sha256 != manifest_sha256:
        raise ErrataError(
            f"{path} is for manifest {errata.manifest_sha256[:12]}, not {manifest_sha256[:12]}; "
            "re-audit the problem and re-register its errata"
        )
    drafted = sorted(
        case.id for case in errata.cases if case.reason.startswith(DRAFT_REASON_PREFIX)
    )
    if drafted:
        raise ErrataError(
            f"{path} still has the drafted placeholder reason for {drafted}; "
            "write the reason each case is not determined by the specification"
        )
    return LoadedErrata(path, errata, hashlib.sha256(raw).hexdigest())


def report_entries(value: object) -> list[dict[str, Any]]:
    """The JSON objects of a report list (anything else is skipped)."""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _draft_case(entry: dict[str, Any], disposition: Disposition) -> dict[str, object]:
    assertions = report_entries(entry.get("assertions"))
    first = assertions[0] if assertions else {}
    variants = ", ".join(str(v) for v in entry.get("variants") or [])
    evidence = [f"audit: {a['rule']}: line {a['line']}: {a['why']}" for a in assertions]
    if variants:
        evidence.append(
            "audit: passes on the reference, does not pass on the spec-conformant "
            f"variant(s): {variants}"
        )
    return {
        "id": entry["case"],
        "checkpoint_origin": entry.get("checkpoint_origin", first.get("checkpoint_origin")),
        "disposition": disposition.value,
        "rule": first.get("rule", "unknown"),
        "assertion": first.get("assertion", ""),
        "reason": f"{DRAFT_REASON_PREFIX}: state why the specification does not determine "
        "the asserted value",
        "evidence": evidence or ["audit: see the validation report's spec_determinacy section"],
    }


def draft_errata(
    problem: str, manifest_sha256: str, audit: dict[str, object], *, registered: str
) -> str:
    """A YAML errata draft from a validation report's audit: flagged cases excluded from solved.

    The draft is a starting point. Its ``reason`` fields must be written by a
    person before the file is committed: ``load_errata`` refuses a file that still
    carries the placeholder reason.
    """
    flagged = report_entries(audit.get("flagged"))
    document = {
        "kind": "raidar-problem-errata",
        "schema_version": 1,
        "problem": problem,
        "manifest_sha256": manifest_sha256,
        "registered": registered,
        "basis": (
            f"spec-determinacy audit v{audit.get('audit_version')} (validation report "
            "spec_determinacy): every flagged case, by the audit rule alone"
        ),
        "cases": [_draft_case(entry, Disposition.EXCLUDE_FROM_SOLVED) for entry in flagged],
    }
    return yaml.safe_dump(document, sort_keys=False, width=100, allow_unicode=True)
