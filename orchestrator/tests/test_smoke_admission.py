"""Smoke-before-measure admission: match, each mismatch, unclean smokes and the override.

Smoke jobs are in-memory ``SmokeJob`` values handed to ``admit_jobs``; no job directory is built and
no trial is run or audited. Reading stored jobs from disk is proved by
``test_smoke_admission_stored_slow.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from raidar.contract.trial import (
    CheckpointState,
    ExecutionValidity,
    PlannedTrial,
    ProgressionRule,
    SnapshotInfo,
    StepResult,
    TrialHashes,
    TrialRecord,
    ValidityCheck,
)
from raidar.engine.health.admission import (
    EMPTY_TREE_DIGEST,
    AdmissionError,
    SmokeJob,
    admit_jobs,
    experiment_kind,
    identity,
    job_problem,
    mismatches,
)
from raidar.engine.health.model import HealthCheck, HealthReport
from tests.capability_support import CAPABILITIES, OTHER_HARNESS

TRIAL_ID = "b00-p0-baseline"

RUNNER = {
    "commit": "abc",
    "dirty": False,
    "toolchain_sha256": "t" * 64,
    "execution_sha256": "e" * 64,
}
RECIPES = {"pack": "p" * 64, "graders": {}}
TARGET = {"name": "mbp", "docker": {"ncpu": 4, "server_version": "29"}}
PRIMITIVE_CODE = {"build": "b" * 64}
AGENT = {
    "agent": "luna",
    "agent_release": "codex@3",
    "adapter": "codex",
    "provider": "openai",
    "model": "gpt-6-luna",
    "reasoning": "low",
    "tool": {"package": "@openai/codex", "binary": "codex", "version": "0.156.1"},
    "capabilities": CAPABILITIES,
    "workflow": "one-shot@1",
    "workflow_sha256": "w" * 64,
    "packages": {},
    "primitive_code": PRIMITIVE_CODE,
    "digest": "d" * 64,
}
OTHER = {**AGENT, "agent": "terra", "model": "gpt-6-terra", "digest": "e" * 64}
CLEAN = HealthReport(trial_id=TRIAL_ID, checks=())


def lock(
    kind: str = "smoke", *arms: dict[str, Any], varies: tuple[str, ...] = ()
) -> dict[str, Any]:
    """A lock whose ``execution.arms`` hold one identity per arm, ``arm-<agent>``."""
    identities = {f"arm-{arm['agent']}": arm for arm in arms or (AGENT,)}
    design: dict[str, Any] = {"kind": kind, **({"varies": list(varies)} if varies else {})}
    return {"execution": {"design": design, "arms": identities}}


def wanted(**changes: Any) -> dict[str, Any] | None:
    parts = {"lock": lock("ab-test"), "runner": RUNNER, "recipes": RECIPES, "target": TARGET}
    parts |= changes
    return identity(parts["lock"], parts["runner"], parts["recipes"], parts["target"])


def trial_record(arm: str, *snapshots: tuple[str | None, str | None]) -> TrialRecord:
    """A valid in-memory record: one step per ``(parent, own)`` snapshot digest pair.

    Default is one step whose agent changed ``/app``.
    """
    pairs = snapshots or ((None, "a" * 64),)
    steps = tuple(f"checkpoint_{n}" for n in range(1, len(pairs) + 1))
    trial = PlannedTrial(
        trial_id=TRIAL_ID,
        attempt=0,
        order=0,
        sequence=0,
        arm=arm,
        problem="scb/adder@fixture",
        problem_index=0,
        steps=steps,
        hashes=TrialHashes(
            lock="l",
            problem="p",
            arm="a",
            workflow="w",
            packages="s",
            primitive_code="s",
            agent="g",
            protocol="r",
            trial="t",
        ),
    )
    results = tuple(
        StepResult(
            trial_id=TRIAL_ID,
            arm=arm,
            attempt=0,
            problem="scb/adder@fixture",
            index=n,
            step=step,
            checkpoint=step,
            checkpoint_state=CheckpointState.SOLVED,
            snapshot=SnapshotInfo(path=f"steps/{step}/app", digest=own) if own else None,
            parent_snapshot_digest=parent,
        )
        for n, (step, (parent, own)) in enumerate(zip(steps, pairs, strict=True))
    )
    return TrialRecord(
        experiment="exp",
        job_id="j",
        trial=trial,
        trial_dir=f"trials/{TRIAL_ID}",
        progression=ProgressionRule(mode="continue", reward_key="strict_pass_rate"),
        step_results=results,
        reached=tuple(False for _ in steps),
        reached_source="derived",
        validity=ExecutionValidity(checks=(ValidityCheck(name="trial_completed", passed=True),)),
    )


def smoke_job(name: str = "smoke-01", **changes: Any) -> SmokeJob:
    """A finished smoke job ``exp/<name>``, clean unless ``changes`` say otherwise.

    ``record`` holds changes to its trial record; ``health`` is its health report (``None``: none).
    """
    smoke_lock = changes.get("lock", lock("smoke"))
    arm = changes.get("executed_arm", next(iter(smoke_lock["execution"]["arms"])))
    record = trial_record(arm, *changes.get("steps", ())).model_copy(
        update=changes.get("record", {})
    )
    manifest = {
        "runner": changes.get("runner", RUNNER),
        "recipes": changes.get("recipes", RECIPES),
        "execution_target": changes.get("target", TARGET),
        "selected": [TRIAL_ID],
        "finished_at": "2026-01-01T00:00:00+00:00",
        **changes.get("manifest", {}),
    }
    for key in changes.get("without", ()):
        del manifest[key]
    health = changes.get("health", CLEAN)
    return SmokeJob(f"exp/{name}", manifest, smoke_lock, lambda trial_id: record, lambda r: health)


def admit(
    kind: str | None,
    found: dict[str, Any] | None,
    jobs: list[SmokeJob],
    *,
    allow_unsmoked: bool,
) -> dict[str, Any]:
    """``admit_jobs`` over ``jobs`` in name order, the order stored jobs are discovered in."""
    ordered = sorted(jobs, key=lambda job: job.name)
    return admit_jobs(kind, found, lambda: ordered, allow_unsmoked=allow_unsmoked)


def decide(jobs: list[SmokeJob], **kwargs: Any) -> dict[str, Any]:
    return admit(
        kwargs.pop("kind", "ab-test"), wanted(), jobs, **{"allow_unsmoked": False, **kwargs}
    )


def test_the_experiment_kind_and_identity_come_from_the_lock() -> None:
    assert experiment_kind(lock("benchmark")) == "benchmark"
    assert experiment_kind({}) is None
    found = wanted()
    assert found is not None
    assert found["arms"] == [{k: v for k, v in AGENT.items() if k not in ("agent", "digest")}]
    assert found["target"] == TARGET
    assert wanted(runner={}) is None
    assert wanted(target=None) is None


def test_a_smoke_kind_is_always_admitted() -> None:
    assert admit("smoke", wanted(), [], allow_unsmoked=False) == {
        "required": False,
        "kind": "smoke",
    }


def test_a_clean_matching_smoke_admits_and_is_recorded() -> None:
    assert decide([smoke_job()]) == {
        "required": True,
        "kind": "ab-test",
        "smoke_job": "exp/smoke-01",
        "smoke_jobs": ["exp/smoke-01"],
        "overridden": False,
    }


def test_a_smoke_covering_more_arms_still_clears_a_narrower_run() -> None:
    jobs = [smoke_job(lock=lock("smoke", AGENT, OTHER))]
    assert decide(jobs)["smoke_job"] == "exp/smoke-01"


def test_a_draft_smoke_is_a_dev_job_and_never_admits_a_measured_run() -> None:
    jobs = [smoke_job(lock={**lock("smoke"), "draft": True})]
    with pytest.raises(AdmissionError, match="draft lock marks a dev job"):
        decide(jobs)
    jobs.append(smoke_job("smoke-02"))
    assert decide(jobs)["smoke_job"] == "exp/smoke-02"


def test_only_executed_arms_clear_and_per_arm_smokes_combine() -> None:
    both = lock("smoke", AGENT, OTHER)
    jobs = [smoke_job("codex", lock=both, executed_arm="arm-luna")]
    wanted_both = wanted(lock=lock("benchmark", AGENT, OTHER))
    with pytest.raises(AdmissionError, match="not smoked"):
        admit("benchmark", wanted_both, jobs, allow_unsmoked=False)
    jobs.append(smoke_job("claude-bad", lock=both, executed_arm="arm-terra", health=None))
    with pytest.raises(AdmissionError, match="not smoked"):
        admit("benchmark", wanted_both, jobs, allow_unsmoked=False)
    jobs.append(smoke_job("claude", lock=both, executed_arm="arm-terra"))
    decision = admit("benchmark", wanted_both, jobs, allow_unsmoked=False)
    assert decision["smoke_jobs"] == ["exp/claude", "exp/codex"]
    assert decision["overridden"] is False


def test_incompatible_per_arm_smokes_cannot_complete_coverage() -> None:
    jobs = [
        smoke_job("codex"),
        smoke_job(
            "claude",
            lock=lock("smoke", OTHER),
            runner={**RUNNER, "execution_sha256": "x" * 64},
        ),
    ]
    with pytest.raises(AdmissionError, match="execution_sha256 differs"):
        admit(
            "benchmark",
            wanted(lock=lock("benchmark", AGENT, OTHER)),
            jobs,
            allow_unsmoked=False,
        )


def harness_lock(*, varies: tuple[str, ...] = ("harness",), **changes: Any) -> dict[str, Any]:
    """A benchmark lock whose second arm is another harness (``changes`` override its identity)."""
    other = {"capabilities": OTHER_HARNESS, **changes}
    return lock("benchmark", AGENT, {**OTHER, **other}, varies=varies)


def refusal(kind_lock: dict[str, Any]) -> str:
    with pytest.raises(AdmissionError) as raised:
        admit("benchmark", wanted(lock=kind_lock), [], allow_unsmoked=True)
    return str(raised.value)


def test_the_same_harness_for_every_arm_is_unchanged_and_needs_no_declaration() -> None:
    decision = admit(
        "benchmark", wanted(lock=lock("benchmark", AGENT, OTHER)), [], allow_unsmoked=True
    )
    assert decision["overridden"] is True


def test_differing_capabilities_without_a_harness_declaration_are_refused_even_overridden() -> None:
    assert "varies: [harness]" in refusal(harness_lock(varies=()))


@pytest.mark.parametrize("missing", [None, "network"])
def test_an_arm_must_declare_every_capability_even_when_the_harness_varies(
    missing: str | None,
) -> None:
    if missing is None:
        broken = harness_lock(capabilities=None)
        reason = "declares no capabilities"
    else:
        partial = {k: v for k, v in OTHER_HARNESS.items() if k != missing}
        broken = harness_lock(capabilities=partial)
        reason = f"lacks {missing}"
    assert reason in refusal(broken)


def test_a_declared_harness_comparison_with_one_model_and_reasoning_is_admitted() -> None:
    same = harness_lock(model=AGENT["model"])
    decision = admit("benchmark", wanted(lock=same), [], allow_unsmoked=True)
    assert decision["overridden"] is True


@pytest.mark.parametrize(
    ("changes", "declared", "refused"),
    [
        ({"model": "other-model"}, ("harness",), "canonical model"),
        ({"model": "other-model"}, ("harness", "model"), None),
        ({"model": AGENT["model"], "reasoning": "high"}, ("harness",), "reasoning"),
        ({"model": AGENT["model"], "reasoning": "high"}, ("harness", "reasoning"), None),
    ],
)
def test_a_harness_comparison_differing_in_model_or_reasoning_must_declare_it(
    changes: dict[str, Any], declared: tuple[str, ...], refused: str | None
) -> None:
    candidate = harness_lock(varies=declared, **changes)
    if refused is None:
        assert admit("benchmark", wanted(lock=candidate), [], allow_unsmoked=True)
    else:
        assert refused in refusal(candidate)


def test_an_arm_without_declared_capabilities_is_refused_outright() -> None:
    undeclared = lock("benchmark", AGENT, {k: v for k, v in OTHER.items() if k != "capabilities"})
    assert "declares no capabilities" in refusal(undeclared)


def test_a_smoke_whose_arm_declared_other_capabilities_does_not_clear() -> None:
    jobs = [smoke_job(lock=lock("smoke", {**AGENT, "capabilities": OTHER_HARNESS}))]

    with pytest.raises(AdmissionError, match="not smoked"):
        decide(jobs)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"agent_override": "oracle"}, "agent override"),
        ({"step_results": ()}, "executed no step"),
    ],
)
def test_a_smoke_requires_actual_work_by_the_declared_agent(
    changes: dict[str, Any], reason: str
) -> None:
    with pytest.raises(AdmissionError, match=reason):
        decide([smoke_job(record=changes)])


@pytest.mark.parametrize(
    ("changes", "wanted_changes", "why"),
    [
        ({"runner": {**RUNNER, "toolchain_sha256": "x" * 64}}, {}, "toolchain_sha256 differs"),
        ({"runner": {**RUNNER, "execution_sha256": "x" * 64}}, {}, "execution_sha256 differs"),
        ({"runner": {"toolchain_sha256": "t" * 64}}, {}, "provenance.*incomplete"),
        ({"recipes": {**RECIPES, "pack": "q" * 64}}, {}, "pack differs"),
        ({"target": {**TARGET, "name": "other"}}, {}, "target differs"),
        ({"target": {**TARGET, "docker": {"ncpu": 8}}}, {}, "target differs"),
        ({}, {"lock": lock("ab-test", {**AGENT, "model": "gpt-9"})}, "not smoked"),
        ({}, {"lock": lock("ab-test", {**AGENT, "reasoning": "high"})}, "not smoked"),
        (
            {},
            {"lock": lock("ab-test", {**AGENT, "primitive_code": {"build": "x" * 64}})},
            "not smoked",
        ),
        (
            {},
            {"lock": lock("ab-test", {**AGENT, "packages": {"p": {"sha256": "x" * 64}}})},
            "not smoked",
        ),
        ({}, {"lock": lock("ab-test", {**AGENT, "workflow_sha256": "x" * 64})}, "not smoked"),
    ],
)
def test_each_mismatch_refuses(
    changes: dict[str, Any], wanted_changes: dict[str, Any], why: str
) -> None:
    with pytest.raises(AdmissionError, match=why):
        admit("ab-test", wanted(**wanted_changes), [smoke_job(**changes)], allow_unsmoked=False)


def test_mismatches_lists_every_difference() -> None:
    a, b = wanted(), wanted(runner={**RUNNER, "toolchain_sha256": "x" * 64}, recipes={"pack": "z"})
    assert a is not None
    assert b is not None
    assert mismatches(a, b) == ["toolchain_sha256 differs", "pack differs"]


def test_a_smoke_that_is_not_clean_does_not_clear_the_run() -> None:
    jobs = [
        smoke_job("unfinished", without=("finished_at",)),
        smoke_job("no-health", health=None),
    ]
    with pytest.raises(AdmissionError) as raised:
        decide(jobs)
    assert "unfinished: did not finish" in str(raised.value)
    assert "no health.json" in str(raised.value)


def test_an_invalid_trial_or_a_health_violation_is_not_clean() -> None:
    failed = ValidityCheck(name="verifier_infra", passed=False, detail="x")
    violation = HealthCheck(id="T1", family="T", status="violation", detail="tool tree changed")
    jobs = [
        smoke_job("invalid", record={"validity": ExecutionValidity(checks=(failed,))}),
        smoke_job("tampered", health=HealthReport(trial_id=TRIAL_ID, checks=(violation,))),
    ]
    with pytest.raises(AdmissionError) as raised:
        decide(jobs)
    assert "is invalid" in str(raised.value)
    assert "health violation" in str(raised.value)


def test_a_smoke_whose_agent_wrote_nothing_in_any_step_is_not_clean() -> None:
    a, b = "a" * 64, "b" * 64
    jobs = []
    for name, steps, why in [
        ("asked", [(None, EMPTY_TREE_DIGEST)], "checkpoint_1: the agent changed nothing"),
        ("stalled", [(None, a), (a, b), (b, b)], "checkpoint_3: the agent changed nothing"),
        ("unrecorded", [(None, a), (a, None)], "checkpoint_2 recorded no /app snapshot"),
        ("worked", [(None, a), (a, b)], None),
    ]:
        job = smoke_job(name, steps=steps)
        jobs.append(job)
        problem = job_problem(job)[1]
        assert (problem is None) if why is None else (why in str(problem)), (name, problem)
    assert decide(jobs)["smoke_job"] == "exp/worked"
    asked = [smoke_job("asked", steps=[(None, EMPTY_TREE_DIGEST)])]
    with pytest.raises(AdmissionError, match="exp/asked.*checkpoint_1: the agent changed nothing"):
        decide(asked)


def test_an_aborted_or_trialless_smoke_is_not_clean() -> None:
    jobs = [
        smoke_job("aborted", manifest={"aborted": "interrupted"}),
        smoke_job("trialless", manifest={"selected": []}),
    ]

    with pytest.raises(AdmissionError) as raised:
        decide(jobs)

    assert "aborted: did not finish" in str(raised.value)
    assert "trialless: ran no trial" in str(raised.value)


def test_an_identity_naming_an_arm_the_lock_does_not_record_is_unrecorded() -> None:
    assert identity(lock("smoke"), RUNNER, RECIPES, TARGET, arm_ids={"arm-ghost"}) is None
    assert identity(lock("smoke"), RUNNER, RECIPES, TARGET, arm_ids=set()) is None


def test_no_smoke_refuses_with_the_way_forward_and_the_override_is_recorded() -> None:
    with pytest.raises(AdmissionError, match="ALLOW_UNSMOKED=1"):
        decide([])
    recorded = decide([], allow_unsmoked=True)
    assert recorded["overridden"] is True
    assert recorded["smoke_job"] is None
    assert recorded["unmet"] == {"": "no smoke job found"}
    assert admit("ab-test", None, [], allow_unsmoked=True)["unmet"] == {
        "": "run provenance is incomplete"
    }
    jobs = [smoke_job()]
    assert decide(jobs, allow_unsmoked=True)["overridden"] is False  # a match needs no override


def test_a_smoke_without_run_provenance_predates_the_gate() -> None:
    old = smoke_job("old", without=("runner", "recipes", "execution_target"))
    with pytest.raises(AdmissionError, match="provenance.*incomplete"):
        decide([old])
