"""The SCB rubric criteria as a Jev noul question set (plan H3, design memo §3.5).

The criteria file is the SCB fork's ``configs/rubrics/llm_judge.jsonl``,
vendored unchanged and pinned by sha256; ``scb-rubric.yaml`` adds what Jev
needs on top: which state each criterion requires, which symbol kinds it
applies to, the request size and the clip budgets. Each criterion becomes one
noul question whose id is the criterion name, so answers map back to SCB
criteria one to one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from raidar.scoring.judge.protocol import NoulCriteria, NoulQuestion
from raidar.scoring.judge.symbols import SymbolKind

QUESTION_SET_DIR = Path(__file__).parent / "question_sets"
SCB_RUBRIC_FILE = QUESTION_SET_DIR / "scb-rubric.yaml"
_DEFAULT_KINDS = (SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.MODULE)


class Requirement(StrEnum):
    SYMBOL = "symbol"
    FILE = "file"
    SPEC = "spec"
    CODEBASE = "codebase"


class QuestionSetError(ValueError):
    """The question-set definition or its pinned criteria file is invalid."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _Source(_Frozen):
    file: str
    upstream: str
    upstream_commit: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _CriterionOverlay(_Frozen):
    requires: Requirement = Requirement.SYMBOL
    kinds: tuple[SymbolKind, ...] = _DEFAULT_KINDS


class _QuestionSetFile(_Frozen):
    id: str
    version: str
    source: _Source
    max_questions_per_request: int = Field(ge=1, le=64)
    symbol_chars: int = Field(ge=1000)
    file_chars: int = Field(ge=1000)
    spec_chars: int = Field(ge=1000)
    codebase_chars: int = Field(ge=1000)
    criteria: dict[str, _CriterionOverlay] = Field(default_factory=dict)


@dataclass(frozen=True)
class Criterion:
    name: str
    category: str
    measures: str
    description: str
    positives: tuple[str, ...]
    negatives: tuple[str, ...]
    requires: Requirement
    kinds: tuple[SymbolKind, ...]

    def question(self) -> NoulQuestion:
        label = self.name.replace("_", " ")
        instructions = (
            f"Does the code in `symbol.source` exhibit the '{label}' pattern "
            f"({self.category.replace('_', ' ')})? {self.description}"
        )
        return NoulQuestion(
            instructions=instructions,
            criteria=NoulCriteria(
                true="It does, as in: " + "; ".join(self.positives),
                false="It does not; these are not the pattern: " + "; ".join(self.negatives),
            ),
        )


@dataclass(frozen=True)
class RubricQuestionSet:
    id: str
    version: str
    criteria: tuple[Criterion, ...]
    max_questions_per_request: int
    symbol_chars: int
    file_chars: int
    spec_chars: int
    codebase_chars: int
    source_sha256: str

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    def applicable(self, kind: SymbolKind) -> tuple[Criterion, ...]:
        return tuple(criterion for criterion in self.criteria if kind in criterion.kinds)


def _criterion(raw: dict[str, Any], overlay: _CriterionOverlay) -> Criterion:
    return Criterion(
        name=raw["name"],
        category=raw["category"],
        measures=raw["type"],
        description=raw["description"],
        positives=tuple(raw["positive_indicators"]),
        negatives=tuple(raw["negative_indicators"]),
        requires=overlay.requires,
        kinds=overlay.kinds,
    )


def load_rubric_question_set(path: Path = SCB_RUBRIC_FILE) -> RubricQuestionSet:
    try:
        definition = _QuestionSetFile.model_validate(yaml.safe_load(path.read_text("utf-8")))
    except (OSError, yaml.YAMLError, ValueError) as error:
        raise QuestionSetError(f"{path}: {error}") from error
    data = (path.parent / definition.source.file).read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != definition.source.sha256:
        raise QuestionSetError(
            f"{definition.source.file}: sha256 {actual} does not match the pinned "
            f"{definition.source.sha256}"
        )
    rows = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    names = [row["name"] for row in rows]
    unknown = sorted(set(definition.criteria) - set(names))
    if unknown or len(set(names)) != len(names):
        raise QuestionSetError(f"{path}: unknown or duplicate criteria {unknown or names}")
    criteria = tuple(
        _criterion(row, definition.criteria.get(row["name"], _CriterionOverlay())) for row in rows
    )
    return RubricQuestionSet(
        id=definition.id,
        version=definition.version,
        criteria=criteria,
        max_questions_per_request=definition.max_questions_per_request,
        symbol_chars=definition.symbol_chars,
        file_chars=definition.file_chars,
        spec_chars=definition.spec_chars,
        codebase_chars=definition.codebase_chars,
        source_sha256=actual,
    )
