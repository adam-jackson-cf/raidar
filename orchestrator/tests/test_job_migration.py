"""``raidar migrate-job``: a legacy job moves onto the v2 lock only when its execution is equal."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from raidar.agents.config import agent_cli
from raidar.cli import main
from raidar.execution.job_migration import (
    LEGACY_DIR,
    differences,
    translate_execution,
    translate_trial_record,
)
from raidar.execution.models import TrialRecord
from raidar.job_layout import is_legacy_job
from raidar.spec.canonical import digest_json
from raidar.spec.loader import load_experiment
from raidar.spec.lock import SECTIONS, build_lock, render_lock
from raidar.spec.metrics import MetricResolver
from raidar.spec.references import RefKind, ResolverSet
from tests.experiment_spec_support import (
    LEGACY_V1,
    PILOT_EXPERIMENT,
    PILOT_ONESHOT,
    InMemoryResolver,
    experiment_document,
    fixture_resolvers,
    write_experiment,
)

LEGACY_ID = "fixture"


# ---- the committed pilot locks -------------------------------------------------


@pytest.mark.parametrize(
    ("legacy", "current"),
    [
        ("pilot-forge.experiment.lock.json", PILOT_EXPERIMENT),
        ("pilot-circuit-eval-oneshot.experiment.lock.json", PILOT_ONESHOT),
    ],
)
def test_the_pilot_locks_translate_to_the_v2_locks_field_for_field(
    legacy: str, current: Path
) -> None:
    """The executed pilot locks (5a73637e8ac3, 827ebb5a7ca9) describe the v2 pilot experiments.

    This also pins the released components: editing a fingerprint source changes a
    component digest, and the translated execution would no longer match.
    """
    executed = json.loads((LEGACY_V1 / legacy).read_text())
    lock = json.loads(current.with_name("experiment.lock.json").read_text())
    design = lock["execution"]["design"]

    translated = translate_execution(
        executed["execution"], kind=design["kind"], experiment_id=design["id"]
    )

    assert differences(translated, lock["execution"]) == []
    assert digest_json(translated) == lock["lock_sha256"]
    assert executed["lock_sha256"] != lock["lock_sha256"]


def test_a_real_pilot_trajectory_record_translates_to_a_valid_trial_record() -> None:
    legacy = json.loads(
        (LEGACY_V1 / "pilot-forge.forge-03.b07-p0-baseline.trajectory.json").read_text()
    )

    record = TrialRecord.model_validate(
        translate_trial_record(legacy, experiment_id="python__forge__guided-pilot")
    )

    assert (record.job_id, record.trial.attempt, record.trial.trial_id) == (
        "forge-03",
        7,
        "b07-p0-baseline",
    )
    assert [result.attempt for result in record.step_results] == [7] * 8
    assert [r.verifier_record for r in record.step_results] == [
        a["oracle_record"] for a in legacy["attempts"]
    ]
    checks = {check.name for check in record.validity.checks}
    assert {"no_verifier_leak", "verifier_verdicts", "agent_version"} <= checks
    assert record.valid == all(check["passed"] for check in legacy["validity"]["checks"])
    assert [r.rewards for r in record.step_results] == [a["rewards"] for a in legacy["attempts"]]


# ---- synthetic legacy jobs -----------------------------------------------------


def _current(path: Path) -> dict[str, Any]:
    _, component, scorer = fixture_resolvers()
    problem = InMemoryResolver(
        RefKind.PROBLEM, {"scb/forge@abc123": {"checkpoints": 8}}, tool_versions={"scb-check": "1"}
    )
    resolvers = ResolverSet([problem, component, scorer, MetricResolver()], entry_point_group=None)
    lock_path = path.with_name("experiment.lock.json")
    rendered = render_lock(
        build_lock(load_experiment(path), lock_path=lock_path, resolvers=resolvers)
    )
    lock_path.write_bytes(rendered)
    return json.loads(rendered)


def _v1_agent(agent: dict[str, Any]) -> dict[str, Any]:
    downgraded = {key: value for key, value in agent.items() if key != "name"}
    downgraded["harness"] = agent_cli(agent["name"]).value
    return downgraded


def _v1_spec(spec: dict[str, Any]) -> dict[str, Any]:
    environment = spec["environment"]
    v1 = {
        key: value
        for key, value in spec.items()
        if key not in {"kind", "runtime", "environment", "agents", "factors"}
    }
    v1.update(
        schema_version=1,
        id=LEGACY_ID,
        runtime={
            "language": spec["runtime"],
            "environment": environment["type"],
            "compose_init": environment["compose_init"],
        },
        agents=[_v1_agent(agent) for agent in spec["agents"]],
        factors=[{"agent.name": "agent.harness"}.get(f, f) for f in spec["factors"]],
    )
    assessment = dict(spec["assessment"])
    assessment["headline_policy"] = assessment.pop("metric", None)
    analysis = dict(spec["analysis"])
    analysis["blocks"] = analysis.pop("attempts")
    v1.update(assessment=assessment, analysis=analysis)
    return v1


def _seal(body: dict[str, Any]) -> dict[str, Any]:
    sections = {name: digest_json(body[name]) for name in SECTIONS}
    unsigned = {**body, "sections": sections}
    return {
        **unsigned,
        "lock_sha256": sections["execution"],
        "document_sha256": digest_json(unsigned),
    }


def _apply(target: dict[str, Any], changes: dict[str, Any]) -> None:
    for dotted, value in changes.items():
        *parents, leaf = dotted.split(".")
        node = target
        for key in parents:
            node = node[key]
        node[leaf] = value


def _legacy_execution(current: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    execution = json.loads(json.dumps(current["execution"]))
    execution["design"] = {
        **{k: spec[k] for k in ("id", "problems", "runtime", "agents", "protocol", "factors")},
        **{k: spec[k] for k in ("conditions", "arms")},
        "blocks": spec["analysis"]["blocks"],
        "seed": spec["analysis"]["seed"],
    }
    agents = {agent["id"]: agent for agent in spec["agents"]}
    for record in execution["arms"].values():
        agent = {k: v for k, v in agents[record["agent"]].items() if k != "id"}
        condition = execution["conditions"][record["condition"]]
        record["digest"] = digest_json({"agent": agent, "condition": condition})
    for record in execution["references"].get("component", {}).values():
        supports = record["metadata"].get("supports")
        if supports is not None:
            record["metadata"]["supports"] = [agent_cli(name).value for name in supports]
    execution["versions"] = {
        "harbor": execution["versions"]["harbor"],
        "harnesses": {
            agent_id: {"harness": agent_cli(entry["name"]).value, "version": entry["version"]}
            for agent_id, entry in execution["versions"]["agents"].items()
        },
    }
    return execution


def _legacy_lock(current: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """The lock version 2 (schema v1 vocabulary) a pre-v2 Raidar wrote for ``current``."""
    spec = _v1_spec(current["spec"])
    execution = _legacy_execution(current, spec)
    _apply(execution, changes)
    body = {
        "lock_version": 2,
        "experiment": {**current["experiment"], "id": LEGACY_ID},
        "overrides": current["overrides"],
        "spec": spec,
        "execution": execution,
        "assessment": current["assessment"],
        "analysis": current["analysis"],
        "toolchain": current["toolchain"],
    }
    return _seal(body)


def _legacy_trial(lock_sha: str, trial_id: str) -> dict[str, Any]:
    hashes = dict.fromkeys(("problem", "arm", "condition", "agent", "protocol", "trial"), "x")
    return {
        "trial_id": trial_id,
        "block": 0,
        "order": 0,
        "sequence": 0,
        "arm": "baseline",
        "problem": "scb/forge@abc123",
        "problem_index": 0,
        "steps": ["checkpoint_1"],
        "hashes": {**hashes, "lock": lock_sha},
    }


def _legacy_record(lock_sha: str) -> dict[str, Any]:
    trial_id = "b00-p0-baseline"
    step = {
        "kind": "raidar-checkpoint-attempt",
        "schema_version": 1,
        "trial_id": trial_id,
        "arm": "baseline",
        "block": 0,
        "problem": "scb/forge@abc123",
        "index": 0,
        "step": "checkpoint_1",
        "checkpoint": "checkpoint_1",
        "checkpoint_state": "infra_error",
        "infra_reasons": ["oracle_leak", "oracle_infra:timeout"],
        "rewards": {"oracle_leak": 1.0},
        "oracle_record": "raidar-oracle/steps/checkpoint_1/record.json",
    }
    return {
        "kind": "raidar-trajectory",
        "schema_version": 1,
        "experiment": LEGACY_ID,
        "run_id": "run-1",
        "trial": _legacy_trial(lock_sha, trial_id),
        "trial_dir": f"trials/{trial_id}",
        "progression": {"mode": "continue", "reward_key": "strict_pass_rate", "threshold": None},
        "attempts": [step],
        "reached": [True],
        "reached_source": "derived",
        "validity": {
            "checks": [
                {"name": "no_oracle_leak", "passed": False, "detail": "checkpoint_1: oracle_leak"},
                {"name": "harness_version", "passed": True, "detail": ""},
            ]
        },
        "phases": [{"phase": "oracle_run", "seconds": 1.0, "step": "checkpoint_1"}],
    }


def _legacy_job(root: Path, executed: dict[str, Any]) -> Path:
    job_dir = root / "runs" / LEGACY_ID / "run-1"
    (job_dir / "trajectories").mkdir(parents=True)
    (job_dir / "trials" / "b00-p0-baseline").mkdir(parents=True)
    (job_dir / "trials" / "b00-p0-baseline" / "result.json").write_text("{}")
    lock_sha = executed["lock_sha256"]
    manifest = {
        "kind": "raidar-run",
        "experiment": LEGACY_ID,
        "run_id": "run-1",
        "lock_sha256": lock_sha,
        "summary": {"trajectories": 1, "valid": 0, "checkpoint_states": {"infra_error": 1}},
    }
    (job_dir / "run.json").write_text(json.dumps(manifest))
    (job_dir / "run.lock.json").write_text(json.dumps(executed, indent=2))
    plan = {
        "kind": "raidar-plan",
        "experiment": LEGACY_ID,
        "lock_sha256": lock_sha,
        "options": {"arms": [], "blocks": [0], "max_checkpoints": None, "agent_override": None},
        "trials": [_legacy_trial(lock_sha, "b00-p0-baseline")],
    }
    (job_dir / "plan.json").write_text(json.dumps(plan))
    record = _legacy_record(lock_sha)
    (job_dir / "trajectories" / "b00-p0-baseline.json").write_text(json.dumps(record))
    return job_dir


def _migrate(job_dir: Path, lock: Path, *extra: str) -> Any:
    return CliRunner().invoke(main, ["migrate-job", str(job_dir), "--lock", str(lock), *extra])


@pytest.fixture
def experiment(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    path = write_experiment(tmp_path / "experiment", experiment_document())
    return path.with_name("experiment.lock.json"), _current(path)


def test_a_legacy_job_moves_onto_the_v2_lock_with_provenance(
    tmp_path: Path, experiment: tuple[Path, dict[str, Any]]
) -> None:
    lock_path, current = experiment
    executed = _legacy_lock(current)
    job_dir = _legacy_job(tmp_path, executed)
    names = ("run.json", "run.lock.json", "plan.json")
    legacy_bytes = {name: (job_dir / name).read_bytes() for name in names}
    trial_bytes = (job_dir / "trials" / "b00-p0-baseline" / "result.json").read_bytes()

    first = _migrate(job_dir, lock_path)
    moved = tmp_path / "runs" / current["experiment"]["id"] / "run-1"
    again = _migrate(moved, lock_path)

    assert first.exit_code == 0, first.output
    assert not job_dir.exists()
    assert moved.is_dir()
    manifest = json.loads((moved / "job.json").read_text())
    assert manifest["kind"] == "raidar-job"
    assert (manifest["job_id"], manifest["lock_sha256"]) == ("run-1", current["lock_sha256"])
    assert manifest["experiment"] == "python__fixture__test"
    assert manifest["summary"]["trials"] == 1
    migration = manifest["job_migration"]
    assert migration["from"]["lock_sha256"] == executed["lock_sha256"]
    assert migration["to"] == {
        "experiment": "python__fixture__test",
        "kind": "ab-test",
        "lock_sha256": current["lock_sha256"],
    }
    for name, data in legacy_bytes.items():
        assert (moved / LEGACY_DIR / name).read_bytes() == data
        assert migration["legacy_sha256"][name] == hashlib.sha256(data).hexdigest()
    assert (moved / "job.lock.json").read_bytes() == lock_path.read_bytes()
    assert (moved / "trials" / "b00-p0-baseline" / "result.json").read_bytes() == trial_bytes
    record = TrialRecord.model_validate_json(
        (moved / "trial-records" / "b00-p0-baseline.json").read_bytes()
    )
    assert record.step_results[0].infra_reasons == ("verifier_leak", "verifier_infra:timeout")
    assert record.step_results[0].rewards == {"oracle_leak": 1.0}  # verifier wire key kept
    assert [check.name for check in record.validity.checks] == ["no_verifier_leak", "agent_version"]
    assert record.validity.checks[0].detail == "checkpoint_1: verifier_leak"
    assert [phase.phase for phase in record.phases] == ["verifier_run"]
    plan = json.loads((moved / "plan.json").read_text())
    assert plan["options"]["attempts"] == [0]
    assert "blocks" not in plan["options"]
    assert plan["trials"][0]["attempt"] == 0
    assert plan["executed_lock_sha256"] == executed["lock_sha256"]
    # The trials keep the lock digest they executed under; job_migration maps it.
    assert record.trial.hashes.lock == executed["lock_sha256"]
    assert again.exit_code == 0, again.output
    assert "Already migrated" in again.output
    # The archived copy is never mistaken for a legacy job still to migrate.
    assert not is_legacy_job(moved / LEGACY_DIR)
    assert not job_dir.exists()


def test_a_version_one_lock_is_translated_and_proven_too(
    tmp_path: Path, experiment: tuple[Path, dict[str, Any]]
) -> None:
    lock_path, current = experiment
    legacy = _legacy_lock(current)
    execution, assessment = legacy["execution"], legacy["assessment"]
    body: dict[str, Any] = {
        "lock_version": 1,
        "experiment": legacy["experiment"],
        "overrides": legacy["overrides"],
        "spec": legacy["spec"],
        "problems": execution["problems"],
        "files": execution["files"],
        "references": {**execution["references"], **assessment["references"]},
        "conditions": execution["conditions"],
        "arms": execution["arms"],
        "headline_policy": assessment["metric"],
        "versions": {
            **legacy["toolchain"],
            **execution["versions"],
            "tools": {**execution["tools"], **assessment["tools"]},
        },
    }
    executed = {**body, "lock_sha256": digest_json(body)}
    job_dir = _legacy_job(tmp_path, executed)

    result = _migrate(job_dir, lock_path, "--no-relocate")

    assert result.exit_code == 0, result.output
    manifest = json.loads((job_dir / "job.json").read_text())
    assert manifest["job_migration"]["from"]["lock_sha256"] == executed["lock_sha256"]


@pytest.mark.parametrize(
    ("change", "reported"),
    [
        ({"design.blocks": 3}, "execution.design.attempts"),
        ({"versions.harbor": "0.22.0"}, "execution.versions.harbor"),
        ({"tools": {"scb-check": "2"}}, "execution.tools.scb-check"),
        ({"files": {}}, "execution.files"),
    ],
)
def test_a_job_of_another_experiment_is_refused(
    tmp_path: Path,
    experiment: tuple[Path, dict[str, Any]],
    change: dict[str, Any],
    reported: str,
) -> None:
    lock_path, current = experiment
    job_dir = _legacy_job(tmp_path, _legacy_lock(current, **change))

    result = _migrate(job_dir, lock_path)

    assert result.exit_code != 0
    assert reported in result.output
    assert (job_dir / "run.json").is_file()
    assert not (job_dir / "job.json").exists()
    assert not (job_dir / LEGACY_DIR).exists()


def test_an_arm_digest_that_does_not_match_its_inputs_is_refused(
    tmp_path: Path, experiment: tuple[Path, dict[str, Any]]
) -> None:
    lock_path, current = experiment
    executed = _legacy_lock(current, **{"arms.guided.digest": "0" * 64})
    job_dir = _legacy_job(tmp_path, executed)

    result = _migrate(job_dir, lock_path)

    assert result.exit_code != 0
    assert "arm 'guided' digest does not match" in result.output


def test_unverifiable_inputs_are_refused(
    tmp_path: Path, experiment: tuple[Path, dict[str, Any]]
) -> None:
    lock_path, current = experiment
    executed = _legacy_lock(current)
    job_dir = _legacy_job(tmp_path, executed)
    manifest = job_dir / "run.json"
    original = manifest.read_text()

    manifest.write_text(original.replace(executed["lock_sha256"], "0" * 64))
    other_job = _migrate(job_dir, lock_path)
    manifest.write_text(original)
    (job_dir / "run.lock.json").write_text(json.dumps({**executed, "files": {}}))
    edited = _migrate(job_dir, lock_path)
    (job_dir / "run.lock.json").write_text(json.dumps(current))
    current_version = _migrate(job_dir, lock_path)

    assert "was executed under lock" in other_job.output
    assert "not a valid version-2 lock" in edited.output
    assert "only versions (1, 2) are migrated" in current_version.output
    assert all(r.exit_code != 0 for r in (other_job, edited, current_version))


def test_v2_readers_refuse_a_legacy_job_with_a_pointer_to_the_migration(
    tmp_path: Path, experiment: tuple[Path, dict[str, Any]]
) -> None:
    _, current = experiment
    job_dir = _legacy_job(tmp_path, _legacy_lock(current))
    store, output = tmp_path / "store", tmp_path / "analysis"
    store.mkdir()

    analysed = CliRunner().invoke(
        main, ["analyze", str(job_dir), "--store", str(store), "--output", str(output)]
    )
    rescored = CliRunner().invoke(
        main,
        ["assess", "--rescore", str(job_dir / "trials"), "--store", str(store)]
        + ["--scorer", "correctness@1"],
    )

    for result in (analysed, rescored):
        assert result.exit_code != 0
        assert "legacy (pre-schema-v2) job" in result.output
        assert "experiment-migrate-job" in result.output
