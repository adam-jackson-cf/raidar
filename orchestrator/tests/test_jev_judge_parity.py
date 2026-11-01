"""``jev-judge/1`` judges, reworks and stops as the spike's define-build-verify judge did.

The criteria are a ``criteria/1`` copy a ``done`` stop kept, the gate is the record its seal wrote,
and Jev is a fake transport: closed, open and rework, ``max_passes``, no progress, the fit rule,
the gate's own endings, regression criteria and a judge that cannot be trusted all end as the
spike's ended.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from raidar_contract.workflow import PackageJudge

from raidar.judge.client import TransportResponse
from raidar.judge.live import LIVE_MODEL, RecordedJev
from raidar.judge.protocol import JEV_PINNED_MODEL
from raidar.subject.primitive.gate import Attribution, GateOutcome
from raidar.subject.primitive.site import TrialInvalid
from raidar.subject.workflow import asking
from raidar.subject.workflow.acceptance import Acceptance
from raidar.subject.workflow.package import (
    GATE_BUDGET,
    GATE_ERROR,
    GATE_PASSED,
    GATE_REFUSED,
    CheckpointPlan,
    PackageCheckpoint,
)
from raidar.subject.workflow.package_judge import JevJudge
from raidar.subject.workflow.stage import FIRST_BUILD, GATE_FIX, REWORK, Checkpoint, Resume, Spent
from tests.criteria_support import (
    CRITERIA,
    FILE,
    OBJECT,
    REJECTS,
    TESTS,
    Host,
    file_digest,
    gate_record,
    write_workspace,
)

pytestmark = pytest.mark.slow

KEY = "sk-or-test-key"
REWORK_PROMPT = "Fix these.\n\n%%%PUSHBACK%%%\n\nGate:\n%%%GATE%%%"
OPEN, CLOSED_P = 0.1, 0.95


class Jev:
    """Answers each aspect's question from ``answer`` (call number, situation) -> p or status."""

    def __init__(self, answer: Callable[[int, str], float | int]) -> None:
        self.answer = answer
        self.requests: list[dict[str, Any]] = []

    def post(
        self, url: str, *, headers: Mapping[str, str], body: bytes, timeout_s: float
    ) -> TransportResponse:
        request = json.loads(body)
        self.requests.append(request)
        given = self.answer(len(self.requests), request["state"]["aspect"]["situation"])
        if isinstance(given, int) and not isinstance(given, bool) and given >= 400:
            return TransportResponse(given, b'{"error": "boom"}')
        answers = {name: {"type": "noul", "noul": given} for name in request["questions"]}
        document = {
            "id": f"gen-{len(self.requests)}",
            "model": JEV_PINNED_MODEL,
            "provider": "TypeSafe",
            "answers": answers,
            "usage": {"input_tokens": 100, "output_tokens": 1, "cost": 0.001},
        }
        return TransportResponse(200, json.dumps(document).encode())


@pytest.fixture
def jev(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable[[int, str], float | int]], Jev]:
    """Install a fake Jev; the recorder reads its key from a given environment, never the host's."""
    monkeypatch.setattr(asking, "BACKOFF_S", 0.0)

    def install(answer: Callable[[int, str], float | int]) -> Jev:
        fake = Jev(answer)
        monkeypatch.setattr(
            asking,
            "new_jev",
            lambda directory, timeout_s: RecordedJev(
                directory, timeout_s=timeout_s, transport=fake, environ={"OPENROUTER_API_KEY": KEY}
            ),
        )
        return fake

    return install


class Runtime:
    """What ``PackageCheckpoint`` shows the judge: the seal, how it stood and the criteria copy."""

    def __init__(self, copy: dict[str, Any] | None, baseline: dict[str, Any] | None) -> None:
        self.copy = copy
        self.evidence = SimpleNamespace(gates=[], baseline=baseline)
        self.gate = SimpleNamespace(
            evidence=self.evidence, record=lambda: {"baseline": {"record": "gate/baseline.json"}}
        )
        self.sealed: Any = None
        self.gate_stand: str | None = None
        self.new_passes = 0
        self.done = True
        self.closed = False

    def output_copy(self, package: str, name: str) -> dict[str, Any] | None:
        assert (package, name) == ("define", "criteria")
        return self.copy

    def reported_done(self, package: str) -> bool:
        return self.done

    def producer_closed(self, package: str) -> bool:
        return self.closed

    def new_pass(self) -> None:
        self.new_passes += 1

    def seal(self, record: dict[str, Any], stand: str) -> None:
        self.evidence.gates.append(record)
        self.sealed = SimpleNamespace(record=record)
        self.gate_stand = stand


BASE = {
    "status": "passed",
    "tests": {
        "collected": [OBJECT, REJECTS],
        "outcomes": dict.fromkeys([OBJECT, REJECTS], "passed"),
    },
}


class Rig:
    """One checkpoint's judge, its criteria, workspace and scripted gate."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        max_passes: int = 3,
        deadline: float | None = None,
        model: str = LIVE_MODEL,
        criteria: str | None = None,
    ) -> None:
        self.workspace = tmp_path / "app"
        write_workspace(self.workspace, {FILE: TESTS})
        self.checkpoint = Checkpoint(Host(), tmp_path / "logs", "task", deadline=deadline)
        self.checkpoint.turns.append(Spent(FIRST_BUILD, 5.0))
        text = json.dumps(CRITERIA) if criteria is None else criteria
        path = tmp_path / "criteria.json"
        path.write_text(text)
        self.copy = {"sha256": file_digest(text)[7:], "host_path": str(path), "error": None}
        self.acceptance = Acceptance()
        self.acceptance.ac_tests.update({"F1.AC1": [OBJECT], "F1.AC2": [OBJECT]})
        definition = PackageJudge.model_validate(
            {
                "criteria": "define.criteria",
                "model": model,
                "closed_at": 0.8,
                "open_at": 0.3,
                "max_passes": max_passes,
                "rework": REWORK_PROMPT,
            }
        )
        self.judge = JevJudge(definition, self.acceptance, str(self.workspace))
        self.runtime = Runtime(self.copy, BASE)
        self.judge.start(self.checkpoint)
        self.judge.take_baseline(cast(Any, self.runtime.gate))

    async def seal(
        self,
        stand: str = GATE_PASSED,
        *,
        text: str = TESTS,
        verdict: str = "passed",
    ) -> Resume | None:
        """A turn ended: the gate sealed ``text`` and the judge looks at the seal."""
        write_workspace(self.workspace, {FILE: text})
        run = len(self.runtime.evidence.gates) + 1
        self.runtime.seal(gate_record(run, {FILE: text}, verdict=verdict), stand)
        self.judge.load_criteria(cast(Any, self.runtime))
        return await self.judge.after(self.checkpoint, cast(Any, self.runtime))


def changed(number: int) -> str:
    return TESTS + f"\n# edit {number}\n"


async def test_every_criterion_closed_on_the_first_pass_is_verified(
    tmp_path: Path, jev: Any
) -> None:
    fake = jev(lambda call, situation: CLOSED_P)
    rig = Rig(tmp_path)

    resume = await rig.seal()

    assert resume is None
    record = rig.judge.record()
    assert (record["ending"], record["gate_ending"], record["passes"]) == (
        "verified",
        "gate_passed",
        1,
    )
    (judged,) = record["judged"]
    assert judged["closed"] == ["F1.AC1", "F1.AC2"]
    assert judged["calls"] == [0]
    assert judged["sources"] == "workflow/sources/pass-1"
    assert len(fake.requests) == 1
    assert fake.requests[0]["state"]["aspect"] == {
        "situation": "a JSON array",
        "outcome": "it is rejected",
    }
    (call,) = record["judge_calls"]
    assert call["aspect"] == "F1.AC1.A1"
    assert call["probability"] == CLOSED_P
    assert (rig.checkpoint.logs_dir / call["request_file"]).is_file()
    assert (rig.checkpoint.logs_dir / "workflow/sources/final" / FILE).read_text() == TESTS
    assert record["criteria"] == "define.criteria"
    assert len(record["criteria_sha256"]) == 64
    assert record["baseline_passed"] == [OBJECT, REJECTS]
    assert record["baseline"] == {"record": "gate/baseline.json"}
    assert KEY not in json.dumps(record)

    assert await rig.seal(text=changed(1)) is None
    assert len(fake.requests) == 1
    assert rig.judge.record()["passes"] == 1
    assert (rig.checkpoint.logs_dir / "workflow/sources/final" / FILE).read_text() == TESTS


async def test_an_open_criterion_comes_back_as_pushback_and_closes_after_the_rework(
    tmp_path: Path, jev: Any
) -> None:
    jev(lambda call, situation: OPEN if call == 1 else CLOSED_P)
    rig = Rig(tmp_path)

    resume = await rig.seal()

    assert resume is not None
    assert resume.kind == REWORK
    assert rig.runtime.new_passes == 1
    assert rig.judge.ending is None

    assert await rig.seal(text=changed(1)) is None
    record = rig.judge.record()
    assert (record["ending"], record["passes"]) == ("verified", 2)
    assert [item["open"] for item in record["judged"]] == [["F1.AC1"], []]
    assert record["judged"][1]["calls"] == [1]
    assert record["judged"][1]["changed"] == [FILE]


async def test_the_pass_limit_ends_an_open_checkpoint_after_max_passes(
    tmp_path: Path, jev: Any
) -> None:
    jev(lambda call, situation: OPEN)
    rig = Rig(tmp_path, max_passes=2)

    assert await rig.seal() is not None
    assert await rig.seal(text=changed(1)) is None

    assert rig.judge.record()["ending"] == "pass_limit"
    assert rig.judge.record()["passes"] == 2


async def test_a_pass_that_changed_nothing_and_closed_nothing_is_no_progress(
    tmp_path: Path, jev: Any
) -> None:
    jev(lambda call, situation: OPEN)
    rig = Rig(tmp_path)

    assert await rig.seal() is not None
    assert await rig.seal() is None

    assert rig.judge.record()["ending"] == "no_progress"


async def test_a_judge_that_fails_every_attempt_leaves_the_aspect_undecided(
    tmp_path: Path, jev: Any
) -> None:
    fake = jev(lambda call, situation: 503)
    rig = Rig(tmp_path)

    resume = await rig.seal()

    assert resume is not None  # undecided counts as open: back to the builder
    assert len(fake.requests) == 4  # one attempt and three retries
    (judged,) = rig.judge.record()["judged"]
    assert judged["undecided"] == ["F1.AC1"]
    assert judged["closed"] == ["F1.AC2"]


async def test_a_failure_that_cannot_improve_is_not_retried(tmp_path: Path, jev: Any) -> None:
    fake = jev(lambda call, situation: 404)
    rig = Rig(tmp_path)

    await rig.seal()

    assert len(fake.requests) == 1


async def test_a_rework_that_does_not_fit_the_time_left_is_declined_and_the_tree_stays_judged(
    tmp_path: Path, jev: Any
) -> None:
    jev(lambda call, situation: OPEN)
    rig = Rig(tmp_path, deadline=time.monotonic() + 5.5)  # the first build took 5 s, then a gate

    resume = await rig.seal()

    assert resume is None
    assert (rig.judge.ending, rig.judge.record()["passes"]) == ("budget_exhausted", 1)


async def test_no_judge_pass_starts_after_the_deadline(tmp_path: Path, jev: Any) -> None:
    fake = jev(lambda call, situation: CLOSED_P)
    rig = Rig(tmp_path, deadline=time.monotonic() - 1)

    assert await rig.seal() is None

    assert rig.judge.ending == "deadline"
    assert fake.requests == []
    assert rig.judge.record()["judged"] == []


async def test_a_refusal_that_outlasts_its_fix_rounds_is_judged_and_ends_gate_refused(
    tmp_path: Path, jev: Any
) -> None:
    jev(lambda call, situation: CLOSED_P)
    rig = Rig(tmp_path)

    resume = await rig.seal(GATE_REFUSED, verdict="refused")

    assert resume is None
    record = rig.judge.record()
    assert (record["ending"], record["gate_ending"]) == ("gate_refused", "gate_refused")
    (judged,) = record["judged"]
    assert judged["gate_verdict"] == "refused"
    assert judged["closed"] == ["F1.AC1"]
    assert judged["open"] == ["F1.AC2"]


async def test_a_fix_turn_that_does_not_fit_is_declined_and_the_refused_tree_is_judged(
    tmp_path: Path, jev: Any
) -> None:
    jev(lambda call, situation: CLOSED_P)
    rig = Rig(tmp_path)

    assert await rig.seal(GATE_BUDGET, verdict="refused") is None

    record = rig.judge.record()
    assert (record["ending"], record["gate_ending"], record["passes"]) == (
        "budget_exhausted",
        "gate_refused",
        1,
    )


async def test_a_seal_that_neither_passed_nor_refused_is_judged_never(
    tmp_path: Path, jev: Any
) -> None:
    fake = jev(lambda call, situation: CLOSED_P)
    rig = Rig(tmp_path)

    assert await rig.seal(GATE_ERROR, verdict="error") is None

    assert (rig.judge.ending, rig.judge.gate_ending) == ("gate_error", "gate_error")
    assert fake.requests == []
    assert rig.judge.record()["judged"] == []


async def test_a_regression_criterion_closes_only_on_a_test_that_passed_on_the_base(
    tmp_path: Path, jev: Any
) -> None:
    jev(lambda call, situation: CLOSED_P)
    rig = Rig(tmp_path)
    rig.acceptance.ac_tests["F1.AC2"] = [REJECTS]
    rig.runtime.evidence.baseline = {
        "status": "passed",
        "tests": {"collected": [OBJECT], "outcomes": {OBJECT: "passed"}},
    }
    rig.judge.take_baseline(cast(Any, rig.runtime.gate))

    resume = await rig.seal()

    assert resume is not None  # REJECTS did not pass on the base
    (judged,) = rig.judge.record()["judged"]
    (open_one,) = [item for item in judged["criteria"] if item["status"] == "open"]
    assert (open_one["id"], open_one["failing"]) == ("F1.AC2", ["baseline"])


async def test_a_base_the_gate_could_not_run_leaves_no_passing_base_test(
    tmp_path: Path, jev: Any
) -> None:
    jev(lambda call, situation: CLOSED_P)
    rig = Rig(tmp_path)
    rig.runtime.evidence.baseline = {"status": "error", "tests": BASE["tests"]}

    rig.judge.take_baseline(cast(Any, rig.runtime.gate))

    assert rig.judge.record()["baseline_passed"] == []


async def test_a_producer_that_reported_done_owes_valid_criteria_or_the_trial_is_invalid(
    tmp_path: Path, jev: Any
) -> None:
    fake = jev(lambda call, situation: CLOSED_P)
    rig = Rig(tmp_path, criteria='{"contract": "criteria/1", "features": []}')

    with pytest.raises(TrialInvalid) as invalid:
        await rig.seal()

    assert invalid.value.kind == "integrity"
    record = rig.judge.record()
    assert record["criteria_sha256"] is None
    assert fake.requests == []
    assert record["errors"][0].startswith("define.criteria: ")


async def test_a_producer_that_reported_done_with_no_copy_is_invalid(
    tmp_path: Path, jev: Any
) -> None:
    jev(lambda call, situation: CLOSED_P)
    rig = Rig(tmp_path)
    rig.runtime.copy = None

    with pytest.raises(TrialInvalid):
        await rig.seal()


async def test_a_closed_producer_is_a_valid_outcome_that_judges_nothing(
    tmp_path: Path, jev: Any
) -> None:
    fake = jev(lambda call, situation: CLOSED_P)
    rig = Rig(tmp_path)
    rig.runtime.copy, rig.runtime.done, rig.runtime.closed = None, False, True

    assert await rig.seal(GATE_REFUSED, verdict="refused") is None

    record = rig.judge.record()
    assert (record["ending"], record["gate_ending"]) == ("no_criteria", "gate_refused")
    assert record["judged"] == []
    assert record["criteria_sha256"] is None
    assert not record["errors"]
    assert fake.requests == []


async def test_a_producer_neither_done_nor_closed_judges_zero_criteria_and_never_verifies(
    tmp_path: Path, jev: Any
) -> None:
    jev(lambda call, situation: CLOSED_P)
    rig = Rig(tmp_path)
    rig.runtime.copy, rig.runtime.done = None, False

    assert await rig.seal() is None

    (judged,) = rig.judge.record()["judged"]
    assert judged["criteria"] == []
    assert rig.judge.ending == "no_progress"


async def test_a_criteria_copy_that_changes_after_it_was_read_is_invalid(
    tmp_path: Path, jev: Any
) -> None:
    jev(lambda call, situation: OPEN)
    rig = Rig(tmp_path)
    await rig.seal()

    rig.runtime.copy = {**rig.copy, "sha256": "0" * 64}
    with pytest.raises(TrialInvalid) as invalid:
        rig.judge.load_criteria(cast(Any, rig.runtime))

    assert invalid.value.kind == "integrity"
    assert rig.judge.record()["errors"] == [
        f"define.criteria: changed after it was read: {'0' * 64}"
    ]


async def test_a_criteria_copy_whose_bytes_differ_from_its_digest_is_invalid(
    tmp_path: Path, jev: Any
) -> None:
    jev(lambda call, situation: CLOSED_P)
    rig = Rig(tmp_path)
    rig.runtime.copy = {**rig.copy, "sha256": "1" * 64}

    with pytest.raises(TrialInvalid):
        rig.judge.load_criteria(cast(Any, rig.runtime))

    assert "differ from its recorded digest" in rig.judge.record()["errors"][0]


async def test_a_cut_ends_a_checkpoint_the_judge_had_not_ended(tmp_path: Path, jev: Any) -> None:
    jev(lambda call, situation: OPEN)
    rig = Rig(tmp_path)
    assert await rig.seal() is not None

    rig.judge.close("deadline")

    assert rig.judge.ending == "deadline"


def test_a_judge_model_other_than_the_pinned_one_is_the_instruments_failure(
    tmp_path: Path,
) -> None:
    with pytest.raises(TrialInvalid) as invalid:
        Rig(tmp_path, model="someone/else-1")

    assert invalid.value.kind == "instrument"


class FakeGate:
    """The gate's seal and prefix retention, scripted."""

    def __init__(self, outcomes: list[GateOutcome]) -> None:
        self.outcomes = outcomes
        self.retained: list[int] = []

    async def seal(self, after: str) -> GateOutcome:
        return self.outcomes.pop(0)

    async def retain_prefix(self, fix_round: int) -> None:
        self.retained.append(fix_round)


def outcome(attribution: Attribution) -> GateOutcome:
    return GateOutcome(attribution, {"checks": []}, "lint failed")


async def test_gate_fixes_keep_every_pre_fix_snapshot_across_judged_passes(
    tmp_path: Path,
) -> None:
    checkpoint = Checkpoint(Host(), tmp_path, "task")
    runtime = PackageCheckpoint(
        checkpoint,
        [],
        CheckpointPlan("%%%TASK%%%", "python", {}, max_fix_rounds=1),
        runner=cast(Any, object()),
    )
    gate = FakeGate([outcome(Attribution.REFUSED)] * 4 + [outcome(Attribution.PASSED)])
    runtime.gate = cast(Any, gate)

    first = await runtime.check("t1")
    assert first is not None
    assert first.kind == GATE_FIX
    assert await runtime.check("t2") is None
    assert runtime.gate_stand == GATE_REFUSED  # the round bound is per pass

    runtime.new_pass()
    again = await runtime.check("t3")
    assert again is not None
    assert again.kind == GATE_FIX
    assert await runtime.check("t4") is None

    assert await runtime.check("t5") is None
    assert runtime.gate_stand == GATE_PASSED
    assert gate.retained == [1, 2]  # a later pass never overwrites an earlier pre-fix tree
