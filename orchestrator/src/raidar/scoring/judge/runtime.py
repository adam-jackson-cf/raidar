"""Policy-bound, cached and audited Jev decisions (the ``JevClient`` of design memo §3.3).

``JevRuntime.decide`` is the single entry point scorers use. For every call,
exactly one ``DecisionRecord`` is appended to the audit, whatever happens:

1. ``off`` policy: no call, recorded as ``skipped``.
2. State missing an input the questions need (sufficiency ``missing``): no
   call, recorded as ``skipped`` with rule ``input_missing``. The only correct
   answer would be "unknown", so asking would spend money on noise.
3. Cache hit on ``hash(redacted state, questions, pinned model)``: replayed
   at $0 with no provider call. An entry that fails its re-check (corrupt,
   misfiled, another model, answers that do not fit the questions) is audited
   as a ``cache_invalid`` failure and is neither replayed nor re-asked.
4. Otherwise one provider call. With no client (no API key) the decision fails
   as ``missing_key``, or ``cache_miss`` in replay-only mode.

A decision counts toward verdicts only when its policy is ``enforce`` and the
decision succeeded (a Jev answer, or a deterministic settlement such as a
carry-forward of one). Shadow decisions are audited and observable but never
count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from raidar.scoring.judge.audit import (
    AuditLog,
    DecisionRecord,
    DecisionStage,
    InputSufficiency,
    SufficiencyLevel,
    new_request_id,
    utc_timestamp,
)
from raidar.scoring.judge.cache import CacheInvalidError, DecisionCache, decision_key
from raidar.scoring.judge.client import (
    FailureReason,
    JevClient,
    JevFailure,
    JevResult,
    JevSuccess,
    state_text,
)
from raidar.scoring.judge.policies import JevPolicy, PolicyMode, PolicyRegistry
from raidar.scoring.judge.protocol import (
    JEV_PINNED_MODEL,
    JEV_STATE_CHAR_LIMIT,
    JevAnswers,
    JevQuestions,
    NoulAnswer,
    answer_matches,
    answers_wire,
    questions_wire,
    validate_questions,
)
from raidar.scoring.judge.redaction import redact, redact_value

VERDICT_UNCALIBRATED = "uncalibrated"
VERDICT_SKIPPED = "skipped"
VERDICT_INSUFFICIENT = "insufficient_input"
VERDICT_ERROR = "error"
VERDICT_FLAG = "flag"
VERDICT_CLEAR = "clear"
RULE_INPUT_MISSING = "input_missing"


@dataclass(frozen=True)
class DecisionOutcome:
    """What a scorer reads back: the audited record and, on success, typed answers."""

    record: DecisionRecord
    answers: JevAnswers | None = None

    @property
    def ok(self) -> bool:
        return self.answers is not None

    @property
    def counts_toward_verdicts(self) -> bool:
        return self.record.counts_toward_verdicts

    def noul(self, question_id: str) -> float | None:
        answer = (self.answers or {}).get(question_id)
        return answer.noul if isinstance(answer, NoulAnswer) else None


@dataclass
class RuntimeTotals:
    decisions: int = 0
    provider_calls: int = 0
    cache_hits: int = 0
    cost_usd: float = 0.0
    failures: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class _Request:
    policy: JevPolicy
    subject: str
    state: Any
    questions: JevQuestions
    sufficiency: InputSufficiency


class JevRuntime:
    def __init__(
        self,
        policies: PolicyRegistry,
        *,
        audit: AuditLog,
        cache: DecisionCache,
        client: JevClient | None = None,
        replay_only: bool = False,
        trial: str | None = None,
    ) -> None:
        self.policies = policies
        self.audit = audit
        self.cache = cache
        self._client = client
        self._replay_only = replay_only
        self._trial = trial
        self.totals = RuntimeTotals()

    @property
    def has_client(self) -> bool:
        return self._client is not None

    def decide(
        self,
        policy_id: str,
        *,
        subject: str,
        state: Any,
        questions: JevQuestions,
        sufficiency: InputSufficiency,
    ) -> DecisionOutcome:
        validate_questions(questions)
        api_key = self._client.api_key if self._client else None
        request = _Request(
            policy=self.policies.get(policy_id),
            subject=redact(subject, api_key),
            state=redact_value(state, api_key),
            questions=questions,
            sufficiency=sufficiency,
        )
        self.totals.decisions += 1
        if request.policy.mode is PolicyMode.OFF:
            return self._skipped(request, rule=None, verdict=VERDICT_SKIPPED)
        if sufficiency.level is SufficiencyLevel.MISSING:
            return self._skipped(request, rule=RULE_INPUT_MISSING, verdict=VERDICT_INSUFFICIENT)
        key = decision_key(request.state, questions, JEV_PINNED_MODEL)
        try:
            cached = self.cache.get(key)
            replay = None if cached is None else _checked_replay(cached.replay(), questions)
        except CacheInvalidError as error:
            invalid = JevFailure(FailureReason.CACHE_INVALID, str(error), 0)
            return self._settled(request, key, invalid, cache_hit=False)
        if replay is not None:
            self.totals.cache_hits += 1
            return self._settled(request, key, replay, cache_hit=True)
        result = self._call(request)
        if not isinstance(result, JevFailure):
            self.cache.put(key, result)
        return self._settled(request, key, result, cache_hit=False)

    def record_deterministic(
        self,
        policy_id: str,
        *,
        subject: str,
        rule: str,
        verdict: str,
        answers: JevAnswers | None = None,
        carried_from: str | None = None,
    ) -> DecisionRecord:
        """Audit a decision a deterministic rule settled before Jev was needed.

        ``answers`` are the answers the settlement stands for (a carry-forward
        repeats its origin's answers) and ``carried_from`` names the origin
        decision's request id, so the audit alone can rebuild every ΣP. Under an
        ``enforce`` policy the settlement counts toward verdicts like a Jev answer.
        """
        policy = self.policies.get(policy_id)
        self.totals.decisions += 1
        enforced = policy.mode is PolicyMode.ENFORCE
        return self.audit.append(
            self._record(
                policy,
                subject=redact(subject, self._client.api_key if self._client else None),
                stage=DecisionStage.DETERMINISTIC,
                rule=rule,
                verdict=verdict,
                answers=None if answers is None else answers_wire(answers),
                carried_from=carried_from,
                enforced=enforced,
            )
        )

    def _call(self, request: _Request) -> JevResult:
        if len(state_text(request.state)) > JEV_STATE_CHAR_LIMIT:
            return JevFailure(
                FailureReason.STATE_TOO_LARGE,
                f"Jev state is over {JEV_STATE_CHAR_LIMIT} characters.",
                0,
            )
        if self._client is None:
            if self._replay_only:
                return JevFailure(FailureReason.CACHE_MISS, "No cached decision to replay.", 0)
            return JevFailure(
                FailureReason.MISSING_KEY, "No OpenRouter API key is configured for Jev.", 0
            )
        self.totals.provider_calls += 1
        return self._client.decide(request.state, request.questions)

    def _skipped(self, request: _Request, *, rule: str | None, verdict: str) -> DecisionOutcome:
        record = self._record(
            request.policy,
            subject=request.subject,
            stage=DecisionStage.SKIPPED,
            rule=rule,
            verdict=verdict,
            questions=questions_wire(request.questions),
            sufficiency=request.sufficiency,
        )
        return DecisionOutcome(self.audit.append(record))

    def _settled(
        self, request: _Request, key: str, result: JevResult, *, cache_hit: bool
    ) -> DecisionOutcome:
        common: dict[str, Any] = {
            "subject": request.subject,
            "state": request.state,
            "questions": questions_wire(request.questions),
            "sufficiency": request.sufficiency,
            "cache_key": key,
            "cache_hit": cache_hit,
            "latency_ms": result.latency_ms,
            "resolved_model": result.resolved_model,
        }
        if isinstance(result, JevFailure):
            reason = result.reason.value
            self.totals.failures[reason] = self.totals.failures.get(reason, 0) + 1
            record = self._record(
                request.policy,
                stage=DecisionStage.JEV_ERROR,
                verdict=VERDICT_ERROR,
                error=result.error,
                failure_reason=reason,
                http_status=result.http_status,
                **common,
            )
            return DecisionOutcome(self.audit.append(record))
        self.totals.cost_usd += result.cost_usd
        enforced = request.policy.mode is PolicyMode.ENFORCE
        record = self._record(
            request.policy,
            stage=DecisionStage.JEV,
            verdict=_verdict(request.policy, result.answers),
            answers=answers_wire(result.answers),
            provider_response_id=result.provider_response_id,
            cost_usd=result.cost_usd,
            enforced=enforced,
            **common,
        )
        return DecisionOutcome(self.audit.append(record), result.answers)

    def _record(
        self,
        policy: JevPolicy,
        *,
        stage: DecisionStage,
        verdict: str,
        sufficiency: InputSufficiency | None = None,
        enforced: bool = False,
        **fields: Any,
    ) -> DecisionRecord:
        return DecisionRecord(
            timestamp=utc_timestamp(),
            request_id=new_request_id(),
            policy=policy.id,
            policy_version=policy.version,
            mode=policy.mode,
            question_set=policy.question_set,
            stage=stage,
            trial=self._trial,
            input_sufficiency=sufficiency,
            verdict=verdict,
            enforced=enforced,
            counts_toward_verdicts=enforced,
            labels=policy.labels,
            **fields,
        )


def _checked_replay(replay: JevSuccess, questions: JevQuestions) -> JevSuccess:
    """The replayed decision, provided it answers exactly the questions asked."""
    for question_id, question in questions.items():
        if not answer_matches(question, replay.answers.get(question_id)):
            raise CacheInvalidError(f'cached answer for "{question_id}" does not fit the question')
    return replay


def _verdict(policy: JevPolicy, answers: JevAnswers) -> str:
    """Shadow answers stay uncalibrated; enforce applies the frozen threshold to noul answers."""
    if policy.mode is not PolicyMode.ENFORCE or policy.promotion is None:
        return VERDICT_UNCALIBRATED
    threshold = policy.promotion.threshold
    flagged = any(
        isinstance(answer, NoulAnswer) and answer.noul >= threshold for answer in answers.values()
    )
    return VERDICT_FLAG if flagged else VERDICT_CLEAR
