"""Shadow audit report for the rubric question set: coverage and input sufficiency (plan H3).

Definitions (all counts come with their denominators):

- *request*: one Jev question batch for one symbol and one required input.
- *asked*: requests the policy wanted answered this checkpoint, i.e. not
  carried forward; ``missing``-input requests count as asked, because the
  template, not Jev, failed them (evaluate-jev rubric R1/R5).
- *input sufficiency*: sufficient asked requests / asked requests.
- *question coverage*: symbol-criterion pairs with an answer (asked or
  carried) / applicable pairs.
- *symbol coverage*: symbols whose every request is settled / symbols.
- *expected counts*: ΣP per applicable criterion; ``null`` for a criterion
  with any unsettled request (never a partial sum), with *measured criteria*
  as the count of non-null criteria / applicable criteria.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from raidar.jev.audit import SufficiencyLevel
from raidar.jev.policies import PolicyMode
from raidar.jev.protocol import JEV_PINNED_MODEL
from raidar.jev.rubric import Judgement, JudgementStatus, RubricCheckpoint
from raidar.jev.runtime import JevRuntime


def _ratio(numerator: int, denominator: int) -> dict[str, Any]:
    value = round(numerator / denominator, 4) if denominator else None
    return {"numerator": numerator, "denominator": denominator, "value": value}


def _asked(judgement: Judgement) -> bool:
    return judgement.status not in {JudgementStatus.CARRIED, JudgementStatus.SKIPPED}


def _symbol_coverage(judgements: Sequence[Judgement]) -> dict[str, Any]:
    settled: dict[tuple[str, str], bool] = {}
    for item in judgements:
        settled[item.symbol.identity] = settled.get(item.symbol.identity, True) and item.settled
    return _ratio(sum(settled.values()), len(settled))


def _failure_reasons(judgements: Sequence[Judgement]) -> dict[str, int]:
    reasons = Counter(item.failure_reason for item in judgements if item.failure_reason)
    return dict(sorted(reasons.items()))


def _counts(judgements: Sequence[Judgement]) -> dict[str, Any]:
    statuses = Counter(item.status.value for item in judgements)
    asked = [item for item in judgements if _asked(item)]
    sufficiency = Counter(item.sufficiency.value for item in asked if item.sufficiency)
    pairs = sum(len(item.criteria) for item in judgements)
    settled_pairs = sum(len(item.criteria) for item in judgements if item.settled)
    return {
        "requests": dict(sorted(statuses.items())),
        "asked": len(asked),
        "sufficiency": {level.value: sufficiency.get(level.value, 0) for level in SufficiencyLevel},
        "input_sufficiency": _ratio(
            sufficiency.get(SufficiencyLevel.SUFFICIENT.value, 0), len(asked)
        ),
        "question_coverage": _ratio(settled_pairs, pairs),
        "symbol_coverage": _symbol_coverage(judgements),
        "failure_reasons": _failure_reasons(judgements),
    }


def _expected(expected: dict[str, float | None]) -> dict[str, Any]:
    measured = {name: round(value, 6) for name, value in expected.items() if value is not None}
    return {
        "measured_criteria": _ratio(len(measured), len(expected)),
        "expected_counts": {name: measured.get(name) for name in expected},
    }


def _checkpoint(result: RubricCheckpoint) -> dict[str, Any]:
    judgements = result.judgements
    carried = {
        item.symbol.identity for item in judgements if item.status is JudgementStatus.CARRIED
    }
    asked = {item.symbol.identity for item in judgements if _asked(item)}
    changed = {item.symbol.identity for item in judgements if not item.span_unchanged}
    return {
        "checkpoint": result.checkpoint,
        "python_files": result.python_files,
        "symbols": result.symbols,
        "symbols_changed": len(changed),
        "requests_carry_eligible": sum(item.carry_eligible for item in judgements),
        "symbols_fully_carried": len(carried - asked),
        "parse_errors": list(result.parse_errors),
        "unsupported_files": list(result.unsupported_files),
        **_counts(judgements),
        "complete": result.complete,
        **_expected(result.expected_counts()),
    }


def rubric_shadow_report(
    results: Sequence[RubricCheckpoint], runtime: JevRuntime, *, policy_id: str
) -> dict[str, Any]:
    policy = runtime.policies.get(policy_id)
    everything = [item for result in results for item in result.judgements]
    totals = runtime.totals
    return {
        "policy": policy.ref,
        "mode": policy.mode.value,
        "counts_toward_verdicts": policy.mode is PolicyMode.ENFORCE,
        "question_set": policy.question_set,
        "pinned_model": JEV_PINNED_MODEL,
        "live_client": runtime.has_client,
        "checkpoints": [_checkpoint(result) for result in results],
        "totals": {
            **_counts(everything),
            "decisions_audited": totals.decisions,
            "provider_calls": totals.provider_calls,
            "cache_hits": totals.cache_hits,
            "cost_usd": round(totals.cost_usd, 6),
        },
    }
