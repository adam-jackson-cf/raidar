"""Agent-end reap (F3): no agent process survives into artifact collection and grading."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from harbor.trial.hooks import TrialEvent

from raidar.contract.trial import ProbeSummary
from raidar.engine.health.reaper import (
    REAP_REPORT,
    install_agent_reaper,
    load_reap_report,
    parse_reap_output,
    reap_agent_processes,
    reap_infra_reason,
)
from raidar.engine.trial.collect import _infra_reasons
from raidar.grading.records import step_record_dir

KILLED_OUTPUT = (
    "@@RAIDAR_KILLED\n"
    "   17  1000 Ss   bash -c codex exec | tee /logs/agent/codex.txt\n"
    "   23  1000 S    tee /logs/agent/codex.txt\n"
    "@@RAIDAR_RESIDUAL\n"
)


class FakeEnv:
    def __init__(
        self, stdout: str, *, raises: Exception | None = None, returncode: int = 0, stderr: str = ""
    ) -> None:
        self.stdout = stdout
        self.raises = raises
        self.returncode, self.stderr = returncode, stderr
        self.calls: list[dict[str, Any]] = []

    async def exec(self, command: str, **kwargs: Any) -> Any:
        self.calls.append({"command": command, **kwargs})
        if self.raises is not None:
            raise self.raises
        return SimpleNamespace(stdout=self.stdout, stderr=self.stderr, return_code=self.returncode)


def test_reap_output_lists_killed_and_residual_processes() -> None:
    report = parse_reap_output("agent", KILLED_OUTPUT + "   99  1000 S  sleep 999\n")
    assert len(report.killed) == 2
    assert report.residual == ("99  1000 S  sleep 999",)
    assert report.error is None
    assert reap_infra_reason(report.as_json()) == "agent_reap_residual:1"


def test_a_clean_reap_is_not_an_infra_reason() -> None:
    report = parse_reap_output("agent", KILLED_OUTPUT)
    assert reap_infra_reason(report.as_json()) is None
    assert reap_infra_reason(None) is None  # runs from before the reaper existed


def test_a_refused_or_truncated_reap_is_infra() -> None:
    refused = parse_reap_output("root", "@@RAIDAR_ERROR agent user is root\n")
    assert reap_infra_reason(refused.as_json()) == "agent_reap_failed:agent user is root"
    truncated = parse_reap_output("agent", "@@RAIDAR_KILLED\n")
    assert truncated.error is not None


def test_reap_runs_as_root_and_never_raises() -> None:
    env = FakeEnv(KILLED_OUTPUT)
    report = asyncio.run(reap_agent_processes(env, "agent"))
    assert report.error is None
    assert env.calls[0]["user"] == "root"

    assert report.killed == tuple(line.strip() for line in KILLED_OUTPUT.splitlines()[1:3])
    assert report.residual == ()


@pytest.mark.parametrize(
    ("raises", "stderr", "returncode", "reason"),
    [
        (RuntimeError("gone"), "", 0, "reap exec failed: RuntimeError: gone"),
        (TimeoutError("deadline"), "", 0, "reap exec failed: TimeoutError: deadline"),
        (None, "permission denied", 1, "reap did not complete"),
        (None, "", 0, "reap did not complete"),
    ],
    ids=["exec-error", "timeout", "nonzero", "malformed"],
)
def test_reap_failure_becomes_infrastructure_evidence(
    raises: Exception | None, stderr: str, returncode: int, reason: str
) -> None:
    env = FakeEnv("malformed", raises=raises, stderr=stderr, returncode=returncode)
    report = asyncio.run(reap_agent_processes(env, "agent"))
    assert reason in str(report.error)
    assert report.killed == report.residual == ()
    assert str(reap_infra_reason(report.as_json())).startswith("agent_reap_failed:")


def test_reaper_hook_writes_the_step_report_after_every_agent_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = SimpleNamespace(resolve_step=lambda name: name)
    monkeypatch.setattr("raidar.engine.health.reaper.load_bundle", lambda root: bundle)
    hooks: dict[TrialEvent, list[Any]] = {}
    trial = SimpleNamespace(
        task=SimpleNamespace(config=SimpleNamespace(agent=SimpleNamespace(user="agent"))),
        agent_environment=FakeEnv(KILLED_OUTPUT + "   5  1000 S  codex\n"),
        paths=SimpleNamespace(trial_dir=tmp_path / "trial"),
        add_hook=lambda event, hook: hooks.setdefault(event, []).append(hook),
    )
    install_agent_reaper(cast(Any, trial), tmp_path / "bundle")
    event = SimpleNamespace(
        result=SimpleNamespace(step_results=[SimpleNamespace(step_name="checkpoint_2")])
    )
    asyncio.run(hooks[TrialEvent.AGENT_END][0](event))

    record_dir = step_record_dir(tmp_path / "trial", "checkpoint_2")
    assert (record_dir / REAP_REPORT).is_file()
    reason = reap_infra_reason(load_reap_report(record_dir))
    assert reason == "agent_reap_residual:1"
    probes = ProbeSummary(passed=True, failures=())
    assert _infra_reasons(None, "AgentTimeoutError", probes, reason)[0] == reason
