"""Agent and environment boundaries: refusals, source selection, pricing, probe and image edges."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from raidar_contract.agent import AgentName

from raidar.subject.agent.config import Harness
from raidar.subject.agent.definition import harbor_adapter
from raidar.subject.agent.native import adapter_class_for_harness, credentials, native_agent_class
from raidar.subject.agent.pricing import price_tokens
from raidar.subject.agent.protocol import IsolationError, unread_json_lines
from raidar.subject.environment.python import image as image_module
from tests.native_support import native_agent

TOKENS: dict[str, Any] = {"prompt": 10, "completion": 5, "cache_read": 3, "cache_write": 1}


# ---------------------------------------------------------------- pricing


_RATES = {"prompt": 1.0, "cache_read": 0.1, "cache_write": 1.5, "completion": 4.0}


def _rate_table(**kwargs: Any) -> tuple[float, float]:
    """Known rates: cached prompt tokens are cheaper, cache writes dearer, per 1000 tokens."""
    fresh = kwargs["prompt_tokens"] - kwargs["cache_read_input_tokens"]
    fresh -= kwargs["cache_creation_input_tokens"]
    prompt = (
        fresh * _RATES["prompt"]
        + kwargs["cache_read_input_tokens"] * _RATES["cache_read"]
        + kwargs["cache_creation_input_tokens"] * _RATES["cache_write"]
    )
    return prompt / 1000, kwargs["completion_tokens"] * _RATES["completion"] / 1000


def test_cost_is_computed_from_the_tokens_and_their_cache_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(cost_per_token=_rate_table))

    plain = price_tokens("m", prompt=100, completion=10, cache_read=0, cache_write=0)
    cached = price_tokens("m", prompt=100, completion=10, cache_read=60, cache_write=0)
    written = price_tokens("m", prompt=100, completion=10, cache_read=0, cache_write=20)
    longer = price_tokens("m", prompt=100, completion=20, cache_read=0, cache_write=0)

    assert plain == pytest.approx(0.14)
    assert cached == pytest.approx(0.1 * 60 / 1000 + 40 / 1000 + 0.04)
    assert cached < plain < written
    assert longer == pytest.approx(plain + 0.04)


def test_a_model_the_table_rejects_or_an_absent_table_is_unpriced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unknown(**_kwargs: Any) -> tuple[float, float]:
        raise KeyError("no such model")

    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(cost_per_token=unknown))
    assert price_tokens("m", **TOKENS) is None

    monkeypatch.setitem(sys.modules, "litellm", None)
    assert price_tokens("m", **TOKENS) is None


# ------------------------------------------------- non-Harbor adapter refusals


class _PlainAdapter:
    """An adapter that is not a Harbor-installed agent."""


def test_an_adapter_that_is_not_harbor_installed_is_refused_everywhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "raidar.subject.agent.native.registry._adapters", {Harness.CLAUDE_CODE: _PlainAdapter}
    )

    with pytest.raises(ValueError, match="not a Harbor-installed agent adapter"):
        harbor_adapter(AgentName.CLAUDE_CODE)
    with pytest.raises(ValueError, match="not a Harbor-installed agent adapter"):
        native_agent_class(Harness.CLAUDE_CODE)
    with pytest.raises(ValueError, match="not a Harbor-installed agent adapter"):
        credentials(Harness.CLAUDE_CODE, {})


class _ProviderlessCodex(adapter_class_for_harness(Harness.CODEX_CLI)):
    """An adapter without a declared model/provider catalog."""

    REQUIRED_PROVIDER = ""
    SUPPORTED_MODELS: dict[str, Any] = {}


def test_a_harbor_adapter_without_a_provider_declares_no_model_coverage() -> None:
    assert _ProviderlessCodex.supported_model_summary() == "(model coverage not declared)"


# ------------------------------------------------------------------ probe


async def test_probe_output_yields_facts_and_distinct_sorted_skills(tmp_path: Path) -> None:
    stdout = "\n".join(
        [
            "user=agent",
            "path=/usr/bin",
            "tmpdir=",
            "skill=/b/SKILL.md",
            "skill=/a/SKILL.md",
            "skill=/b/SKILL.md",
            "unrelated=1",
            "noise without separator",
        ]
    )

    class ProbeEnvironment:
        default_user = "agent"
        capabilities = SimpleNamespace(mounted=True)

        async def exec(self, command: str, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                return_code=0, stdout=stdout if "SKILL.md" in command else "", stderr=""
            )

    agent = native_agent(Harness.CODEX_CLI, tmp_path, bundled_skills=[])
    await agent.isolate(ProbeEnvironment())

    assert agent.process_facts() == {"user": "agent", "path": "/usr/bin", "tmpdir": None}
    assert agent.harness_isolation["skills_off"] == ["/a/SKILL.md", "/b/SKILL.md"]


# --------------------------------------------------------- JSON-lines audit


def test_blank_lines_are_not_counted_as_unread_records(tmp_path: Path) -> None:
    record = tmp_path / "turn.jsonl"
    record.write_text('{"ok": 1}\n\n   \nnot json\n{"foo": "bar"}\n[1]\n', encoding="utf-8")

    assert unread_json_lines(record, lambda r: "ok" in r) == 3


# ------------------------------------------------------------- codex auth


def _auth_file(tmp_path: Path, content: str = '{"tokens": {"a": 1}}') -> Path:
    path = tmp_path / "auth.json"
    path.write_text(content)
    return path


def test_chatgpt_mode_uses_the_session_cache_even_when_a_key_is_present(tmp_path: Path) -> None:
    _auth_file(tmp_path)
    resolved = credentials(
        Harness.CODEX_CLI,
        {"CODEX_HOME": str(tmp_path), "CODEX_AUTH_MODE": "chatgpt", "OPENAI_API_KEY": "fixture"},
    )

    assert resolved.issue is None
    assert resolved.session_used
    assert resolved.session_file == tmp_path / "auth.json"


# ------------------------------------------------------- isolated codex


def test_an_isolated_codex_without_its_options_refuses_to_report_isolation(
    tmp_path: Path,
) -> None:
    agent = native_agent(Harness.CODEX_CLI, tmp_path)
    agent.options = None

    with pytest.raises(IsolationError, match="without its options"):
        agent.isolation_options  # noqa: B018


# ------------------------------------------------------------ docker image


def _fake_docker_process(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Honors the ``subprocess.run`` contract: capture returns output, else it is written out."""
    code = 1 if argv[1] == "fail" else 0
    if kwargs.get("capture_output"):
        result = subprocess.CompletedProcess(argv, code, stdout="result-json", stderr="")
    else:
        kwargs["stdout"].write("build log\n")
        result = subprocess.CompletedProcess(argv, code, stdout=None, stderr=None)
    if kwargs["check"] and code:
        raise subprocess.CalledProcessError(code, argv)
    return result


def test_docker_results_are_returned_but_build_logs_stay_off_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(image_module.subprocess, "run", _fake_docker_process)

    captured = image_module.run_docker(["inspect"], check=True)
    streamed = image_module.run_docker(["build"], check=True, capture=False)

    out = capsys.readouterr()
    assert captured.stdout == "result-json"
    assert streamed.stdout is None
    assert (out.out, out.err) == ("", "build log\n")


def test_a_failing_docker_command_raises_only_when_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(image_module.subprocess, "run", _fake_docker_process)

    assert image_module.run_docker(["fail"], check=False).returncode == 1
    with pytest.raises(subprocess.CalledProcessError):
        image_module.run_docker(["fail"], check=True)


def test_image_presence_follows_whether_docker_inspects_the_tag() -> None:
    def docker(present: bool) -> Any:
        def run(
            args: Any, *, check: bool, capture: bool = True
        ) -> subprocess.CompletedProcess[str]:
            body = '[{"Id": "sha256:1", "Config": null}]' if present else ""
            return subprocess.CompletedProcess(list(args), 0 if present else 1, stdout=body)

        return run

    assert image_module.image_present("t:1", docker(True))
    assert not image_module.image_present("t:1", docker(False))
    record = image_module.inspect_image("t:1", docker(True))
    assert record is not None
    assert (record.image_id, dict(record.labels)) == ("sha256:1", {})
