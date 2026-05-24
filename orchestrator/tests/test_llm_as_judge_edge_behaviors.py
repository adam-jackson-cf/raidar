import json
import subprocess
from types import SimpleNamespace

from raidar.scorers import llm_as_judge


def test_codex_command_response_text_and_output_clipping(monkeypatch):
    monkeypatch.setattr(
        llm_as_judge.settings,
        "llm_as_judge",
        SimpleNamespace(model="gpt-5.5", reasoning_effort="low"),
    )
    assert (
        llm_as_judge._codex_judge_command()[-3:]
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
    assert "-c" not in llm_as_judge._codex_judge_command()

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


def test_metric_score_from_json_and_fallback_responses():
    json_score = llm_as_judge._metric_score_from_response(
        '{"score":1.2,"passed":"false","findings":["a"]}',
        metric_id="requirements-adherence",
    )
    assert json_score.score == 1.0
    assert json_score.passed is False
    assert json_score.evidence == "1 judge finding(s)."

    fenced = llm_as_judge._metric_score_from_response(
        '```json\n{"score":"0.75","summary":"ok"}\n```',
        metric_id="requirements-adherence",
    )
    assert fenced.score == 0.75
    assert fenced.passed is False
    assert fenced.evidence == "ok"

    assert llm_as_judge._parse_json_response("[]") is None
    assert llm_as_judge._bounded_score("bad") == 0.0
    assert llm_as_judge._bounded_score("-1") == 0.0
    assert llm_as_judge._bool_value("true", default=False) is True
    assert llm_as_judge._bool_value("false", default=True) is False
    assert llm_as_judge._bool_value("unknown", default=True) is True
    assert llm_as_judge._evidence_from_json({"verdict": "ship"}, fallback="fallback") == "ship"
    assert llm_as_judge._evidence_from_json({}, fallback="fallback") == "fallback"

    fallback = llm_as_judge._metric_score_from_response(
        "VERDICT: PASS\nEVIDENCE: enough",
        metric_id="requirements-adherence",
    )
    assert fallback.score == 1.0
    assert fallback.passed is True


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
        prompt=SimpleNamespace(entry="prompt.md"),
        acceptance=SimpleNamespace(
            requirements=[
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
    assert llm_as_judge._task_prompt(tmp_path / "missing", scenario) == ""
    assert llm_as_judge._looks_like_workspace_path("src/app.ts") is True
    assert llm_as_judge._looks_like_workspace_path("../app.ts") is False


def test_evaluate_llm_as_judge_metric_handles_resolution_call_and_failure(monkeypatch, tmp_path):
    scenario = SimpleNamespace(
        name="scenario",
        scenario_revision="v001",
        description="Description",
        prompt=SimpleNamespace(entry="missing.md"),
        acceptance=SimpleNamespace(requirements=[]),
        scorers=[],
    )
    monkeypatch.setattr(
        llm_as_judge,
        "resolve_scorer_definition_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("missing judge")),
    )
    missing = llm_as_judge.evaluate_llm_as_judge_metric(
        workspace=tmp_path,
        scenario_dir=tmp_path,
        scenario=scenario,
        metric_id="requirements-adherence",
        judge_path="missing.toml",
    )
    assert missing.passed is False
    assert missing.missing_patterns == ["missing.toml"]

    judge_file = tmp_path / "judge.toml"
    judge_file.write_text("judge role", encoding="utf-8")
    monkeypatch.setattr(
        llm_as_judge, "resolve_scorer_definition_file", lambda *_args, **_kwargs: judge_file
    )
    monkeypatch.setattr(
        llm_as_judge,
        "_call_judge",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("judge failed")),
    )
    failed = llm_as_judge.evaluate_llm_as_judge_metric(
        workspace=tmp_path,
        scenario_dir=tmp_path,
        scenario=scenario,
        metric_id="requirements-adherence",
        judge_path="judge.toml",
    )
    assert failed.evidence == "LLM judge failed: RuntimeError: judge failed"

    monkeypatch.setattr(
        llm_as_judge, "_call_judge", lambda **_kwargs: '{"score":0.9,"passed":true}'
    )
    scored = llm_as_judge.evaluate_llm_as_judge_metric(
        workspace=tmp_path,
        scenario_dir=tmp_path,
        scenario=scenario,
        metric_id="requirements-adherence",
        judge_path="judge.toml",
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
    try:
        llm_as_judge._call_codex_judge(judge_role="role", prompt="prompt")
    except OSError as exc:
        assert "ChatGPT auth" in str(exc)

    monkeypatch.setattr(
        llm_as_judge,
        "resolve_codex_auth",
        lambda requested_mode: SimpleNamespace(resolved_mode="chatgpt", auth_json_path=auth_path),
    )
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs["input"]
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"type": "message", "content": "ok"}),
            stderr="",
        )

    monkeypatch.setattr(llm_as_judge.subprocess, "run", fake_run)
    assert llm_as_judge._call_codex_judge(judge_role="role", prompt="prompt") == "ok"
    assert captured["input"] == "role\n\nprompt"
    assert "OPENAI_API_KEY" not in captured["env"]

    monkeypatch.setattr(
        llm_as_judge.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 2, stdout="out", stderr="err"
        ),
    )
    try:
        llm_as_judge._call_codex_judge(judge_role="role", prompt="prompt")
    except RuntimeError as exc:
        assert "Codex judge failed with exit 2" in str(exc)
