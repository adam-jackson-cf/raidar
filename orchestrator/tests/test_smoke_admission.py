"""Smoke-before-measure admission: match, each mismatch, unclean smokes and the override.

Smoke jobs are small in-memory records written as a few JSON files; no trial is run or audited.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
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
    admit,
    experiment_kind,
    identity,
    mismatches,
    smoke_problem,
)
from raidar.engine.health.model import HealthCheck, HealthReport, write_health
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


@dataclass
class SmokeJob:
    job_dir: Path
    trial_dir: Path
    record: TrialRecord

    @property
    def job_file(self) -> Path:
        return self.job_dir / "job.json"

    @property
    def record_file(self) -> Path:
        return self.job_dir / "trial-records" / f"{TRIAL_ID}.json"


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


def smoke_job(root: Path, name: str = "smoke-01", **changes: Any) -> SmokeJob:
    """A finished smoke job under ``root/<exp>/<name>``, clean unless ``changes`` say otherwise."""
    job_dir = root / "exp" / name
    smoke_lock = changes.get("lock", lock("smoke"))
    arm = changes.get("executed_arm", next(iter(smoke_lock["execution"]["arms"])))
    record = trial_record(arm, *changes.get("steps", ()))
    job = SmokeJob(job_dir, job_dir / "trials" / TRIAL_ID, record)
    job.record_file.parent.mkdir(parents=True)
    job.record_file.write_text(job.record.model_dump_json())
    manifest = {
        "runner": changes.get("runner", RUNNER),
        "recipes": changes.get("recipes", RECIPES),
        "execution_target": changes.get("target", TARGET),
        "selected": [TRIAL_ID],
        "finished_at": "2026-01-01T00:00:00+00:00",
        **changes.get("manifest", {}),
    }
    job.job_file.write_text(json.dumps(manifest))
    (job_dir / "job.lock.json").write_text(json.dumps(smoke_lock))
    if changes.get("health", True):
        write_health(job.trial_dir, HealthReport(trial_id=TRIAL_ID, checks=()))
    return job


def decide(root: Path, **kwargs: Any) -> dict[str, Any]:
    return admit(
        kwargs.pop("kind", "ab-test"), wanted(), root, **{"allow_unsmoked": False, **kwargs}
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


def test_a_smoke_kind_is_always_admitted(tmp_path: Path) -> None:
    assert admit("smoke", wanted(), tmp_path, allow_unsmoked=False) == {
        "required": False,
        "kind": "smoke",
    }


def test_a_clean_matching_smoke_admits_and_is_recorded(tmp_path: Path) -> None:
    smoke_job(tmp_path)
    assert decide(tmp_path) == {
        "required": True,
        "kind": "ab-test",
        "smoke_job": "exp/smoke-01",
        "smoke_jobs": ["exp/smoke-01"],
        "overridden": False,
    }


def test_a_smoke_covering_more_arms_still_clears_a_narrower_run(tmp_path: Path) -> None:
    smoke_job(tmp_path, lock=lock("smoke", AGENT, OTHER))
    assert decide(tmp_path)["smoke_job"] == "exp/smoke-01"


def test_a_draft_smoke_is_a_dev_job_and_never_admits_a_measured_run(tmp_path: Path) -> None:
    smoke_job(tmp_path, lock={**lock("smoke"), "draft": True})
    with pytest.raises(AdmissionError, match="draft lock marks a dev job"):
        decide(tmp_path)
    smoke_job(tmp_path, "smoke-02")
    assert decide(tmp_path)["smoke_job"] == "exp/smoke-02"


def test_only_executed_arms_clear_and_per_arm_smokes_combine(
    tmp_path: Path,
) -> None:
    both = lock("smoke", AGENT, OTHER)
    smoke_job(tmp_path, "codex", lock=both, executed_arm="arm-luna")
    wanted_both = wanted(lock=lock("benchmark", AGENT, OTHER))
    with pytest.raises(AdmissionError, match="not smoked"):
        admit("benchmark", wanted_both, tmp_path, allow_unsmoked=False)
    smoke_job(tmp_path, "claude-bad", lock=both, executed_arm="arm-terra", health=False)
    with pytest.raises(AdmissionError, match="not smoked"):
        admit("benchmark", wanted_both, tmp_path, allow_unsmoked=False)
    smoke_job(tmp_path, "claude", lock=both, executed_arm="arm-terra")
    decision = admit("benchmark", wanted_both, tmp_path, allow_unsmoked=False)
    assert decision["smoke_jobs"] == ["exp/claude", "exp/codex"]
    assert decision["overridden"] is False


def test_incompatible_per_arm_smokes_cannot_complete_coverage(tmp_path: Path) -> None:
    smoke_job(tmp_path, "codex")
    smoke_job(
        tmp_path,
        "claude",
        lock=lock("smoke", OTHER),
        runner={**RUNNER, "execution_sha256": "x" * 64},
    )
    with pytest.raises(AdmissionError, match="execution_sha256 differs"):
        admit(
            "benchmark",
            wanted(lock=lock("benchmark", AGENT, OTHER)),
            tmp_path,
            allow_unsmoked=False,
        )


def harness_lock(*, varies: tuple[str, ...] = ("harness",), **changes: Any) -> dict[str, Any]:
    """A benchmark lock whose second arm is another harness (``changes`` override its identity)."""
    other = {"capabilities": OTHER_HARNESS, **changes}
    return lock("benchmark", AGENT, {**OTHER, **other}, varies=varies)


def refusal(kind_lock: dict[str, Any], tmp_path: Path) -> str:
    with pytest.raises(AdmissionError) as raised:
        admit("benchmark", wanted(lock=kind_lock), tmp_path, allow_unsmoked=True)
    return str(raised.value)


def test_the_same_harness_for_every_arm_is_unchanged_and_needs_no_declaration(
    tmp_path: Path,
) -> None:
    decision = admit(
        "benchmark", wanted(lock=lock("benchmark", AGENT, OTHER)), tmp_path, allow_unsmoked=True
    )
    assert decision["overridden"] is True


def test_differing_capabilities_without_a_harness_declaration_are_refused_even_overridden(
    tmp_path: Path,
) -> None:
    assert "varies: [harness]" in refusal(harness_lock(varies=()), tmp_path)


@pytest.mark.parametrize("missing", [None, "network"])
def test_an_arm_must_declare_every_capability_even_when_the_harness_varies(
    tmp_path: Path, missing: str | None
) -> None:
    if missing is None:
        broken = harness_lock(capabilities=None)
        reason = "declares no capabilities"
    else:
        partial = {k: v for k, v in OTHER_HARNESS.items() if k != missing}
        broken = harness_lock(capabilities=partial)
        reason = f"lacks {missing}"
    assert reason in refusal(broken, tmp_path)


def test_a_declared_harness_comparison_with_one_model_and_reasoning_is_admitted(
    tmp_path: Path,
) -> None:
    same = harness_lock(model=AGENT["model"])
    decision = admit("benchmark", wanted(lock=same), tmp_path, allow_unsmoked=True)
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
    tmp_path: Path, changes: dict[str, Any], declared: tuple[str, ...], refused: str | None
) -> None:
    candidate = harness_lock(varies=declared, **changes)
    if refused is None:
        assert admit("benchmark", wanted(lock=candidate), tmp_path, allow_unsmoked=True)
    else:
        assert refused in refusal(candidate, tmp_path)


def test_an_arm_without_declared_capabilities_is_refused_outright(tmp_path: Path) -> None:
    undeclared = lock("benchmark", AGENT, {k: v for k, v in OTHER.items() if k != "capabilities"})
    assert "declares no capabilities" in refusal(undeclared, tmp_path)


def test_a_smoke_whose_arm_declared_other_capabilities_does_not_clear(tmp_path: Path) -> None:
    smoke_job(tmp_path, lock=lock("smoke", {**AGENT, "capabilities": OTHER_HARNESS}))

    with pytest.raises(AdmissionError, match="not smoked"):
        decide(tmp_path)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"agent_override": "oracle"}, "agent override"),
        ({"step_results": ()}, "executed no step"),
    ],
)
def test_a_smoke_requires_actual_work_by_the_declared_agent(
    tmp_path: Path, changes: dict[str, Any], reason: str
) -> None:
    trial = smoke_job(tmp_path)
    record = trial.record.model_copy(update=changes)
    (trial.job_dir / "trial-records" / f"{TRIAL_ID}.json").write_text(record.model_dump_json())
    with pytest.raises(AdmissionError, match=reason):
        decide(tmp_path)


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
    tmp_path: Path, changes: dict[str, Any], wanted_changes: dict[str, Any], why: str
) -> None:
    smoke_job(tmp_path, **changes)
    with pytest.raises(AdmissionError, match=why):
        admit("ab-test", wanted(**wanted_changes), tmp_path, allow_unsmoked=False)


def test_mismatches_lists_every_difference() -> None:
    a, b = wanted(), wanted(runner={**RUNNER, "toolchain_sha256": "x" * 64}, recipes={"pack": "z"})
    assert a is not None
    assert b is not None
    assert mismatches(a, b) == ["toolchain_sha256 differs", "pack differs"]


def test_a_smoke_that_is_not_clean_does_not_clear_the_run(tmp_path: Path) -> None:
    smoke_job(tmp_path, "unfinished", manifest={"finished_at": None})
    (tmp_path / "exp" / "unfinished" / "job.json").write_text(
        json.dumps({"runner": RUNNER, "recipes": RECIPES, "execution_target": TARGET})
    )
    smoke_job(tmp_path, "no-health", health=False)
    with pytest.raises(AdmissionError) as raised:
        decide(tmp_path)
    assert "unfinished: did not finish" in str(raised.value)
    assert "no health.json" in str(raised.value)


def test_an_invalid_trial_or_a_health_violation_is_not_clean(tmp_path: Path) -> None:
    bad = smoke_job(tmp_path, "invalid")
    failed = ValidityCheck(name="verifier_infra", passed=False, detail="x")
    record = bad.record.model_copy(
        update={"validity": ExecutionValidity(checks=(failed,))},
    )
    (bad.job_dir / "trial-records" / f"{TRIAL_ID}.json").write_text(record.model_dump_json())
    tampered = smoke_job(tmp_path, "tampered")
    violation = HealthCheck(id="T1", family="T", status="violation", detail="tool tree changed")
    write_health(tampered.trial_dir, HealthReport(trial_id=TRIAL_ID, checks=(violation,)))
    with pytest.raises(AdmissionError) as raised:
        decide(tmp_path)
    assert "is invalid" in str(raised.value)
    assert "health violation" in str(raised.value)


def test_a_smoke_whose_agent_wrote_nothing_in_any_step_is_not_clean(tmp_path: Path) -> None:
    a, b = "a" * 64, "b" * 64
    for name, steps, why in [
        ("asked", [(None, EMPTY_TREE_DIGEST)], "checkpoint_1: the agent changed nothing"),
        ("stalled", [(None, a), (a, b), (b, b)], "checkpoint_3: the agent changed nothing"),
        ("unrecorded", [(None, a), (a, None)], "checkpoint_2 recorded no /app snapshot"),
        ("worked", [(None, a), (a, b)], None),
    ]:
        t = smoke_job(tmp_path, name, steps=steps)
        problem = smoke_problem(t.job_dir, json.loads(t.job_file.read_text()))
        assert (problem is None) if why is None else (why in str(problem)), (name, problem)
    assert decide(tmp_path)["smoke_job"] == "exp/worked"
    smoke_job(tmp_path / "other", "asked", steps=[(None, EMPTY_TREE_DIGEST)])
    with pytest.raises(AdmissionError, match="exp/asked.*checkpoint_1: the agent changed nothing"):
        decide(tmp_path / "other")


def test_a_smoke_with_a_missing_or_unreadable_trial_record_is_not_clean(tmp_path: Path) -> None:
    missing = smoke_job(tmp_path, "missing")
    missing.record_file.unlink()
    garbled = smoke_job(tmp_path, "garbled")
    garbled.record_file.write_text("{not json")

    with pytest.raises(AdmissionError) as raised:
        decide(tmp_path)

    assert f"missing: trial {TRIAL_ID} has no record" in str(raised.value)
    assert f"garbled: trial {TRIAL_ID} has an unreadable record" in str(raised.value)


def test_an_aborted_or_trialless_smoke_is_not_clean(tmp_path: Path) -> None:
    smoke_job(tmp_path, "aborted", manifest={"aborted": "interrupted"})
    smoke_job(tmp_path, "trialless", manifest={"selected": []})

    with pytest.raises(AdmissionError) as raised:
        decide(tmp_path)

    assert "aborted: did not finish" in str(raised.value)
    assert "trialless: ran no trial" in str(raised.value)


def test_a_job_with_an_unreadable_manifest_or_lock_is_no_smoke_candidate(tmp_path: Path) -> None:
    broken = smoke_job(tmp_path, "broken-manifest")
    broken.job_file.write_text("[1, 2]")
    unlocked = smoke_job(tmp_path, "broken-lock")
    (unlocked.job_dir / "job.lock.json").write_text("{not json")

    recorded = decide(tmp_path, allow_unsmoked=True)

    assert recorded["unmet"] == {"": "no smoke job found"}


def test_an_identity_naming_an_arm_the_lock_does_not_record_is_unrecorded() -> None:
    assert identity(lock("smoke"), RUNNER, RECIPES, TARGET, arm_ids={"arm-ghost"}) is None
    assert identity(lock("smoke"), RUNNER, RECIPES, TARGET, arm_ids=set()) is None


def test_no_smoke_refuses_with_the_way_forward_and_the_override_is_recorded(
    tmp_path: Path,
) -> None:
    with pytest.raises(AdmissionError, match="ALLOW_UNSMOKED=1"):
        decide(tmp_path)
    recorded = decide(tmp_path, allow_unsmoked=True)
    assert recorded["overridden"] is True
    assert recorded["smoke_job"] is None
    assert recorded["unmet"] == {"": "no smoke job found"}
    assert admit("ab-test", None, tmp_path, allow_unsmoked=True)["unmet"] == {
        "": "run provenance is incomplete"
    }
    smoke_job(tmp_path)
    assert decide(tmp_path, allow_unsmoked=True)["overridden"] is False  # a match needs no override


def test_a_smoke_without_run_provenance_predates_the_gate(tmp_path: Path) -> None:
    t = smoke_job(tmp_path, "old")
    t.job_file.write_text(json.dumps({"selected": [TRIAL_ID], "finished_at": "x"}))
    with pytest.raises(AdmissionError, match="provenance.*incomplete"):
        decide(tmp_path)
