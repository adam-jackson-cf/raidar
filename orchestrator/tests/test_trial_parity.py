"""A trial's parity record and harness isolation, decided from parsed step evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from raidar.engine.trial.parity import (
    build_parity,
    distinct_isolations,
    harness_isolation,
    network_policies,
    parity_record,
    record_turns,
)
from tests.execution_support import SEED_DIGEST, write_seed_record

PROCESS = {"user": "agent", "path": "/usr/local/bin:/usr/bin", "tmpdir": None}
ISOLATION = {"flags": ["--ignore-user-config", "--disable", "plugins"]}
LIVENESS = {"idle_s": 300.0}


def _turn(**changes: Any) -> dict[str, Any]:
    return {"harness_isolation": ISOLATION, "agent_process": PROCESS, **changes}


def test_the_parity_record_keeps_each_distinct_agent_process_and_the_limits() -> None:
    turns = [_turn(), _turn(), _turn(agent_process={**PROCESS, "tmpdir": "/tmp/t"}), {"other": 1}]

    record = build_parity(
        turns,
        seed_digest=SEED_DIGEST,
        network=[{"network_mode": "allowlist", "allowed_hosts": []}],
        image_id="sha256:i",
        timeouts={"agent_timeout_s": 1800.0},
        liveness=LIVENESS,
    )

    assert record.seed_digest == SEED_DIGEST
    assert [process.tmpdir for process in record.agent_process] == [None, "/tmp/t"]
    assert (record.image_id, record.limits["agent_timeout_s"]) == ("sha256:i", 1800.0)
    assert record.limits["liveness"]["idle_s"] == 300.0
    assert record.network == ({"network_mode": "allowlist", "allowed_hosts": []},)


def test_missing_evidence_leaves_the_parity_parts_empty() -> None:
    record = build_parity(
        [], seed_digest=None, network=[], image_id=None, timeouts={}, liveness=LIVENESS
    )

    assert (record.seed_digest, record.agent_process, record.network) == (None, (), ())


def test_harness_isolation_keeps_each_distinct_application() -> None:
    other = {"flags": ["--ignore-user-config"]}
    turns = [_turn(), _turn(harness_isolation=None), _turn(), _turn(harness_isolation=other)]

    assert distinct_isolations(turns) == (ISOLATION, other)


@pytest.mark.parametrize("record", [None, [], "text", {"turns": None}, {"turns": 3 * [None]}])
def test_a_malformed_workflow_record_yields_no_turns(record: object) -> None:
    assert list(record_turns(record)) == []


def test_only_mapping_turns_of_a_workflow_record_count() -> None:
    assert list(record_turns({"turns": [_turn(), "x", _turn(agent_process=None)]})) == [
        _turn(),
        _turn(agent_process=None),
    ]


def test_the_network_record_is_each_distinct_agent_phase_policy() -> None:
    reordered = {"network_mode": "allowlist", "allowed_hosts": ["api.openai.com", "pypi.org"]}
    document = {
        "agent": {"network_mode": "allowlist", "allowed_hosts": ["pypi.org", "api.openai.com"]},
        "steps": [{"agent": reordered}, {"agent": {"network_mode": "no-network"}}, {}],
    }

    assert network_policies(document) == (
        {"network_mode": "allowlist", "allowed_hosts": ["api.openai.com", "pypi.org"]},
        {"network_mode": "no-network", "allowed_hosts": []},
        {"network_mode": None, "allowed_hosts": []},
    )
    assert network_policies({}) == ({"network_mode": None, "allowed_hosts": []},)


def test_the_record_reader_gathers_the_seed_turns_and_policies_from_a_few_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIDAR_LIVENESS_IDLE_S", "300")
    write_seed_record(tmp_path, "checkpoint_1")
    workflow = tmp_path / "steps" / "checkpoint_1" / "agent" / "workflow"
    workflow.mkdir(parents=True, exist_ok=True)
    (workflow / "workflow.json").write_text(json.dumps({"turns": [_turn()]}))
    broken = tmp_path / "steps" / "checkpoint_2" / "agent" / "workflow"
    broken.mkdir(parents=True)
    (broken / "workflow.json").write_text("{not json")
    task = tmp_path / "task"
    task.mkdir()
    (task / "task.toml").write_text('[agent]\nnetwork_mode = "allowlist"\n')

    record = parity_record(
        tmp_path,
        ("checkpoint_1", "checkpoint_2", "checkpoint_3"),
        task_dir=task,
        image_id="sha256:i",
        timeouts={},
    )

    assert record.seed_digest == SEED_DIGEST
    assert len(record.agent_process) == 1
    assert record.limits["liveness"]["idle_s"] == 300.0
    assert record.network == ({"network_mode": "allowlist", "allowed_hosts": []},)
    steps = ("checkpoint_1", "checkpoint_2", "checkpoint_3")
    assert harness_isolation(tmp_path, steps) == (ISOLATION,)
    unseeded = parity_record(tmp_path, (), task_dir=task, image_id=None, timeouts={})
    assert (unseeded.seed_digest, unseeded.agent_process) == (None, ())
