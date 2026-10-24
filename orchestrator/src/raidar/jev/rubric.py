"""SCB rubric criteria asked per changed symbol, with carry-forward (plan H3).

For each checkpoint, in order:

1. The production Python files of the snapshot come from the assessor-owned
   inventory (``raidar.scorers.inventory``), so agent-written exclusions
   cannot hide code. Production files in other languages are listed as
   unsupported, never dropped silently.
2. Each symbol's applicable criteria are grouped by the state they require
   and split into requests of at most ``max_questions_per_request``.
3. A request is carried forward from the prior checkpoint, with no Jev call,
   when the symbol's span is unchanged under the SCB fork's diff-hunk rules
   (``raidar.jev.carry_forward``) and the prior answer settled. As in the fork
   and design memo §3.5, only symbols changed since k-1 are asked: a change
   elsewhere in the file or in the cumulative spec does not re-ask an
   unchanged symbol. A carry is audited as a deterministic decision with rule
   ``carry_forward:<origin checkpoint>``, the carried answers and the origin
   request id, so every ΣP can be rebuilt from the audit alone.
4. Anything else is asked through ``JevRuntime`` under the rubric policy.

``expected_counts`` gives the ΣP expected smell count per criterion (design
memo §3.4). A criterion's count is a number only when every request that
applies it at the checkpoint settled; otherwise it is ``None`` (unmeasured),
never a partial sum. Codebase-scope criteria are asked with a bounded
whole-codebase state built once per checkpoint (``raidar.jev.codebase``): the
module, class and function inventory, the inheritance and interface graph,
and name-matched call and access sites of the symbol. The whole state is held
under Jev's 96k-character cap; whatever is clipped makes the input ``partial``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from raidar.jev.audit import DecisionStage, InputSufficiency, SufficiencyLevel
from raidar.jev.carry_forward import (
    DiffHunk,
    is_span_unchanged,
    parse_diff_hunks,
    unified_diff_text,
)
from raidar.jev.client import state_text
from raidar.jev.codebase import CodebaseIndex
from raidar.jev.protocol import JEV_STATE_CHAR_LIMIT, JevAnswers, NoulAnswer
from raidar.jev.redaction import Clipped, clip
from raidar.jev.rubric_questions import Criterion, Requirement, RubricQuestionSet
from raidar.jev.runtime import RULE_INPUT_MISSING, DecisionOutcome, JevRuntime
from raidar.jev.symbols import Symbol, SymbolParseError, extract_symbols
from raidar.scorers.inventory import FileRole, InventoryPolicy, build_inventory

RUBRIC_POLICY = "code-quality.rubric-smells"
RULE_CARRY_FORWARD = "carry_forward"
_MIN_CODEBASE_CHARS = 2_000
_CAP_MARGIN = 1_000
_TASK = (
    "Judge one Python symbol from an agent-written program against one code-quality "
    "criterion per question. Judge only the code in symbol.source; other fields are context."
)


class JudgementStatus(StrEnum):
    ANSWERED = "answered"
    CARRIED = "carried"
    INSUFFICIENT = "insufficient"
    ERROR = "error"
    SKIPPED = "skipped"


_SETTLED = frozenset({JudgementStatus.ANSWERED, JudgementStatus.CARRIED})


@dataclass(frozen=True)
class CheckpointSource:
    """One checkpoint's snapshot and, when known, its cumulative specification."""

    name: str
    root: Path
    spec: str | None = None


@dataclass(frozen=True)
class Judgement:
    checkpoint: str
    symbol: Symbol
    requires: Requirement
    criteria: tuple[str, ...]
    status: JudgementStatus
    request_id: str
    sufficiency: SufficiencyLevel | None
    answers: Mapping[str, float] | None = None
    carried_from: str | None = None
    origin_request_id: str | None = None
    span_unchanged: bool = False
    carry_eligible: bool = False
    cost_usd: float = 0.0
    cache_hit: bool = False
    failure_reason: str | None = None

    @property
    def settled(self) -> bool:
        return self.status in _SETTLED


@dataclass(frozen=True)
class RubricCheckpoint:
    checkpoint: str
    judgements: tuple[Judgement, ...]
    symbols: int
    python_files: int
    parse_errors: tuple[str, ...] = ()
    unsupported_files: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        """Every applicable request of every criterion settled."""
        return all(value is not None for value in self.expected_counts().values())

    def expected_counts(self) -> dict[str, float | None]:
        """ΣP per applicable criterion; ``None`` unless all its requests settled.

        A parse error or an unsupported production file leaves code unjudged,
        so then no criterion is measured.
        """
        whole = not self.parse_errors and not self.unsupported_files
        totals: dict[str, float | None] = {}
        for judgement in self.judgements:
            for name in judgement.criteria:
                value = totals.get(name, 0.0)
                answer = (judgement.answers or {}).get(name)
                totals[name] = None if value is None or answer is None else value + answer
        return {name: value if whole else None for name, value in sorted(totals.items())}


@dataclass
class _Prior:
    checkpoint: str | None = None
    files: dict[str, str] = field(default_factory=dict)
    symbols: dict[tuple[str, str], Symbol] = field(default_factory=dict)
    judgements: dict[tuple[str, str, str, tuple[str, ...]], Judgement] = field(default_factory=dict)


@dataclass(frozen=True)
class _Snapshot:
    files: dict[str, str]
    unsupported: tuple[str, ...]


def _read_snapshot(root: Path, policy: InventoryPolicy | None) -> _Snapshot:
    inventory = build_inventory(root, policy)
    production = inventory.with_role(FileRole.PRODUCTION)
    files = {
        item.path: (root / item.path).read_text(encoding="utf-8")
        for item in production
        if item.path.endswith(".py")
    }
    unsupported = tuple(item.path for item in production if not item.path.endswith(".py"))
    return _Snapshot(files, unsupported)


def _chunks(
    question_set: RubricQuestionSet, symbol: Symbol
) -> list[tuple[Requirement, tuple[Criterion, ...]]]:
    grouped: dict[Requirement, list[Criterion]] = {}
    for criterion in question_set.applicable(symbol.kind):
        grouped.setdefault(criterion.requires, []).append(criterion)
    size = question_set.max_questions_per_request
    return [
        (requirement, tuple(items[start : start + size]))
        for requirement in Requirement
        for items in [grouped.get(requirement, [])]
        for start in range(0, len(items), size)
    ]


@dataclass(frozen=True)
class _Inputs:
    """What one symbol's requests can draw on beyond its own source."""

    file_text: str
    spec: str | None
    codebase: CodebaseIndex | None


def _context(
    question_set: RubricQuestionSet,
    symbol: Symbol,
    requirement: Requirement,
    inputs: _Inputs,
    budget: int,
) -> dict[str, Clipped] | None:
    """The clipped extra inputs a requirement needs, or None when one is unavailable."""
    if requirement is Requirement.FILE:
        return {"file": clip(inputs.file_text, question_set.file_chars)}
    if requirement is Requirement.SPEC:
        return None if inputs.spec is None else {"spec": clip(inputs.spec, question_set.spec_chars)}
    if requirement is Requirement.CODEBASE:
        if inputs.codebase is None:
            return None
        sections = inputs.codebase.state(symbol, budget).sections
        return {f"codebase.{name}": text for name, text in sections.items()}
    return {}


def _place(state: dict[str, Any], symbol: Symbol, name: str, text: str) -> None:
    if name == "file":
        state["file"] = {"path": symbol.path, "source": text}
    elif name.startswith("codebase."):
        state.setdefault("codebase", {})[name.split(".", 1)[1]] = text
    else:
        state[name] = text


def _fit_codebase(
    question_set: RubricQuestionSet,
    symbol: Symbol,
    inputs: _Inputs,
    state: dict[str, Any],
    context: dict[str, Clipped],
) -> dict[str, Clipped]:
    """Shrink the codebase share until the state fits Jev's cap (JSON escaping adds length)."""
    budget = question_set.codebase_chars
    overflow = len(state_text(state)) - JEV_STATE_CHAR_LIMIT
    while overflow > 0 and budget > _MIN_CODEBASE_CHARS:
        budget = max(_MIN_CODEBASE_CHARS, budget - overflow - _CAP_MARGIN)
        context = _context(question_set, symbol, Requirement.CODEBASE, inputs, budget) or {}
        state.pop("codebase", None)
        for name, text in context.items():
            _place(state, symbol, name, text.text)
        overflow = len(state_text(state)) - JEV_STATE_CHAR_LIMIT
    return context


def _state(
    question_set: RubricQuestionSet,
    symbol: Symbol,
    requirement: Requirement,
    inputs: _Inputs,
) -> tuple[dict[str, Any], InputSufficiency]:
    source = clip(symbol.source, question_set.symbol_chars)
    state: dict[str, Any] = {
        "task": _TASK,
        "symbol": {
            "path": symbol.path,
            "qualname": symbol.qualname,
            "kind": symbol.kind.value,
            "source": source.text,
        },
    }
    context = _context(question_set, symbol, requirement, inputs, question_set.codebase_chars)
    if context is None:
        missing = InputSufficiency(level=SufficiencyLevel.MISSING, missing=(requirement.value,))
        return state, missing
    for name, text in context.items():
        _place(state, symbol, name, text.text)
    if requirement is Requirement.CODEBASE:
        context = _fit_codebase(question_set, symbol, inputs, state, context)
    parts = {"symbol": source, **context}
    clipped = tuple(name for name, text in parts.items() if text.clipped)
    level = SufficiencyLevel.PARTIAL if clipped else SufficiencyLevel.SUFFICIENT
    return state, InputSufficiency(level=level, clipped=clipped)


def _status(outcome: DecisionOutcome) -> JudgementStatus:
    if outcome.ok:
        return JudgementStatus.ANSWERED
    record = outcome.record
    if record.stage is DecisionStage.JEV_ERROR:
        return JudgementStatus.ERROR
    if record.rule == RULE_INPUT_MISSING:
        return JudgementStatus.INSUFFICIENT
    return JudgementStatus.SKIPPED


def _noul_answers(outcome: DecisionOutcome, names: tuple[str, ...]) -> dict[str, float]:
    answers = {}
    for name in names:
        value = outcome.noul(name)
        if value is None:
            raise ValueError(f"validated Jev decision lacks a noul answer for {name}")
        answers[name] = value
    return answers


def _as_noul_answers(answers: Mapping[str, float] | None) -> JevAnswers | None:
    if answers is None:
        return None
    return {name: NoulAnswer(type="noul", noul=value) for name, value in answers.items()}


class RubricRunner:
    """Runs the rubric question set over a trajectory's checkpoints, in order."""

    def __init__(
        self,
        runtime: JevRuntime,
        question_set: RubricQuestionSet,
        *,
        policy_id: str = RUBRIC_POLICY,
        inventory_policy: InventoryPolicy | None = None,
    ) -> None:
        self.runtime = runtime
        self.question_set = question_set
        self.policy_id = policy_id
        self.inventory_policy = inventory_policy
        self._asks_codebase = any(
            criterion.requires is Requirement.CODEBASE for criterion in question_set.criteria
        )

    def run(self, checkpoints: Sequence[CheckpointSource]) -> list[RubricCheckpoint]:
        prior = _Prior()
        results = []
        for source in checkpoints:
            result, prior = self._checkpoint(source, prior)
            results.append(result)
        return results

    def _checkpoint(
        self, source: CheckpointSource, prior: _Prior
    ) -> tuple[RubricCheckpoint, _Prior]:
        snapshot = _read_snapshot(source.root, self.inventory_policy)
        current = _Prior(checkpoint=source.name, files=snapshot.files)
        codebase = CodebaseIndex(snapshot.files) if self._asks_codebase else None
        judgements: list[Judgement] = []
        errors = []
        for path, text in sorted(snapshot.files.items()):
            try:
                symbols = extract_symbols(path, text)
            except SymbolParseError as error:
                errors.append(str(error))
                continue
            hunks = parse_diff_hunks(unified_diff_text(prior.files.get(path, ""), text, path))
            for symbol in symbols:
                current.symbols[symbol.identity] = symbol
                unchanged = self._unchanged(symbol, prior, hunks, path in prior.files)
                for requirement, criteria in _chunks(self.question_set, symbol):
                    judgement = self._judge(
                        source,
                        symbol,
                        requirement,
                        criteria,
                        _Inputs(text, source.spec, codebase),
                        prior if unchanged else None,
                    )
                    judgement = replace(judgement, span_unchanged=unchanged)
                    key = (*symbol.identity, requirement.value, judgement.criteria)
                    current.judgements[key] = judgement
                    judgements.append(judgement)
        result = RubricCheckpoint(
            checkpoint=source.name,
            judgements=tuple(judgements),
            symbols=len(current.symbols),
            python_files=len(snapshot.files),
            parse_errors=tuple(errors),
            unsupported_files=snapshot.unsupported,
        )
        return result, current

    @staticmethod
    def _unchanged(symbol: Symbol, prior: _Prior, hunks: list[DiffHunk], existed: bool) -> bool:
        before = prior.symbols.get(symbol.identity)
        if before is None or not existed or before.sha256 != symbol.sha256:
            return False
        unchanged, new_start, _ = is_span_unchanged(hunks, before.start, before.end)
        return unchanged and new_start == symbol.start

    def _judge(
        self,
        source: CheckpointSource,
        symbol: Symbol,
        requirement: Requirement,
        criteria: tuple[Criterion, ...],
        inputs: _Inputs,
        prior: _Prior | None,
    ) -> Judgement:
        names = tuple(criterion.name for criterion in criteria)
        subject = f"{source.name}:{symbol.path}::{symbol.qualname}[{requirement.value}]"
        earlier = None
        if prior is not None:
            earlier = prior.judgements.get((*symbol.identity, requirement.value, names))
        eligible = earlier is not None
        if earlier is not None and earlier.settled:
            origin = earlier.carried_from or earlier.checkpoint
            origin_request = earlier.origin_request_id or earlier.request_id
            record = self.runtime.record_deterministic(
                self.policy_id,
                subject=subject,
                rule=f"{RULE_CARRY_FORWARD}:{origin}",
                verdict="carried",
                answers=_as_noul_answers(earlier.answers),
                carried_from=origin_request,
            )
            return Judgement(
                checkpoint=source.name,
                symbol=symbol,
                requires=requirement,
                criteria=names,
                status=JudgementStatus.CARRIED,
                request_id=record.request_id,
                sufficiency=earlier.sufficiency,
                answers=earlier.answers,
                carried_from=origin,
                origin_request_id=origin_request,
                carry_eligible=True,
            )
        state, sufficiency = _state(self.question_set, symbol, requirement, inputs)
        outcome = self.runtime.decide(
            self.policy_id,
            subject=subject,
            state=state,
            questions={criterion.name: criterion.question() for criterion in criteria},
            sufficiency=sufficiency,
        )
        answers = _noul_answers(outcome, names) if outcome.ok else None
        return Judgement(
            checkpoint=source.name,
            symbol=symbol,
            requires=requirement,
            criteria=names,
            status=_status(outcome),
            request_id=outcome.record.request_id,
            sufficiency=sufficiency.level,
            answers=answers,
            carry_eligible=eligible,
            cost_usd=outcome.record.cost_usd,
            cache_hit=outcome.record.cache_hit,
            failure_reason=outcome.record.failure_reason,
        )
