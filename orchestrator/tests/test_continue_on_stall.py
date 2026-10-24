"""continue-on-stall@2 and the session chain it rides on, plus the gate's pre-fix record.

- detection: a session that changed nothing and ended asking or refusing (fixed patterns);
- the component: a workflow that contributes no workflow wrapper, so it composes
  with guardrail-gate@4, and is nested by the execution layer around the native
  agent (inside the gate when there is one; the arm's agent otherwise);
- the agent pin and auth are read through every level of wrapping;
- guardrail-gate@4: the pre-fix snapshot record and a failed snapshot.

The real-container proof is ``test_guardrails_trial.py``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from harbor.models.trial.config import AgentConfig

from raidar.contract.snapshot import SNAPSHOT_EXCLUDES
from raidar.design.experiment.schema import AgentEntry
from raidar.engine.trial.agent_config import arm_agent_config, reported_harness_pin, with_host_auth
from raidar.subject.agent.config import Harness
from raidar.subject.environment.python.models import load_pack_spec
from raidar.subject.treatment import ComponentKind, ComponentRegistry
from raidar.subject.treatment.catalog.gate import GUARDRAIL_GATE_IMPORT_PATH
from raidar.subject.treatment.catalog.stall import CONTINUE_ON_STALL_IMPORT_PATH, stall_records
from raidar.subject.treatment.definition.evidence import StepEvidence
from raidar.subject.treatment.runtime.continue_on_stall import (
    ASSUMPTIONS_FILE,
    STALL_DIR,
    STALL_RECORD,
    continuation_prompt,
    detect_stall_message,
    fingerprint_script,
)
from raidar.subject.treatment.runtime.gate_run import GuardrailGateOptions
from raidar.subject.treatment.runtime.guardrail_gate import (
    GuardrailGateAgent,
    PreFixSnapshot,
    _GateState,
    gate_record,
)
from raidar.subject.treatment.runtime.session_chain import (
    InnerAgentSpec,
    apply_session_wrappers,
    condition_session_wrappers,
    render_task,
)
from tests.component_support import compile_uses, use

GATE = {"command": "guardrail-check", "mode": "commit", "max_rounds": 3, "timeout_sec": 300}


# ---- detection -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Should the output be JSON or text? Please confirm before I proceed.", True),
        ("Which format do you want?", True),
        ("I need your confirmation on the output format.", True),
        ("Let me know how you would like the errors reported.", True),
        ("Shall I proceed?**", True),
        ("Implemented the CLI and its tests.", False),
        ("See http://example.invalid/a?b=1 for the reference.", False),
        ("Use the `a?b` operator.", False),
        ("```python\nx = a if b else c  # why?\n```\nDone.", False),
        ("", False),
    ],
)
def test_a_request_is_a_question_or_a_call_for_clarification(message: str, expected: bool) -> None:
    assert bool(detect_stall_message(message)) is expected


def test_the_fingerprint_prunes_the_snapshot_excludes_and_hashes_every_file() -> None:
    script = fingerprint_script("/app")
    for name in SNAPSHOT_EXCLUDES:
        assert f"-name {name}" in script
    assert "sha256sum" in script
    assert "LC_ALL=C sort -z" in script


def test_the_continuation_says_no_human_is_available_and_asks_for_assumptions() -> None:
    prompt = continuation_prompt("Build the CLI.", "Which flag name do you want?")

    assert "No human is available" in prompt
    assert ASSUMPTIONS_FILE in prompt
    assert "Build the CLI." in prompt
    assert "Which flag name do you want?" in prompt
    assert "do not ask again" in prompt


# ---- the component -------------------------------------------------------------------


def test_the_component_is_a_session_wrapper_that_composes_with_the_gate() -> None:
    definition = ComponentRegistry().get("continue-on-stall@2")
    compiled = compile_uses(use("continue-on-stall@2"), use("guardrail-gate@4", **GATE))

    assert definition.manifest.kind is ComponentKind.WORKFLOW
    assert (
        "orchestrator/src/raidar/subject/treatment/runtime/continue_on_stall.py" in definition.manifest.fingerprint
    )
    assert compiled.agent_wrapper is not None
    assert compiled.agent_wrapper.import_path == GUARDRAIL_GATE_IMPORT_PATH
    (wrapper,) = condition_session_wrappers(compiled)
    assert wrapper.import_path == CONTINUE_ON_STALL_IMPORT_PATH
    assert compiled.overlay == {}
    assert compiled.support_files == {}


@pytest.mark.parametrize("bad", [{"max": 2}])
def test_the_component_takes_no_with_keys(bad: dict[str, int]) -> None:
    from raidar.subject.treatment import ComponentRejectedError

    with pytest.raises(ComponentRejectedError):
        compile_uses(use("continue-on-stall@2", **bad))


def _record(path: Path, *, stalled: bool) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / STALL_RECORD).write_text(json.dumps({"stalled": stalled, "continued": stalled}))


def test_the_probe_needs_a_record_and_counts_the_stalls(tmp_path: Path) -> None:
    compiled = compile_uses(use("continue-on-stall@2"))
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "raidar-reset.json").write_text(json.dumps({"kind": "raidar-step-reset"}))

    assert (
        not compiled.uses[0]
        .definition.probe(compiled.uses[0].context, StepEvidence(agent_dir), 0)
        .passed
    )
    _record(agent_dir / "guardrail-gate" / "stage_1_work" / STALL_DIR, stalled=True)
    _record(agent_dir / "guardrail-gate" / "stage_2_fix" / STALL_DIR, stalled=False)

    outcome = compiled.uses[0].definition.probe(
        compiled.uses[0].context, StepEvidence(agent_dir), 0
    )
    assert outcome.passed
    assert "2 session(s) recorded; 1 stalled, 1 continued" in outcome.detail
    assert len(stall_records(StepEvidence(agent_dir))) == 2


# ---- nesting in the arm's agent config ------------------------------------------------

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


def _config(*uses: Any) -> AgentConfig:
    compiled = compile_uses(use("prompt-wrapper@1", ("components/probe/wrapper.md",)), *uses)
    return arm_agent_config(AGENT, compiled, load_pack_spec(), agent_timeout_sec=1800)


def test_inside_the_gate_the_stall_wrapper_wraps_the_native_agent() -> None:
    config = _config(use("continue-on-stall@2"), use("guardrail-gate@4", **GATE))

    assert config.import_path == GUARDRAIL_GATE_IMPORT_PATH
    inner = InnerAgentSpec.model_validate(config.kwargs["inner_agent"])
    assert inner.import_path == CONTINUE_ON_STALL_IMPORT_PATH
    native = inner.native()
    assert (native.name, native.kwargs["version"]) == ("codex", "0.156.1")
    assert inner.template() is not None, "the prompt template stays on the native agent"
    assert inner.without_template().template() is None
    assert reported_harness_pin(config) == "0.156.1"


def test_without_a_workflow_the_stall_wrapper_is_the_arms_agent() -> None:
    config = _config(use("continue-on-stall@2"))

    assert (config.name, config.import_path) == (None, CONTINUE_ON_STALL_IMPORT_PATH)
    assert config.kwargs["inner_agent"]["name"] == "codex"
    assert reported_harness_pin(config) == "0.156.1"
    assert _config().import_path is None, "no session wrapper: the native agent as before"


def test_host_auth_reaches_a_doubly_wrapped_codex(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    resolved = SimpleNamespace(resolved_mode="chatgpt", auth_json_path=auth)
    monkeypatch.setattr("raidar.engine.trial.agent_config.resolve_codex_auth", lambda: resolved)
    config = _config(use("continue-on-stall@2"), use("guardrail-gate@4", **GATE))

    assert str(auth) in with_host_auth(config, Harness.CODEX_CLI).env.values()


def test_the_task_is_rendered_once_with_the_chains_template(tmp_path: Path) -> None:
    template = tmp_path / "t.md"
    template.write_text("FRAME\n{{ instruction }}\n")
    chain = InnerAgentSpec.model_validate(
        {
            "import_path": "x:Y",
            "kwargs": {
                "inner_agent": {"name": "codex", "kwargs": {"prompt_template_path": str(template)}}
            },
        }
    )

    assert render_task(chain, "do it") == "FRAME\ndo it"
    assert render_task(chain.without_template(), "do it") == "do it"
    with pytest.raises(ValueError, match="exactly one"):
        InnerAgentSpec.model_validate({"name": "codex", "import_path": "x:Y"})


def test_apply_nests_the_first_wrapper_outermost() -> None:
    from raidar.subject.treatment.definition.contribution import AgentWrapper

    base = AgentConfig(name="codex", kwargs={"version": "1"})
    config = apply_session_wrappers(
        base, [AgentWrapper("a:A", {"k": 1}), AgentWrapper("b:B", {"k": 2})]
    )

    assert config.import_path == "a:A"
    assert config.kwargs["inner_agent"]["import_path"] == "b:B"
    assert config.kwargs["inner_agent"]["kwargs"]["inner_agent"]["name"] == "codex"
    assert apply_session_wrappers(base, []) is base


# ---- the gate's pre-fix snapshot record -------------------------------------------------


def test_the_gate_record_carries_every_pre_fix_snapshot() -> None:
    options = GuardrailGateOptions(inner_agent=InnerAgentSpec(name="nop"), command="check")
    state = _GateState()
    state.pre_fix.append(PreFixSnapshot(1, "stage_2_fix", "guardrail-gate/pre_fix_1/app", "d", 2))
    state.pre_fix.append(
        PreFixSnapshot(2, "stage_3_fix", "guardrail-gate/pre_fix_2/app", None, None, "boom")
    )

    record = gate_record(options, state)

    assert record["workflow"] == "guardrail-gate@4"
    assert [item["round"] for item in record["pre_fix_snapshots"]] == [1, 2]
    assert record["pre_fix_snapshots"][1] == {
        "round": 2,
        "before_session": "stage_3_fix",
        "path": "guardrail-gate/pre_fix_2/app",
        "tree_digest": None,
        "files": None,
        "error": "boom",
    }


def test_a_failed_pre_fix_snapshot_is_recorded_and_never_fails_the_step(tmp_path: Path) -> None:
    agent = GuardrailGateAgent(
        logs_dir=tmp_path,
        model_name="openai/gpt-6-luna",
        inner_agent={"name": "nop"},
        command="guardrail-check",
    )

    class _Broken:
        async def download_dir_with_exclusions(self, **_: Any) -> None:
            raise RuntimeError("container gone")

    snapshot = asyncio.run(agent._snapshot(_Broken(), 1, "stage_2_fix"))  # type: ignore[arg-type]  # noqa: SLF001

    assert (snapshot.digest, snapshot.error) == (None, "RuntimeError: container gone")
