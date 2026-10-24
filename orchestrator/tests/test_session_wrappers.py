"""continue-on-stall@2 and transient-retry@1: the iteration-3b session wrappers.

- continue-on-stall@2: a no-edit refusal stalls like a question (the
  ``smoke-03`` r2 messages @1 missed); only a step's first session is eligible,
  so a gate's fix sessions are recorded and never continued;
- transient-retry@1: a session the provider drops (Harbor's transient API
  errors) is run again, at most twice, after a wait; other errors pass through;
  every attempt's logs, usage and error are kept;
- both compose with guardrail-gate@4 in condition order:
  gate -> continue-on-stall@2 -> transient-retry@1 -> native agent.

The wrappers run here against a local bash environment; the real-container
proof is ``test_guardrails_v4_trial.py``.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

import pytest
from harbor.agents.base import BaseAgent
from harbor.agents.installed.base import ApiOverloadedError, NonZeroAgentExitCodeError
from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.models.agent.context import AgentContext

from raidar.components import ComponentKind, ComponentRegistry, ComponentRejectedError
from raidar.components.catalog.gate import GUARDRAIL_GATE_IMPORT_PATH
from raidar.components.catalog.retry import TRANSIENT_RETRY_IMPORT_PATH, retry_records
from raidar.components.catalog.stall import CONTINUE_ON_STALL_IMPORT_PATH
from raidar.components.definition.evidence import StepEvidence
from raidar.components.runtime import transient_retry
from raidar.components.runtime.continue_on_stall import (
    STALL_DIR,
    STALL_RECORD,
    ContinueOnStallAgent,
    continuation_prompt,
    detect_stall_message,
)
from raidar.components.runtime.session_chain import InnerAgentSpec
from raidar.components.runtime.transient_retry import (
    RETRY_DIR,
    RETRY_RECORD,
    TransientRetryAgent,
    is_retryable,
    retry_prompt,
)
from raidar.execution.agent_config import arm_agent_config, reported_harness_pin
from raidar.packs.python.models import load_pack_spec
from raidar.spec.schema import AgentEntry
from tests.component_support import compile_uses, use

GATE = {"command": "guardrail-check", "mode": "commit", "max_rounds": 3, "timeout_sec": 300}
SMOKE_03_CP3 = (
    "`/app` contains no program source or existing CLI to extend—only `mypy.ini` and "
    "`ruff.toml`. The specification refers to previously defined behavior, but that behavior "
    "and the data-store format aren’t present. I can’t build a compatible `forge` CLI "
    "without that context."
)
SMOKE_03_CP2 = (
    "`/app` contains only `mypy.ini` and `ruff.toml`; there’s no existing program or CLI "
    "to extend. The specification doesn’t define how blueprints are stored or how `forge` "
    "is invoked, so I can’t implement blueprint lookup and allocation persistence without "
    "making those choices up."
)


# ---- continue-on-stall@2 detection ----------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (SMOKE_03_CP3, True),
        (SMOKE_03_CP2, True),
        ("I cannot proceed without the storage format.", True),
        ("There is not enough context to implement the lookup.", True),
        ("The output format is not defined in the specification.", True),
        ("Which format do you want?", True),
        ("Implemented the CLI; all tests pass.", False),
        ("Added validation so a unit cannot be created without a host.", False),
        ("", False),
    ],
)
def test_a_no_edit_refusal_stalls_like_a_question(message: str, expected: bool) -> None:
    assert bool(detect_stall_message(message)) is expected


def test_the_v2_continuation_covers_a_refusal_and_asks_for_assumptions() -> None:
    prompt = continuation_prompt("Build the CLI.", SMOKE_03_CP3)

    assert "could not\nproceed without more information" in prompt
    assert "No human is available" in prompt
    assert "ASSUMPTIONS.md" in prompt
    assert "Build the CLI." in prompt


# ---- the components and their nesting ----------------------------------------------------

AGENT = AgentEntry.model_validate(
    {
        "id": "codex-luna",
        "name": "codex",
        "provider": "openai",
        "model": "gpt-6-luna",
        "reasoning_effort": "low",
        "version": "0.156.1",
    }
)


def test_both_are_workflow_session_wrappers_without_with_keys() -> None:
    registry = ComponentRegistry()
    for ref in ("continue-on-stall@2", "transient-retry@1"):
        assert registry.get(ref).manifest.kind is ComponentKind.WORKFLOW
        with pytest.raises(ComponentRejectedError):
            compile_uses(use(ref, retries=3))
    assert "raidar/components/runtime/continue_on_stall.py" in (
        registry.get("continue-on-stall@2").manifest.fingerprint
    )
    assert "raidar/components/runtime/transient_retry.py" in (
        registry.get("transient-retry@1").manifest.fingerprint
    )


def test_the_chain_is_gate_then_stall_then_retry_then_the_native_agent() -> None:
    compiled = compile_uses(
        use("prompt-wrapper@1", ("components/probe/wrapper.md",)),
        use("continue-on-stall@2"),
        use("transient-retry@1"),
        use("guardrail-gate@4", **GATE),
    )
    config = arm_agent_config(AGENT, compiled, load_pack_spec(), agent_timeout_sec=1800)

    assert config.import_path == GUARDRAIL_GATE_IMPORT_PATH
    stall = InnerAgentSpec.model_validate(config.kwargs["inner_agent"])
    assert stall.import_path == CONTINUE_ON_STALL_IMPORT_PATH
    retry = stall.nested()
    assert retry is not None
    assert retry.import_path == TRANSIENT_RETRY_IMPORT_PATH
    assert (retry.native().name, reported_harness_pin(config)) == ("codex", "0.156.1")
    assert stall.template() is not None


def test_the_retry_probe_needs_a_record_and_counts_retries(tmp_path: Path) -> None:
    compiled = compile_uses(use("transient-retry@1"))
    definition, context = compiled.uses[0].definition, compiled.uses[0].context
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()

    assert not definition.probe(context, StepEvidence(agent_dir), 0).passed
    for stage, retries in (("stage_1_work", 1), ("stage_2_fix", 0)):
        folder = agent_dir / "guardrail-gate" / stage / RETRY_DIR
        folder.mkdir(parents=True)
        record = {"retries": retries, "recovered": retries > 0}
        (folder / RETRY_RECORD).write_text(json.dumps(record))

    outcome = definition.probe(context, StepEvidence(agent_dir), 0)
    assert outcome.passed
    assert "2 session(s) recorded; 1 retr(ies), 1 recovered" in outcome.detail
    assert len(retry_records(StepEvidence(agent_dir))) == 2


# ---- running the wrappers against a local environment ------------------------------------


@dataclass
class _Capabilities:
    mounted: bool = True


@dataclass
class LocalEnvironment:
    """Runs each exec with bash on the host (logs and workspace are tmp dirs)."""

    workspace: Path
    capabilities: _Capabilities = field(default_factory=_Capabilities)

    async def exec(self, command: str, **_: Any) -> ExecResult:
        done = subprocess.run(
            ["bash", "-c", command], cwd=self.workspace, capture_output=True, text=True, check=False
        )
        return ExecResult(stdout=done.stdout, stderr=done.stderr, return_code=done.returncode)


class ScriptedNative(BaseAgent):
    """Each session pops the next (workspace file or None, final message, error class or None)."""

    script: ClassVar[list[tuple[str | None, str, type[Exception] | None]]] = []
    prompts: ClassVar[list[str]] = []

    @staticmethod
    def name() -> str:
        return "scripted-native"

    def version(self) -> str | None:
        return "1"

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        type(self).prompts.append(instruction)
        write, message, error = type(self).script.pop(0)
        logs = Path(str(self.environment_logs_dir))
        logs.mkdir(parents=True, exist_ok=True)
        event = {"type": "item.completed", "item": {"type": "agent_message", "text": message}}
        (logs / "codex.txt").write_text(json.dumps(event) + "\n")
        if write is not None:
            workspace = Path(environment.workspace)  # type: ignore[attr-defined]
            (workspace / write).write_text("x\n")
        if error is not None:
            raise error("Selected model is at capacity. Please try a different model.")

    def populate_context_post_run(self, context: AgentContext) -> None:
        context.n_input_tokens, context.n_output_tokens = 10, 1
        context.n_cache_tokens, context.cost_usd = 0, 0.5


NATIVE = {"import_path": "tests.test_session_wrappers:ScriptedNative"}


@pytest.fixture
def no_wait(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waits: list[float] = []

    async def record(_: TransientRetryAgent, seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(TransientRetryAgent, "_sleep", record)
    ScriptedNative.prompts.clear()
    return waits


def _retry_agent(logs: Path, **kwargs: Any) -> TransientRetryAgent:
    return TransientRetryAgent(
        logs_dir=logs,
        model_name="openai/gpt-6-luna",
        environment_logs_dir=PurePosixPath(str(logs)),
        inner_agent=NATIVE,
        **kwargs,
    )


def _run(agent: BaseAgent, environment: LocalEnvironment) -> AgentContext:
    context = AgentContext()
    asyncio.run(agent.run("Build it.", environment, context))  # type: ignore[arg-type]
    return context


def test_an_overloaded_session_is_retried_and_every_attempt_is_kept(
    tmp_path: Path, no_wait: list[float]
) -> None:
    ScriptedNative.script = [
        ("partial.txt", "", ApiOverloadedError),
        (None, "", ApiOverloadedError),
        ("done.txt", "Done.", None),
    ]
    logs, app = tmp_path / "logs", tmp_path / "app"
    logs.mkdir()
    app.mkdir()

    _run(_retry_agent(logs), LocalEnvironment(app))

    assert no_wait == [60.0, 180.0]
    assert ScriptedNative.prompts[0] == "Build it."
    assert all(prompt == retry_prompt("Build it.") for prompt in ScriptedNative.prompts[1:])
    assert "cut off by the model provider" in ScriptedNative.prompts[1]
    record = json.loads((logs / RETRY_DIR / RETRY_RECORD).read_text())
    assert (record["retries"], record["recovered"]) == (2, True)
    assert [a["error_class"] for a in record["attempts"]] == [
        "ApiOverloadedError",
        "ApiOverloadedError",
        None,
    ]
    assert [a["logs"] for a in record["attempts"]] == ["attempt_1", "attempt_2", "."]
    assert (logs / RETRY_DIR / "attempt_1" / "codex.txt").is_file()
    assert {p.name for p in app.iterdir()} == {"partial.txt", "done.txt"}

    # An outer wrapper reads the session's usage through a fresh instance on its logs.
    context = AgentContext()
    _retry_agent(logs).populate_context_post_run(context)
    assert (context.n_input_tokens, context.cost_usd) == (30, 1.5)
    root = json.loads((logs / "trajectory.json").read_text())
    assert root["agent"]["name"] == "raidar-transient-retry"


def test_other_errors_and_the_last_retrys_error_pass_through_unchanged(
    tmp_path: Path, no_wait: list[float]
) -> None:
    for name, script, expected in (
        ("other", [(None, "", NonZeroAgentExitCodeError)], 0),
        ("exhausted", [(None, "", ApiOverloadedError)] * 3, 2),
    ):
        ScriptedNative.script = list(script)
        logs, app = tmp_path / name / "logs", tmp_path / name / "app"
        logs.mkdir(parents=True)
        app.mkdir()
        error = ApiOverloadedError if name == "exhausted" else NonZeroAgentExitCodeError
        with pytest.raises(error):
            _run(_retry_agent(logs), LocalEnvironment(app))
        record = json.loads((logs / RETRY_DIR / RETRY_RECORD).read_text())
        assert (record["retries"], record["recovered"]) == (expected, False)
    assert is_retryable(ApiOverloadedError("x"))
    assert not is_retryable(NonZeroAgentExitCodeError("x"))


def _stall_agent(logs: Path, app: Path) -> ContinueOnStallAgent:
    return ContinueOnStallAgent(
        logs_dir=logs,
        model_name="openai/gpt-6-luna",
        environment_logs_dir=PurePosixPath(str(logs)),
        inner_agent=NATIVE,
        workspace=str(app),
    )


def test_the_steps_first_session_that_refuses_is_continued(
    tmp_path: Path, no_wait: list[float]
) -> None:
    ScriptedNative.script = [(None, SMOKE_03_CP3, None), ("forge", "Built it.", None)]
    logs, app = tmp_path / "logs", tmp_path / "app"
    logs.mkdir()
    app.mkdir()

    _run(_stall_agent(logs, app), LocalEnvironment(app))

    record = json.loads((logs / STALL_DIR / STALL_RECORD).read_text())
    assert (record["eligible"], record["stalled"], record["continued"]) == (True, True, True)
    assert record["session"]["matched"] == ["cannot-without", "not-defined"]
    assert record["continuation"]["changed"] is True
    assert record["component"] == "continue-on-stall@2"
    assert "could not\nproceed" in ScriptedNative.prompts[-1]


def test_a_later_session_of_the_step_is_recorded_but_never_continued(
    tmp_path: Path, no_wait: list[float]
) -> None:
    ScriptedNative.script = [(None, "Should I keep the old flag?", None)]
    logs, app = tmp_path / "logs", tmp_path / "app"
    earlier = logs / "guardrail-gate" / "stage_1_work" / STALL_DIR
    earlier.mkdir(parents=True)
    (earlier / STALL_RECORD).write_text(json.dumps({"stalled": False}))
    app.mkdir()

    _run(_stall_agent(logs, app), LocalEnvironment(app))

    record = json.loads((logs / STALL_DIR / STALL_RECORD).read_text())
    assert (record["eligible"], record["would_stall"]) == (False, True)
    assert (record["stalled"], record["continued"]) == (False, False)
    assert len(ScriptedNative.prompts) == 1
    assert ScriptedNative.script == []
    assert transient_retry.MAX_RETRIES == 2
