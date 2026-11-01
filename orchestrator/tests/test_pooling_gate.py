"""The pooling gate: refusal kinds, tamper, overrides, pre-epoch jobs, timing across targets,
capability declarations and parity records.

Jobs are built in memory; the records-manifest check is an injected reader (the real files
are proven in ``test_analysis_command``).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from raidar.contract.trial import (
    ExecutionValidity,
    ParityRecord,
    PlannedTrial,
    ProgressionRule,
    TrialHashes,
    TrialRecord,
)
from raidar.evaluation.analysis import pooling
from raidar.evaluation.analysis.pooling import PoolingError, check_pooling
from raidar.evaluation.analysis.sources import JobDir
from tests.capability_support import CAPABILITIES, OTHER_HARNESS

TRIAL = "t1"
SEALED = {"sealed": True}
DAMAGED = {"sealed": False}
PARITY = ParityRecord(
    image_id="sha256:image",
    seed_digest="seed",
    limits={"agent_timeout_s": 1800.0},
    network=({"network_mode": "allowlist", "allowed_hosts": ["api.openai.com"]},),
)


@pytest.fixture(autouse=True)
def _records_manifest_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """The injected seal reader: a trial is intact when its seal is ``SEALED``."""

    def findings(
        trial_dir: Path, record: Path, job_path: Path, sealed: Mapping[str, Any] | None
    ) -> list[str]:
        if sealed is None:
            return ["trial has no records manifest"]
        return [] if sealed == SEALED else ["recorded files differ from the sealed digest"]

    monkeypatch.setattr(pooling, "tamper_findings", findings)


def _record(trial_id: str, arm: str, parity: ParityRecord | None) -> TrialRecord:
    hashes = TrialHashes(
        lock="l", problem="p", arm=arm, workflow="w", packages="s", primitive_code="s",
        agent="h", protocol="p",
        trial=trial_id,
    )  # fmt: skip
    planned = PlannedTrial(
        trial_id=trial_id, attempt=0, order=0, sequence=0, arm=arm, problem="scb/forge@x",
        problem_index=0, steps=("checkpoint_1",), hashes=hashes,
    )  # fmt: skip
    return TrialRecord(
        experiment="exp",
        job_id="job",
        trial=planned,
        trial_dir=f"trials/{trial_id}",
        progression=ProgressionRule(mode="continue", reward_key="solved"),
        step_results=(),
        reached=(),
        reached_source="derived",
        validity=ExecutionValidity(checks=()),
        parity=parity,
    )


def _manifest(**changes: Any) -> dict[str, Any]:
    base = {
        "job_id": "job",
        "mechanics_epoch": "mechanics-v1",
        "runner": {
            "commit": "abc",
            "dirty": False,
            "toolchain_sha256": "tc1",
            "execution_sha256": "execution1",
        },
        "recipes": {"pack": "pack1", "graders": {"scb/forge@x": "g1"}},
        "effective_limits": {"agent_s": {"a": 1800.0}, "verifier_s": 1500.0},
        "execution_target": {
            "name": "mbp",
            "docker": {
                "os": "linux",
                "arch": "arm64",
                "ncpu": 8,
                "mem_total": 1,
                "server_version": "1",
            },
        },
    }
    return {**base, **changes}


def _job(
    name: str,
    manifest: dict[str, Any] | None = None,
    records: tuple[TrialRecord, ...] = (),
    *,
    seal: Mapping[str, Any] = SEALED,
    **plan: Any,
) -> JobDir:
    """An in-memory job with one sealed trial; ``manifest`` is its job.json (default: epoch job)."""
    fields = _manifest(job_id=name) if manifest is None else {**manifest, "job_id": name}
    if "mechanics_epoch" in fields:
        fields["records_manifests"] = {TRIAL: seal}
    hashes = {
        "agent": plan.get("agent", "h1"),
        "workflow": plan.get("workflow", "w1"),
        "packages": plan.get("packages", "s1"),
        "primitive_code": plan.get("primitive_code", "s1"),
    }
    trials = [{"trial_id": TRIAL, "arm": "a", "hashes": hashes}]
    capabilities = plan.get("capabilities", CAPABILITIES)
    arm = {
        "capabilities": capabilities,
        "canonical_model": plan.get("canonical_model"),
        "reasoning": plan.get("reasoning"),
    }
    arms = {"a": arm if capabilities is not None else {}}
    return JobDir(
        path=Path("jobs") / name,
        manifest=fields,
        plan={"arms": arms, "trials": trials, "varies": plan.get("varies", [])},
        records=records,
    )


def _pair(**changes: Any) -> list[JobDir]:
    return [_job("one"), _job("two", _manifest(**changes))]


def test_matching_jobs_pool_and_a_single_job_is_checked_for_integrity() -> None:
    report = check_pooling(_pair())
    assert (report.unmeasured, report.overrides_used, report.timing_cost_comparable) == (
        (),
        (),
        True,
    )
    assert check_pooling([_job("solo")]).findings == ()


@pytest.mark.parametrize(
    ("field", "changes"),
    [
        (
            "toolchain",
            {
                "runner": {
                    "commit": "abc",
                    "dirty": False,
                    "toolchain_sha256": "tc2",
                    "execution_sha256": "execution1",
                }
            },
        ),
        (
            "execution_code",
            {
                "runner": {
                    "commit": "def",
                    "dirty": False,
                    "toolchain_sha256": "tc1",
                    "execution_sha256": "execution2",
                }
            },
        ),
        ("pack_recipe", {"recipes": {"pack": "pack2", "graders": {"scb/forge@x": "g1"}}}),
        ("grader_recipe", {"recipes": {"pack": "pack1", "graders": {"scb/forge@x": "g2"}}}),
        ("effective_limits", {"effective_limits": {"agent_s": {"a": 60.0}, "verifier_s": 1500.0}}),
        (
            "dirty_runner",
            {
                "runner": {
                    "commit": "abc",
                    "dirty": True,
                    "toolchain_sha256": "tc1",
                    "execution_sha256": "execution1",
                }
            },
        ),
    ],
)
def test_pooling_refuses_each_provenance_difference_naming_jobs_and_values(
    field: str, changes: dict[str, Any]
) -> None:
    jobs = _pair(**changes)
    with pytest.raises(PoolingError) as raised:
        check_pooling(jobs)
    assert field in str(raised.value)
    assert "two" in str(raised.value)
    if field != "dirty_runner":
        assert "one=" in str(raised.value)
    lifted = check_pooling(jobs, [field])
    assert lifted.overrides_used == (field,)
    assert lifted.findings[0].message


@pytest.mark.parametrize(
    ("change", "one", "two"), [("agent", "h1", "h2"), ("workflow", "w1", "w2")]
)
def test_pooling_refuses_a_different_subject_but_the_override_lifts_it(
    change: str, one: str, two: str
) -> None:
    jobs = [_job("one"), _job("two", **{change: two})]
    with pytest.raises(PoolingError, match=rf"subject differs.*one=.*{one}.*two=.*{two}"):
        check_pooling(jobs)
    assert check_pooling(jobs, ["subject"]).overrides_used == ("subject",)


@pytest.mark.parametrize("field", ["packages", "primitive_code"])
def test_pooling_refuses_different_package_or_primitive_identity_and_no_override_lifts_it(
    field: str,
) -> None:
    jobs = [_job("one"), _job("two", **{field: "s2"})]
    with pytest.raises(PoolingError, match=rf"{field} differs.*one=.*s1.*two=.*s2") as refused:
        check_pooling(jobs)
    assert "nothing lifts it" in str(refused.value)
    with pytest.raises(PoolingError, match=f"{field} is never overridable"):
        check_pooling(jobs, [field])


def test_pooling_refuses_a_different_epoch_with_pre_epoch_as_missing() -> None:
    jobs = [_job("old", {}), _job("new")]
    with pytest.raises(PoolingError, match=r"mechanics_epoch differs.*old=pre-epoch"):
        check_pooling(jobs)


def test_timing_and_cost_across_target_hardware_are_refused_unless_lifted() -> None:
    other = _manifest()
    other["execution_target"] = {"name": "laptop", "docker": {"os": "linux", "arch": "arm64"}}
    jobs = [_job("one"), _job("two", other)]
    with pytest.raises(PoolingError, match="timing_cost differs"):
        check_pooling(jobs)
    report = check_pooling(jobs, ["timing_cost"])
    assert report.timing_cost_comparable is False
    assert report.overrides_used == ("timing_cost",)


def test_pre_epoch_jobs_are_unmeasured_not_refused() -> None:
    jobs = [_job("old-1", {}), _job("old-2", {})]
    assert check_pooling(jobs).unmeasured == ("old-1", "old-2")
    assert check_pooling(jobs[:1]).unmeasured == ("old-1",)


def test_epoch_jobs_without_execution_identity_need_an_explicit_pool_override() -> None:
    manifest = _manifest()
    del manifest["runner"]["execution_sha256"]
    jobs = [_job("one", manifest), _job("two", manifest)]
    with pytest.raises(PoolingError, match="execution code identity is unrecorded.*one, two"):
        check_pooling(jobs)
    assert check_pooling(jobs, ["execution_code"]).overrides_used == ("execution_code",)
    assert check_pooling(jobs[:1]).findings == ()


def test_a_damaged_or_unsealed_trial_refuses_even_with_every_override() -> None:
    every = ["mechanics_epoch", "toolchain", "timing_cost"]
    damaged = _job("solo", seal=DAMAGED)
    with pytest.raises(PoolingError, match=r"never overridable.*solo: trial t1: recorded files"):
        check_pooling([damaged], every)
    unsealed = _job("solo", records=(_record(TRIAL, "a", PARITY), _record("t2", "a", PARITY)))
    with pytest.raises(PoolingError, match=r"solo: trial t2: trial has no records manifest"):
        check_pooling([unsealed], every)


def test_a_pre_epoch_job_has_no_manifest_to_tamper_with() -> None:
    old = _job("old", {}, records=(_record(TRIAL, "a", PARITY),))
    assert check_pooling([old]).unmeasured == ("old",)


def test_tamper_is_not_an_override_name() -> None:
    with pytest.raises(PoolingError, match="never overridable"):
        check_pooling([_job("solo")], ["tamper"])


def _parity_pair(two: ParityRecord | None) -> list[JobDir]:
    return [
        _job("one", records=(_record(TRIAL, "a", PARITY),)),
        _job("two", records=(_record(TRIAL, "a", two),)),
    ]


def test_pooling_reports_the_shared_capabilities_and_parity() -> None:
    report = check_pooling(_parity_pair(PARITY))

    assert report.capabilities == CAPABILITIES
    shared = report.parity["scb/forge@x"]
    assert (shared.trials, shared.record["image_id"]) == (2, "sha256:image")


@pytest.mark.parametrize(
    ("two", "message"),
    [({**CAPABILITIES, "isolation_proof": "partial"}, r"different capabilities.*isolation_proof"),
     (None, "no capability declaration recorded for arm.*two/a"),
     ({k: v for k, v in CAPABILITIES.items() if k != "network"}, "lacks network")],
    ids=["different", "unrecorded", "incomplete"],
)  # fmt: skip
def test_arms_must_share_a_complete_capability_declaration_with_no_override(
    two: dict[str, str] | None, message: str
) -> None:
    jobs = [_job("one"), _job("two", capabilities=two)]

    with pytest.raises(PoolingError, match=message):
        check_pooling(jobs)
    with pytest.raises(PoolingError, match="capabilities is never overridable"):
        check_pooling(jobs, ["capabilities"])


def _harness_jobs(**two: Any) -> list[JobDir]:
    """Two jobs whose experiment declares a harness comparison; ``two`` shapes the second."""
    return [
        _job("one", varies=["harness"], canonical_model="m", reasoning="low"),
        _job(
            "two",
            **{
                "capabilities": OTHER_HARNESS,
                "varies": ["harness"],
                "canonical_model": "m",
                "reasoning": "low",
                **two,
            },
        ),
    ]


def test_a_declared_harness_comparison_pools_and_is_reported() -> None:
    report = check_pooling(_harness_jobs())

    assert report.capabilities is None
    assert report.varies == ("harness",)


def test_every_job_must_declare_the_harness_variation() -> None:
    jobs = _harness_jobs(varies=[])

    with pytest.raises(PoolingError, match=r"declare `varies: \[harness\]`"):
        check_pooling(jobs)


@pytest.mark.parametrize(
    ("two", "varies", "message"),
    [({"canonical_model": "other"}, ["harness"], "canonical model"),
     ({"canonical_model": "other"}, ["harness", "model"], None),
     ({"reasoning": "high"}, ["harness"], "reasoning"),
     ({"reasoning": "high"}, ["harness", "reasoning"], None)],
)  # fmt: skip
def test_a_harness_comparison_differing_in_model_or_reasoning_must_declare_it(
    two: dict[str, str], varies: list[str], message: str | None
) -> None:
    jobs = [
        _job("one", varies=varies, canonical_model="m", reasoning="low"),
        _job(
            "two",
            **{"capabilities": OTHER_HARNESS, "varies": varies, "canonical_model": "m",
               "reasoning": "low", **two},
        ),
    ]  # fmt: skip

    if message is None:
        assert check_pooling(jobs).varies == tuple(sorted(varies))
    else:
        with pytest.raises(PoolingError, match=message):
            check_pooling(jobs)


def test_trials_of_a_problem_must_share_a_parity_record() -> None:
    jobs = _parity_pair(PARITY.model_copy(update={"seed_digest": "other-seed"}))

    with pytest.raises(PoolingError, match=r"scb/forge@x: trials differ in seed_digest") as refused:
        check_pooling(jobs)
    assert "nothing lifts it" in str(refused.value)


def test_a_valid_trial_without_a_parity_record_is_refused() -> None:
    with pytest.raises(PoolingError, match="no parity record for trial.*two/t1"):
        check_pooling(_parity_pair(None))
