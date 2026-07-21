import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from raidar.scorers import llm_as_judge


def test_codex_command_response_text_and_output_clipping(monkeypatch):
    monkeypatch.setattr(
        llm_as_judge.settings,
        "llm_as_judge",
        SimpleNamespace(model="gpt-5.5", reasoning_effort="low"),
    )
    command = llm_as_judge._codex_judge_command(
        image_name="raidar-task-env:judge-reserve",
        auth_path=Path("/tmp/auth.json"),
    )
    assert (
        command[-3:]
        == [
            "gpt-5.5",
            "-c",
            "model_reasoning_effort=low",
            "-",
        ][-3:]
    )
    monkeypatch.setattr(
        llm_as_judge.settings,
        "llm_as_judge",
        SimpleNamespace(model="gpt-5.5", reasoning_effort=""),
    )
    command = llm_as_judge._codex_judge_command(
        image_name="raidar-task-env:judge-reserve",
        auth_path=Path("/tmp/auth.json"),
    )
    assert "-c" not in command

    stdout = "\n".join(
        [
            json.dumps({"item": {"type": "agent_message", "text": "agent text"}}),
            json.dumps({"item": {"type": "message", "content": [{"text": "part one"}, "skip"]}}),
            json.dumps({"type": "message", "content": "top message"}),
            json.dumps({"type": "agent_message", "text": "top agent"}),
            "plain fallback",
            json.dumps({"type": "ignored"}),
        ]
    )
    assert llm_as_judge._codex_response_text(stdout) == (
        "agent text\npart one\ntop message\ntop agent\nplain fallback"
    )
    assert llm_as_judge._codex_response_text("") == ""
    assert llm_as_judge._content_text({"bad": "shape"}) == ""
    assert llm_as_judge._clip_output("a\n b\tc", max_chars=20) == "a b c"
    assert llm_as_judge._clip_output("x" * 10, max_chars=5) == "xx..."


def test_metric_score_requires_strict_structured_json():
    output = {
        "passed": True,
        "score": 0.9,
        "verdict": "PASS",
        "evidence": "All rubric areas are supported.",
        "findings": [],
        "rubric_coverage": {"intent": "pass"},
        "residual_risk": [],
    }
    json_score = llm_as_judge._metric_score_from_response(
        json.dumps(output),
        metric_id="requirements-adherence",
    )
    assert json_score.score == 0.9
    assert json_score.passed is True
    assert json_score.evidence == "All rubric areas are supported."

    fenced = llm_as_judge._metric_score_from_response(
        f"```json\n{json.dumps(output)}\n```",
        metric_id="requirements-adherence",
    )
    assert fenced.score == 0.9
    assert fenced.passed is True
    assert llm_as_judge._parse_json_response(f"```\njson\n{json.dumps(output)}\n```") == output

    assert llm_as_judge._parse_json_response("[]") is None
    for invalid in ("VERDICT: PASS", '{"score": 0.9}', '{"score": 2}'):
        with pytest.raises(llm_as_judge.JudgeEvaluationError):
            llm_as_judge._metric_score_from_response(
                invalid,
                metric_id="requirements-adherence",
            )


def test_judge_prompt_includes_scenario_prompt_and_bounded_workspace_sources(monkeypatch, tmp_path):
    scenario_dir = tmp_path / "scenario"
    workspace = tmp_path / "workspace"
    scenario_dir.mkdir()
    workspace.mkdir()
    (scenario_dir / "prompt.md").write_text("Build the app", encoding="utf-8")
    (workspace / "src").mkdir()
    (workspace / "src" / "app.ts").write_text("console.log('hello')", encoding="utf-8")
    (workspace / "src" / "binary.ts").write_bytes(b"\xff")
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "skip.ts").write_text("skip", encoding="utf-8")
    scenario = SimpleNamespace(
        name="scenario",
        scenario_revision="v001",
        description="Description",
        languages=["typescript"],
        prompt=SimpleNamespace(entry="prompt.md"),
        requirements=SimpleNamespace(
            items=[
                SimpleNamespace(
                    id="req-1",
                    description="Need source",
                    check=SimpleNamespace(type="file_exists", pattern="src/app.ts"),
                ),
                SimpleNamespace(
                    id="req-2",
                    description="Ignore unsafe path",
                    check=SimpleNamespace(type="file_exists", pattern="../secret"),
                ),
            ]
        ),
        scorers=[
            SimpleNamespace(config={"artifact-checks": {"required_paths": ["src/app.ts", 42]}})
        ],
    )
    monkeypatch.setattr(
        llm_as_judge.settings,
        "llm_as_judge",
        SimpleNamespace(max_source_chars=10),
    )

    prompt = llm_as_judge._judge_prompt(
        workspace=workspace,
        scenario_dir=scenario_dir,
        scenario=scenario,
    )

    assert "Scenario: scenario@v001" in prompt
    assert "Task prompt:\nBuild the app" in prompt
    assert "File: src/app.ts" in prompt
    assert "node_modules" not in prompt
    monkeypatch.setattr(
        llm_as_judge.settings,
        "llm_as_judge",
        SimpleNamespace(max_source_chars=1000),
    )
    assert "binary.ts" not in llm_as_judge._workspace_sources(workspace, scenario=scenario)
    assert llm_as_judge._task_prompt(tmp_path / "missing", scenario) == ""
    assert llm_as_judge._looks_like_workspace_path("src/app.ts") is True
    assert llm_as_judge._looks_like_workspace_path("../app.ts") is False


def test_evaluate_llm_as_judge_metric_handles_resolution_call_and_failure(monkeypatch, tmp_path):
    scenario = SimpleNamespace(
        name="scenario",
        scenario_revision="v001",
        description="Description",
        languages=["typescript"],
        prompt=SimpleNamespace(entry="missing.md"),
        requirements=SimpleNamespace(items=[]),
        scorers=[],
    )
    monkeypatch.setattr(
        llm_as_judge,
        "resolve_scorer_definition_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("missing judge")),
    )
    with pytest.raises(llm_as_judge.JudgeEvaluationError, match="Judge definition unavailable"):
        llm_as_judge.evaluate_llm_as_judge_metric(
            workspace=tmp_path,
            scenario_dir=tmp_path,
            scenario=scenario,
            metric_id="requirements-adherence",
            judge_path="missing.toml",
        )

    judge_file = tmp_path / "judge.toml"
    judge_file.write_text("judge role", encoding="utf-8")
    monkeypatch.setattr(
        llm_as_judge, "resolve_scorer_definition_file", lambda *_args, **_kwargs: judge_file
    )
    with pytest.raises(llm_as_judge.JudgeEvaluationError, match="explicit judge executor"):
        llm_as_judge.evaluate_llm_as_judge_metric(
            workspace=tmp_path,
            scenario_dir=tmp_path,
            scenario=scenario,
            metric_id="requirements-adherence",
            judge_path="judge.toml",
        )

    def failed_executor(_role, _prompt):
        raise RuntimeError("judge failed")

    with pytest.raises(llm_as_judge.JudgeEvaluationError, match="Judge execution failed"):
        llm_as_judge.evaluate_llm_as_judge_metric(
            workspace=tmp_path,
            scenario_dir=tmp_path,
            scenario=scenario,
            metric_id="requirements-adherence",
            judge_path="judge.toml",
            executor=failed_executor,
        )

    response = json.dumps(
        {
            "passed": True,
            "score": 0.9,
            "verdict": "PASS",
            "evidence": "Supported.",
            "findings": [],
            "rubric_coverage": {},
            "residual_risk": [],
        }
    )
    scored = llm_as_judge.evaluate_llm_as_judge_metric(
        workspace=tmp_path,
        scenario_dir=tmp_path,
        scenario=scenario,
        metric_id="requirements-adherence",
        judge_path="judge.toml",
        executor=lambda _role, _prompt: response,
    )
    assert scored.score == 0.9
    assert scored.passed is True


def test_call_codex_judge_requires_chatgpt_auth_and_returns_response(monkeypatch, tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"tokens":true}', encoding="utf-8")
    monkeypatch.setattr(
        llm_as_judge.settings,
        "llm_as_judge",
        SimpleNamespace(codex_auth_mode="chatgpt", model="gpt-5.5", reasoning_effort="low"),
    )
    monkeypatch.setattr(
        llm_as_judge,
        "resolve_codex_auth",
        lambda requested_mode: SimpleNamespace(resolved_mode="api-key", auth_json_path=None),
    )
    with pytest.raises(OSError, match="ChatGPT auth"):
        llm_as_judge._call_codex_judge(
            image_name="raidar-task-env:judge-reserve",
            judge_role="role",
            prompt="prompt",
        )

    monkeypatch.setattr(
        llm_as_judge,
        "resolve_codex_auth",
        lambda requested_mode: SimpleNamespace(resolved_mode="chatgpt", auth_json_path=auth_path),
    )
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs["input"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"type": "message", "content": "ok"}),
            stderr="",
        )

    monkeypatch.setattr(llm_as_judge.subprocess, "run", fake_run)
    assert (
        llm_as_judge._call_codex_judge(
            image_name="raidar-task-env:judge-reserve",
            judge_role="role",
            prompt="prompt",
        )
        == "ok"
    )
    assert captured["input"] == "role\n\nprompt"
    assert captured["command"][:4] == ["docker", "run", "--rm", "-i"]

    monkeypatch.setattr(
        llm_as_judge.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 2, stdout="out", stderr="err"
        ),
    )
    with pytest.raises(RuntimeError, match="Codex judge failed with exit 2"):
        llm_as_judge._call_codex_judge(
            image_name="raidar-task-env:judge-reserve",
            judge_role="role",
            prompt="prompt",
        )


def test_codex_judge_executor_requires_and_binds_prepared_image(monkeypatch):
    with pytest.raises(llm_as_judge.JudgeEvaluationError, match="image is unavailable"):
        llm_as_judge.codex_judge_executor(" ")

    calls = []
    monkeypatch.setattr(
        llm_as_judge,
        "_call_codex_judge",
        lambda **kwargs: calls.append(kwargs) or "structured response",
    )
    executor = llm_as_judge.codex_judge_executor("raidar-task-env:judge-reserve")
    assert executor("role", "prompt") == "structured response"
    assert calls == [
        {
            "image_name": "raidar-task-env:judge-reserve",
            "judge_role": "role",
            "prompt": "prompt",
        }
    ]
