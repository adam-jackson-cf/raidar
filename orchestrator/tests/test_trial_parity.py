"""A trial's parity record and harness isolation, read from what its steps left behind."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from raidar.engine.trial.parity import agent_network, harness_isolation, parity_record
from tests.execution_support import SEED_DIGEST, write_seed_record

PROCESS = {"user": "agent", "path": "/usr/local/bin:/usr/bin", "tmpdir": None}
ISOLATION = {"flags": ["--ignore-user-config", "--disable", "plugins"]}


def _workflow(trial_dir: Path, step: str, *turns: dict[str, Any]) -> None:
    folder = trial_dir / "steps" / step / "agent" / "workflow"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "workflow.json").write_text(json.dumps({"kind": "raidar-workflow", "turns": turns}))


def _turn(**changes: Any) -> dict[str, Any]:
    return {"harness_isolation": ISOLATION, "agent_process": PROCESS, **changes}


def _task(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "task.toml").write_text('[agent]\nnetwork_mode = "allowlist"\n')
    return folder


def test_the_parity_record_reads_the_seed_and_each_distinct_agent_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIDAR_LIVENESS_IDLE_S", "300")
    write_seed_record(tmp_path, "checkpoint_1")
    _workflow(tmp_path, "checkpoint_1", _turn(), _turn())
    _workflow(tmp_path, "checkpoint_2", _turn(agent_process={**PROCESS, "tmpdir": "/tmp/t"}))

    record = parity_record(
        tmp_path,
        ("checkpoint_1", "checkpoint_2"),
        task_dir=_task(tmp_path / "task"),
        image_id="sha256:i",
        timeouts={"agent_timeout_s": 1800.0},
    )

    assert record.seed_digest == SEED_DIGEST
    assert [process.tmpdir for process in record.agent_process] == [None, "/tmp/t"]
    assert (record.image_id, record.limits["agent_timeout_s"]) == ("sha256:i", 1800.0)
    assert record.limits["liveness"]["idle_s"] == 300.0
    assert record.network == ({"network_mode": "allowlist", "allowed_hosts": []},)


def test_missing_evidence_leaves_the_parity_parts_empty(tmp_path: Path) -> None:
    record = parity_record(
        tmp_path, ("checkpoint_1",), task_dir=_task(tmp_path), image_id=None, timeouts={}
    )

    assert (record.seed_digest, record.agent_process) == (None, ())


def test_harness_isolation_keeps_each_distinct_application(tmp_path: Path) -> None:
    other = {"flags": ["--ignore-user-config"]}
    _workflow(tmp_path, "checkpoint_1", _turn(), _turn(harness_isolation=None))
    _workflow(tmp_path, "checkpoint_2", _turn(), _turn(harness_isolation=other))

    assert harness_isolation(tmp_path, ("checkpoint_1", "checkpoint_2", "checkpoint_3")) == (
        ISOLATION,
        other,
    )


def test_the_network_record_is_each_distinct_agent_phase_policy(tmp_path: Path) -> None:
    (tmp_path / "task.toml").write_text(
        '[agent]\nnetwork_mode = "allowlist"\nallowed_hosts = ["pypi.org", "api.openai.com"]\n'
        '[[steps]]\n[steps.agent]\nnetwork_mode = "allowlist"\n'
        'allowed_hosts = ["api.openai.com", "pypi.org"]\n'
        '[[steps]]\n[steps.agent]\nnetwork_mode = "no-network"\n'
    )

    assert agent_network(tmp_path) == (
        {"network_mode": "allowlist", "allowed_hosts": ["api.openai.com", "pypi.org"]},
        {"network_mode": "no-network", "allowed_hosts": []},
    )
