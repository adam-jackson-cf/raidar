"""Trial configuration decisions: the verifier's pins, the arm's agent and host session auth."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from harbor.models.trial.config import AgentConfig

from raidar.engine.trial import agent_config, blueprint, config, host_auth
from raidar.engine.trial.agent_config import (
    agent_digest,
    arm_agent_config,
    effective_agent_timeout_s,
    native_agent,
    reported_harness_pin,
)
from raidar.engine.trial.blueprint import arm_blueprints
from raidar.engine.trial.config import (
    VERIFIER_PHASE_MARGIN_S,
    harbor_trial_config,
    verifier_timeout_s,
)
from raidar.engine.trial.host_auth import CODEX_AUTH_ENV, with_host_auth
from raidar.subject.agent.config import Harness
from raidar.subject.agent.native import Credentials
from raidar.subject.workflow.inner import INNER_KWARG
from raidar.subject.workflow.runner import WorkflowRunner

# --- verifier timing and pins ----------------------------------------------------------------


def _protocol(coverage: str, *, timeout: int = 300) -> Any:
    return SimpleNamespace(
        verifier_coverage=coverage, verifier_timeout_sec=timeout, snapshot_source="artifact"
    )


def test_the_verifier_phase_budgets_a_coverage_run_only_when_the_trial_makes_it() -> None:
    assert verifier_timeout_s(_protocol("trial")) == 2 * 300 + VERIFIER_PHASE_MARGIN_S
    assert verifier_timeout_s(_protocol("offline")) == 300 + VERIFIER_PHASE_MARGIN_S


def _plan(
    *,
    coverage: str = "trial",
    grader: dict[str, str] | None = None,
    errata: dict[str, str] | None = None,
    solvable: dict[str, str] | None = None,
) -> Any:
    problem = SimpleNamespace(grader=grader, errata=errata, solvable=solvable)
    agent = SimpleNamespace(harness=Harness.CODEX_CLI)
    arm = SimpleNamespace(arm=SimpleNamespace(agent=agent), agent_config=AgentConfig(name="oracle"))
    return SimpleNamespace(
        loaded=SimpleNamespace(spec=SimpleNamespace(protocol=_protocol(coverage))),
        arms={"guided": arm},
        problems=[SimpleNamespace(grader=None, errata=None, solvable=None), problem],
    )


_TRIAL = SimpleNamespace(arm="guided", problem_index=1, trial_id="guided-p1-r0")


def _trial_config(plan: Any, tmp_path: Path) -> Any:
    return harbor_trial_config(
        plan,
        _TRIAL,
        tmp_path / "task",
        trials_dir=tmp_path / "trials",
        bundle_root=tmp_path / "bundle",
        docker_context="ctx",
    )


@pytest.fixture(autouse=True)
def _no_host_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "with_host_auth", lambda agent, harness: agent)


def test_a_trial_verifies_with_the_problems_grader_errata_and_solvable_pins(
    tmp_path: Path,
) -> None:
    plan = _plan(grader={"digest": "g"}, errata={"digest": "e"}, solvable={"digest": "s"})

    trial_config = _trial_config(plan, tmp_path)

    assert trial_config.trial_name == "guided-p1-r0"
    kwargs = trial_config.verifier.kwargs
    assert kwargs["pinned"] == {
        "grader": {"digest": "g"},
        "errata": {"digest": "e"},
        "solvable": {"digest": "s"},
    }
    assert kwargs["coverage"] is True
    assert kwargs["grader_timeout_s"] == 300.0
    assert kwargs["oracle_bundle"] == str(tmp_path / "bundle")
    assert trial_config.verifier.override_timeout_sec == 2 * 300 + VERIFIER_PHASE_MARGIN_S


def test_a_problem_without_errata_or_solvable_sets_pins_only_its_grader(tmp_path: Path) -> None:
    plan = _plan(coverage="offline", grader={"digest": "g"})

    trial_config = _trial_config(plan, tmp_path)

    assert trial_config.verifier.kwargs["pinned"] == {"grader": {"digest": "g"}}
    assert trial_config.verifier.kwargs["coverage"] is False
    assert trial_config.verifier.override_timeout_sec == 300 + VERIFIER_PHASE_MARGIN_S


def test_a_problem_without_a_validated_grader_identity_cannot_be_verified(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="problem 1 pins no validated grader identity"):
        _trial_config(_plan(grader=None, errata={"digest": "e"}), tmp_path)


# --- host session auth -----------------------------------------------------------------------


def _credentials(monkeypatch: pytest.MonkeyPatch, found: Credentials) -> list[Harness]:
    asked: list[Harness] = []

    def lookup(harness: Harness, env: Any) -> Credentials:
        asked.append(harness)
        return found

    monkeypatch.setattr(host_auth, "credentials", lookup)
    return asked


def test_a_codex_agent_with_a_host_session_file_gets_only_its_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Path("/host/.codex/auth.json")
    _credentials(monkeypatch, Credentials(None, session, True, True))
    original = AgentConfig(name="codex", env={"KEEP": "1"})

    configured = with_host_auth(original, Harness.CODEX_CLI)

    assert configured.env == {"KEEP": "1", CODEX_AUTH_ENV: str(session)}
    assert original.env == {"KEEP": "1"}


def test_a_host_that_cannot_authenticate_codex_fails_with_the_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _credentials(monkeypatch, Credentials("no codex login on this host"))

    with pytest.raises(OSError, match="no codex login on this host"):
        with_host_auth(AgentConfig(name="codex"), Harness.CODEX_CLI)


@pytest.mark.parametrize(
    "found",
    [
        Credentials(None, Path("/host/auth.json"), True, False),
        Credentials(None, None, False, True),
    ],
)
def test_codex_without_a_usable_session_file_keeps_its_configuration(
    monkeypatch: pytest.MonkeyPatch, found: Credentials
) -> None:
    _credentials(monkeypatch, found)
    original = AgentConfig(name="codex")

    assert with_host_auth(original, Harness.CODEX_CLI) == original


def test_another_harness_never_consults_the_host_for_codex_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _credentials(monkeypatch, Credentials("must not be reached"))
    original = AgentConfig(name="codex")

    assert with_host_auth(original, Harness.CLAUDE_CODE) == original
    assert asked == []


# --- agent configuration ---------------------------------------------------------------------


def test_the_effective_agent_budget_is_the_configured_timeout_at_harbors_default_scale() -> None:
    assert effective_agent_timeout_s(90) == 90.0


NO_PACK: Any = SimpleNamespace()


def test_a_no_llm_override_runs_the_named_harbor_agent_under_the_phase_timeout() -> None:
    arm: Any = SimpleNamespace()  # an override never reads the arm

    configured = arm_agent_config(arm, NO_PACK, agent_timeout_sec=45, override="nop")

    assert configured.name == "nop"
    assert configured.override_timeout_sec == 45.0
    assert reported_harness_pin(configured) is None


class _Adapter:
    def native_agent_class(self) -> Any:
        return SimpleNamespace(import_path=lambda: "harbor.agents.native:Codex")

    def isolation_kwargs(self, settings: dict[str, Any]) -> dict[str, Any]:
        return {"isolation": dict(settings)}


def _stage(name: str, *, setup: bool, stop: bool, outputs: tuple[str, ...]) -> Any:
    manifest = SimpleNamespace(
        setup="setup.sh" if setup else None,
        on_stop="stop.sh" if stop else None,
        outputs={output: {} for output in outputs},
    )
    return SimpleNamespace(
        release=SimpleNamespace(name=name), package=SimpleNamespace(manifest=manifest)
    )


def _arm(*, stages: list[Any], reasoning: str | None = "high") -> Any:
    placement = SimpleNamespace(instructions="AGENTS.md", skills=".agents/skills")
    released = SimpleNamespace(
        ref="codex@1", definition=SimpleNamespace(settings={"mode": "strict"}, placement=placement)
    )
    agent = SimpleNamespace(
        harness=Harness.CODEX_CLI,
        name="codex",
        version="0.1.0",
        reasoning=reasoning,
        provider="openai",
        model="gpt-x",
        released=released,
    )
    return SimpleNamespace(
        agent=agent,
        workflow=SimpleNamespace(ref="wf@1", stages=stages),
        definition=SimpleNamespace(to_json=lambda: {"stages": len(stages)}),
        packages={stage.release.name: {"digest": f"d-{stage.release.name}"} for stage in stages},
        primitive_code={"gate": "code-hash"},
    )


@pytest.fixture
def pack(monkeypatch: pytest.MonkeyPatch) -> Any:
    harness_calls: list[tuple[str, str | None]] = []

    def harness_kwargs(pack: Any, harness: str, *, configured: str | None) -> dict[str, str]:
        harness_calls.append((harness, configured))
        return {"version": configured or "pinned"}

    monkeypatch.setattr(agent_config, "harbor_agent_kwargs", harness_kwargs)
    monkeypatch.setattr(agent_config, "harbor_adapter", lambda name: _Adapter())
    monkeypatch.setattr(agent_config, "image_lock_hash", lambda spec: "pack-hash")
    return SimpleNamespace(calls=harness_calls)


def test_a_workflow_arm_runs_its_native_agent_inside_the_runner_at_the_pinned_cli(
    pack: Any,
) -> None:
    arm = _arm(stages=[])

    configured = arm_agent_config(arm, NO_PACK, agent_timeout_sec=120)

    assert configured.import_path == WorkflowRunner.import_path()
    assert configured.model_name == "openai/gpt-x"
    assert configured.override_timeout_sec == 120.0
    inner = configured.kwargs[INNER_KWARG]
    assert inner["name"] == "codex"
    assert inner["import_path"] == "harbor.agents.native:Codex"
    assert inner["kwargs"] == {
        "version": "0.1.0",
        "reasoning_effort": "high",
        "isolation": {"mode": "strict"},
    }
    assert configured.kwargs["workflow"] == "wf@1"
    assert configured.kwargs["phase_timeout_s"] == 120.0
    assert configured.kwargs["skills_placement"] == ".agents/skills"
    assert "locked_packages" not in configured.kwargs
    assert pack.calls == [("codex-cli", "0.1.0")]
    assert native_agent(configured) == ("codex", inner["kwargs"])
    assert reported_harness_pin(configured) == "0.1.0"


def test_an_arm_without_reasoning_sets_no_reasoning_effort(pack: Any) -> None:
    configured = arm_agent_config(_arm(stages=[], reasoning=None), NO_PACK, agent_timeout_sec=60)

    assert "reasoning_effort" not in configured.kwargs[INNER_KWARG]["kwargs"]


def test_a_package_arm_carries_its_locked_packages_declarations_and_pack_identity(
    pack: Any,
) -> None:
    stages = [
        _stage("planner", setup=True, stop=False, outputs=("plan", "notes")),
        _stage("checker", setup=False, stop=True, outputs=()),
    ]

    configured = arm_agent_config(_arm(stages=stages), NO_PACK, agent_timeout_sec=60)

    kwargs = configured.kwargs
    assert kwargs["locked_packages"] == {
        "planner": {
            "digest": "d-planner",
            "declared": {"setup": True, "stop": False, "outputs": ["notes", "plan"]},
        },
        "checker": {
            "digest": "d-checker",
            "declared": {"setup": False, "stop": True, "outputs": []},
        },
    }
    assert kwargs["primitives"] == {"gate": "code-hash"}
    assert kwargs["pack"] == "pack-hash"
    assert kwargs[INNER_KWARG]["kwargs"]["instructions_path"] == "AGENTS.md"


def test_the_agent_identity_changes_with_the_override_and_the_cli_version(pack: Any) -> None:
    arm = _arm(stages=[])
    native = arm_agent_config(arm, NO_PACK, agent_timeout_sec=60)
    nop = arm_agent_config(arm, NO_PACK, agent_timeout_sec=60, override="nop")
    other_version = AgentConfig(
        import_path=native.import_path,
        kwargs={INNER_KWARG: {"name": "codex", "kwargs": {"version": "9.9.9"}}},
    )

    base = agent_digest(arm, native, None)

    assert base == agent_digest(arm, native, None)
    assert base != agent_digest(arm, nop, "nop")
    assert base != agent_digest(arm, other_version, None)


def test_a_plain_harbor_agent_is_its_own_native_agent() -> None:
    plain = AgentConfig(name="codex", kwargs={"version": 3})
    nameless = AgentConfig(import_path="x.y:Z", kwargs={INNER_KWARG: "not a mapping"})
    bare_inner = AgentConfig(
        import_path=WorkflowRunner.import_path(), kwargs={INNER_KWARG: {"name": "claude-code"}}
    )

    assert native_agent(plain) == ("codex", {"version": 3})
    assert reported_harness_pin(plain) == "3"
    assert native_agent(nameless)[0] is None
    assert native_agent(bare_inner) == ("claude-code", {})
    assert reported_harness_pin(nameless) is None
    assert reported_harness_pin(AgentConfig(name="codex")) is None


# --- arm blueprints --------------------------------------------------------------------------


def _loaded(*ids: str) -> Any:
    spec = SimpleNamespace(
        arms=[SimpleNamespace(id=arm_id) for arm_id in ids],
        protocol=SimpleNamespace(agent_timeout_sec=77),
    )
    return SimpleNamespace(spec=spec)


def test_an_override_gives_every_arm_the_no_llm_agent_under_the_protocol_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arms = {"plain": SimpleNamespace(tag="plain"), "guided": SimpleNamespace(tag="guided")}
    monkeypatch.setattr(blueprint, "resolve_arm", lambda spec, arm_id: arms[arm_id])

    blueprints = arm_blueprints(_loaded("plain", "guided"), NO_PACK, agent_override="nop")

    assert list(blueprints) == ["plain", "guided"]
    assert blueprints["guided"].arm is arms["guided"]
    for item in blueprints.values():
        assert item.agent_config.name == "nop"
        assert item.agent_config.override_timeout_sec == 77.0


def test_without_an_override_each_arm_runs_its_own_native_agent(
    monkeypatch: pytest.MonkeyPatch, pack: Any
) -> None:
    arms = {"plain": _arm(stages=[], reasoning=None), "guided": _arm(stages=[])}
    monkeypatch.setattr(blueprint, "resolve_arm", lambda spec, arm_id: arms[arm_id])

    blueprints = arm_blueprints(_loaded("plain", "guided"), NO_PACK, agent_override=None)

    assert blueprints["guided"].agent_config.model_name == "openai/gpt-x"
    assert blueprints["guided"].agent_config.override_timeout_sec == 77.0
    assert native_agent(blueprints["guided"].agent_config)[1]["reasoning_effort"] == "high"
    assert "reasoning_effort" not in native_agent(blueprints["plain"].agent_config)[1]
