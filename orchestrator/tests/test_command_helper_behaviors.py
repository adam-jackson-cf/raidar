import json
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

from raidar.commands import matrix_report, quality, scenario, shared


def test_shared_command_helpers_raise_and_parse_json(monkeypatch, tmp_path) -> None:
    calls: list[tuple[list[str], object]] = []

    class Result:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode

    monkeypatch.setattr(
        shared.subprocess,
        "run",
        lambda cmd, cwd, env, check: calls.append((cmd, env)) or Result(0),
    )
    shared.run_or_raise(["tool", "ok"], tmp_path, env={"A": "1"})
    assert calls == [(["tool", "ok"], {"A": "1"})]

    monkeypatch.setattr(
        shared.subprocess,
        "run",
        lambda cmd, cwd, env, check: Result(7),
    )
    with pytest.raises(click.ClickException, match="Command failed \\(7\\): tool fail"):
        shared.run_or_raise(["tool", "fail"], tmp_path)

    assert shared.load_json_file(tmp_path / "missing.json") is None
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"ok": True}), encoding="utf-8")
    assert shared.load_json_file(payload) == {"ok": True}
    payload.write_text("[1, 2]", encoding="utf-8")
    assert shared.load_json_file(payload) is None
    payload.write_text("{bad", encoding="utf-8")
    assert shared.load_json_file(payload) is None

    monkeypatch.setattr(
        shared,
        "_service_resolve_scenario_yaml",
        lambda _path: (_ for _ in ()).throw(FileNotFoundError("missing scenario")),
    )
    with pytest.raises(click.ClickException, match="missing scenario"):
        shared.resolve_scenario_yaml(tmp_path / "scenario")

    assert shared.python_cmd()

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        shared,
        "execute_run_command",
        lambda request, *, repo_root: captured.setdefault("request", request),
    )
    options = SimpleNamespace()
    shared.execute_run_options(
        options,
        force_experiment_summary=True,
        cleanup_before_runs=False,
        echo=True,
        execution_suffix="suffix",
    )
    assert captured["request"].options is options
    assert captured["request"].execution_suffix == "suffix"


def test_quality_gate_helpers_validate_requirements_and_commands(monkeypatch, tmp_path) -> None:
    with pytest.raises(click.ClickException, match="--stage is only supported"):
        quality.validate_quality_gate_options(fix=False, stage=True)

    monkeypatch.setattr(quality.repo_state, "has_unstaged_changes", lambda _root: True)
    with pytest.raises(click.ClickException, match="Unstaged changes detected"):
        quality.validate_quality_gate_options(fix=True, stage=False)

    monkeypatch.setattr(
        quality.repo_state, "assert_no_generated_artifact_changes", lambda _root: None
    )
    monkeypatch.setattr(
        quality.shutil, "which", lambda command: None if command == "lizard" else "/bin/tool"
    )
    with pytest.raises(click.ClickException, match="Missing required command: lizard"):
        quality.assert_quality_gate_requirements()

    monkeypatch.setattr(
        quality.shutil, "which", lambda command: None if command == "lint-imports" else "/bin/tool"
    )
    with pytest.raises(click.ClickException, match="Missing required command: lint-imports"):
        quality.assert_quality_gate_requirements()

    calls: list[list[str]] = []
    monkeypatch.setattr(quality, "python_cmd", lambda: "python")
    monkeypatch.setattr(quality, "run_or_raise", lambda cmd, *_args, **_kwargs: calls.append(cmd))
    quality.run_ruff_quality_gates(fix=True)
    quality.run_ruff_quality_gates(fix=False)

    assert calls[0] == ["python", "-m", "ruff", "format", "--force-exclude"]
    assert calls[1] == ["python", "-m", "ruff", "check", ".", "--fix", "--force-exclude"]
    assert calls[2] == ["python", "-m", "ruff", "format", "--check", "--force-exclude"]
    assert calls[3] == ["python", "-m", "ruff", "check", ".", "--no-fix", "--force-exclude"]

    monkeypatch.setattr(quality, "validate_quality_gate_options", lambda **_kwargs: None)
    monkeypatch.setattr(quality, "assert_quality_gate_requirements", lambda: None)
    monkeypatch.setattr(quality, "run_ruff_quality_gates", lambda **_kwargs: None)
    monkeypatch.setattr(quality, "run_orchestrator_quality_gates", lambda: None)
    calls.clear()
    runner = CliRunner()
    result = runner.invoke(quality.quality_gates, ["--fix", "--stage"])
    assert result.exit_code == 0
    assert calls == [["git", "-C", str(quality.REPO_ROOT), "add", "-u"]]


def test_matrix_report_failure_paths_and_parallel_reporting(monkeypatch, tmp_path) -> None:
    class BrokenValidation(Exception):
        def __str__(self) -> str:
            return "bad matrix"

    monkeypatch.setattr(matrix_report, "load_matrix_config", None, raising=False)
    monkeypatch.setattr(
        "raidar.matrix.load_matrix_config",
        lambda _config: (_ for _ in ()).throw(BrokenValidation()),
    )
    monkeypatch.setattr(matrix_report, "ValidationError", BrokenValidation)
    with pytest.raises(click.ClickException, match="bad matrix"):
        matrix_report.matrix_config_from_options({"config": tmp_path / "matrix.yaml"})

    good_job = SimpleNamespace(
        entry_id="good",
        agent=SimpleNamespace(harness="codex-cli", model="gpt-5.5"),
    )
    bad_job = SimpleNamespace(
        entry_id="bad",
        agent=SimpleNamespace(harness="codex-cli", model="gpt-5.5"),
    )

    def run_job(job):
        if job.entry_id == "bad":
            raise RuntimeError("failed")
        return SimpleNamespace(summary_path=tmp_path / "summary.json")

    successes, failures = matrix_report.run_parallel_matrix_jobs([good_job, bad_job], 2, run_job)
    assert (successes, failures) == (1, 1)

    runner = CliRunner()
    monkeypatch.setattr("raidar.matrix.create_example_matrix", lambda: "matrix: example")
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(matrix_report.init_matrix)
        assert result.exit_code == 0
        assert "Example matrix configuration created" in result.output


def test_matrix_command_executes_non_dry_run_and_job_options(monkeypatch, tmp_path) -> None:
    job = SimpleNamespace(
        entry_id="entry-a",
        scenario_path=tmp_path / "scenario.yaml",
        scenario=SimpleNamespace(name="Demo", scenario_revision="v001"),
        agent=SimpleNamespace(
            harness="codex-cli",
            provider="openai",
            model="gpt-5.5",
            reasoning_effort=None,
        ),
    )
    config = SimpleNamespace(
        id="matrix-a",
        experiment=SimpleNamespace(
            timeout_sec=30,
            repeats=1,
            repeat_parallel=1,
            retry_void=0,
        ),
    )
    calls: dict[str, object] = {}

    monkeypatch.setattr(matrix_report, "matrix_config_from_options", lambda _options: config)
    monkeypatch.setattr("raidar.matrix.resolve_matrix_jobs", lambda _config, repo_root: [job])
    monkeypatch.setattr(
        matrix_report,
        "cleanup_stale_harbor_before_runs",
        lambda: calls.setdefault("cleanup", True),
    )
    monkeypatch.setattr(
        matrix_report,
        "resolve_experiments_root",
        lambda **_kwargs: tmp_path / "experiments",
    )

    def fake_execute_run_options(options, **settings):
        calls["run"] = (options, settings)
        return SimpleNamespace(summary_path=tmp_path / "summary.json")

    monkeypatch.setattr(matrix_report, "execute_run_options", fake_execute_run_options)

    runner = CliRunner()
    matrix_path = tmp_path / "matrix.yaml"
    matrix_path.write_text("matrix: {}", encoding="utf-8")
    result = runner.invoke(
        matrix_report.matrix,
        ["--config", str(matrix_path), "--parallel", "1"],
    )

    assert result.exit_code == 0, result.output
    options, settings = calls["run"]
    assert calls["cleanup"] is True
    assert options.scenario == job.scenario_path
    assert settings["execution_suffix"] == "codex-cli__openai-gpt-5.5"
    assert "Matrix completed: 1 experiments succeeded, 0 failed." in result.output


def test_scenario_echo_helpers_and_listing(monkeypatch, tmp_path) -> None:
    scenario_root = tmp_path / "scenarios"
    assert scenario.list_scenarios_with_revisions(scenario_root) == []
    revision = scenario_root / "demo" / "v001" / "scenario.yaml"
    revision.parent.mkdir(parents=True)
    revision.write_text("name: demo", encoding="utf-8")
    monkeypatch.setattr(scenario, "scenario_revision_paths", lambda _root: [revision])
    monkeypatch.setattr(scenario, "load_scenario", lambda _path: SimpleNamespace(name="Demo"))
    assert scenario.list_scenarios_with_revisions(scenario_root) == [("Demo", ("v001",))]

    task_def = SimpleNamespace(
        name="Demo",
        scenario_revision="v001",
        parent_revision=None,
        description="A task",
        difficulty="easy",
        category="code",
        timeout_sec=600,
        verification=SimpleNamespace(gates=[SimpleNamespace(name="build")]),
        visual=SimpleNamespace(
            reference_image="reference.png",
            pass_policy=SimpleNamespace(
                minimum_score=0.9,
                fail_if_global_below=0.8,
                minimum_worst_region=0.7,
            ),
        ),
        requirements=SimpleNamespace(items=[object(), object()]),
    )
    monkeypatch.setattr(scenario, "scenario_evaluation_profile", lambda _task: "code-task")
    monkeypatch.setattr(scenario, "scenario_scorers", lambda _task: ["typescript-code-task"])
    monkeypatch.setattr(scenario, "scenario_metrics", lambda _task: ["functional"])

    scenario.echo_scenario_summary(task_def)
    scenario.echo_visual_config(task_def)
    scenario.echo_requirements_config(task_def)

    rules = revision.parent / "rules"
    rules.mkdir()
    (rules / "codex.md").write_text("rules", encoding="utf-8")
    scenario.echo_rule_variants(revision.parent)
    scenario.echo_available_revisions(scenario_root / "demo")

    empty_root = scenario_root / "empty"
    empty_root.mkdir()
    monkeypatch.setattr(scenario, "scenario_revision_paths", lambda _root: [])
    assert scenario.list_scenarios_with_revisions(scenario_root) == []
    scenario.echo_available_revisions(empty_root)
    scenario.echo_rule_variants(tmp_path / "no-rules")
    scenario.echo_visual_config(SimpleNamespace(visual=None))
    scenario.echo_requirements_config(SimpleNamespace(requirements=SimpleNamespace(items=[])))


def test_scenario_commands_wrap_service_results(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text("scenario", encoding="utf-8")
    starter = tmp_path / "starter"
    starter.mkdir()

    init_result = SimpleNamespace(scenario_yaml=tmp_path / "created" / "scenario.yaml")
    monkeypatch.setattr(scenario, "_service_init_scenario", lambda _request: init_result)
    monkeypatch.setattr(
        scenario, "scenario_init_payload", lambda _result: {"scenario_yaml": "created"}
    )
    result = runner.invoke(scenario.scenario_init, ["--path", str(tmp_path / "created"), "--json"])
    assert result.exit_code == 0
    assert '"scenario_yaml": "created"' in result.output

    monkeypatch.setattr(
        scenario,
        "_service_init_scenario",
        lambda _request: (_ for _ in ()).throw(FileExistsError("exists")),
    )
    result = runner.invoke(scenario.scenario_init, ["--path", str(tmp_path / "created")])
    assert result.exit_code != 0
    assert "exists" in result.output

    validate_scenario = SimpleNamespace(
        name="Demo",
        scenario_revision="v001",
        parent_revision=None,
        starter=SimpleNamespace(root="starter"),
        prompt=SimpleNamespace(entry="prompt.md"),
        verification=SimpleNamespace(required_commands=[1], gates=[1, 2]),
        scorers=[1, 2, 3],
        metric_ids=lambda: ["functional"],
    )
    monkeypatch.setattr(
        scenario,
        "_service_validate_scenario",
        lambda _path: SimpleNamespace(scenario=validate_scenario),
    )
    result = runner.invoke(scenario.scenario_validate, ["--scenario", str(scenario_path)])
    assert result.exit_code == 0
    assert "Scenario validation passed." in result.output

    monkeypatch.setattr(
        scenario,
        "_service_validate_scenario",
        lambda _path: (_ for _ in ()).throw(FileNotFoundError("missing")),
    )
    result = runner.invoke(scenario.scenario_validate, ["--scenario", str(scenario_path)])
    assert result.exit_code != 0
    assert "missing" in result.output

    clone_result = SimpleNamespace(
        scenario_root=tmp_path / "demo",
        source_revision="v001",
        target_revision="v002",
        parent_revision="v001",
        target_scenario_yaml=tmp_path / "demo" / "v002" / "scenario.yaml",
    )
    monkeypatch.setattr(scenario, "_service_clone_scenario_revision", lambda _request: clone_result)
    monkeypatch.setattr(
        scenario, "scenario_clone_payload", lambda _result: {"target_revision": "v002"}
    )
    result = runner.invoke(
        scenario.scenario_clone_revision,
        ["--path", str(tmp_path), "--from-revision", "v001", "--json"],
    )
    assert result.exit_code == 0
    assert '"target_revision": "v002"' in result.output

    monkeypatch.setattr(
        scenario,
        "_service_clone_scenario_revision",
        lambda _request: (_ for _ in ()).throw(ValueError("bad revision")),
    )
    result = runner.invoke(
        scenario.scenario_clone_revision,
        ["--path", str(tmp_path), "--from-revision", "v001"],
    )
    assert result.exit_code != 0
    assert "bad revision" in result.output

    monkeypatch.setattr(scenario, "inject_rules", lambda rules_dir, target, harness: "ok")
    scenario.inject.callback(tmp_path, "codex-cli", starter)
