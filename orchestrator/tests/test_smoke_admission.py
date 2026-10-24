"""Smoke-before-measure admission: match, each mismatch, unclean smokes and the override."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from raidar.contract.trial import ExecutionValidity, TrialRecord, ValidityCheck
from raidar.engine.health.admission import (
    EMPTY_TREE_DIGEST,
    AdmissionError,
    admit,
    experiment_kind,
    identity,
    mismatches,
    smoke_problem,
)
from raidar.engine.health.audit import audit_trial
from raidar.engine.health.model import HealthCheck, HealthReport, write_health
from tests.health_support import TRIAL_ID, HealthyTrial, healthy_trial

RUNNER = {"commit": "abc", "dirty": False, "toolchain_sha256": "t" * 64}
RECIPES = {"pack": "p" * 64, "graders": {}}
TARGET = {"name": "mbp", "docker": {"ncpu": 4, "server_version": "29"}}
AGENT = {
    "id": "luna",
    "name": "codex",
    "model": "gpt-6-luna",
    "reasoning_effort": "low",
    "provider": "openai",
}


def lock(kind: str = "smoke", *agents: dict[str, Any]) -> dict[str, Any]:
    entries = list(agents) or [AGENT]
    arms = [{"id": f"arm-{a['id']}", "agent": a["id"]} for a in entries]
    return {"execution": {"design": {"kind": kind, "agents": entries, "arms": arms}}}


def wanted(**changes: Any) -> dict[str, Any] | None:
    parts = {"lock": lock("ab-test"), "runner": RUNNER, "recipes": RECIPES, "target": TARGET}
    parts |= changes
    return identity(parts["lock"], parts["runner"], parts["recipes"], parts["target"])


def smoke_job(root: Path, name: str = "smoke-01", **changes: Any) -> HealthyTrial:
    """A finished smoke job under ``root/<exp>/<name>``, clean unless ``changes`` say otherwise."""
    job_dir = root / "exp" / name
    t = healthy_trial(root / ".work" / name, job_dir=job_dir)
    manifest = {
        "runner": changes.get("runner", RUNNER),
        "recipes": changes.get("recipes", RECIPES),
        "execution_target": changes.get("target", TARGET),
        "selected": [TRIAL_ID],
        "finished_at": "2026-01-01T00:00:00+00:00",
        **changes.get("manifest", {}),
    }
    t.job_file.write_text(json.dumps(manifest))
    (job_dir / "job.lock.json").write_text(json.dumps(changes.get("lock", lock("smoke"))))
    if changes.get("health", True):
        write_health(t.trial_dir, audit_trial(t.job_dir, t.trial_dir, t.record, t.job()))
    return t


def decide(root: Path, **kwargs: Any) -> dict[str, Any]:
    return admit(
        kwargs.pop("kind", "ab-test"), wanted(), root, **{"allow_unsmoked": False, **kwargs}
    )


def test_the_experiment_kind_and_identity_come_from_the_lock() -> None:
    assert experiment_kind(lock("benchmark")) == "benchmark"
    assert experiment_kind({}) is None
    found = wanted()
    assert found is not None
    assert found["agents"] == [["codex", "gpt-6-luna", "low"]]
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
        "overridden": False,
    }


def test_a_smoke_covering_more_agents_still_clears_a_narrower_run(tmp_path: Path) -> None:
    other = {**AGENT, "id": "haiku", "name": "claude-code", "model": "claude-haiku-4-5"}
    smoke_job(tmp_path, lock=lock("smoke", AGENT, other))
    assert decide(tmp_path)["smoke_job"] == "exp/smoke-01"


@pytest.mark.parametrize(
    ("changes", "wanted_changes", "why"),
    [
        ({"runner": {**RUNNER, "toolchain_sha256": "x" * 64}}, {}, "toolchain_sha256 differs"),
        ({"recipes": {**RECIPES, "pack": "q" * 64}}, {}, "pack differs"),
        ({"target": {**TARGET, "name": "other"}}, {}, "target differs"),
        ({"target": {**TARGET, "docker": {"ncpu": 8}}}, {}, "target differs"),
        ({}, {"lock": lock("ab-test", {**AGENT, "model": "gpt-9"})}, "not smoked"),
        ({}, {"lock": lock("ab-test", {**AGENT, "reasoning_effort": "high"})}, "not smoked"),
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


def _steps(t: HealthyTrial, *digests: tuple[str | None, str | None]) -> TrialRecord:
    """The trial's record with one step per ``(parent, own)`` snapshot digest pair."""
    template = t.record.step_results[0]
    assert template.snapshot is not None
    results = tuple(
        template.model_copy(
            update={
                "step": f"checkpoint_{n}",
                "snapshot": template.snapshot.model_copy(update={"digest": own}),
                "parent_snapshot_digest": parent,
            }
        )
        for n, (parent, own) in enumerate(digests, 1)
    )
    record = t.record.model_copy(update={"step_results": results})
    (t.job_dir / "trial-records" / f"{TRIAL_ID}.json").write_text(record.model_dump_json())
    return record


def test_a_smoke_whose_agent_wrote_nothing_in_any_step_is_not_clean(tmp_path: Path) -> None:
    a, b = "a" * 64, "b" * 64
    for name, digests, why in [
        ("asked", [(None, EMPTY_TREE_DIGEST)], "checkpoint_1: the agent changed nothing"),
        ("stalled", [(None, a), (a, b), (b, b)], "checkpoint_3: the agent changed nothing"),
        ("worked", [(None, a), (a, b)], None),
    ]:
        t = smoke_job(tmp_path, name)
        _steps(t, *digests)
        problem = smoke_problem(t.job_dir, json.loads(t.job_file.read_text()))
        assert (problem is None) if why is None else (why in str(problem)), (name, problem)
    assert decide(tmp_path)["smoke_job"] == "exp/worked"
    only_asked = smoke_job(tmp_path / "other", "asked")
    _steps(only_asked, (None, EMPTY_TREE_DIGEST))
    with pytest.raises(AdmissionError, match="exp/asked.*checkpoint_1: the agent changed nothing"):
        decide(tmp_path / "other")


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
    with pytest.raises(AdmissionError, match="predates run provenance"):
        decide(tmp_path)
