from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from raidar.application.models import SuiteExecutionResult
from raidar.commands import matrix_report
from raidar.matrix import MatrixAgentSpec, MatrixJob


def _job(entry_id: str = "entry-1") -> MatrixJob:
    return MatrixJob(
        entry_id=entry_id,
        scenario_path="scenarios/demo/v001",
        scenario=SimpleNamespace(name="demo", scenario_revision="v001"),
        agent=MatrixAgentSpec(
            harness="codex-cli",
            provider="openai",
            model="gpt-5.5",
            reasoning_effort="low",
        ),
    )


def test_matrix_config_from_options_loads_and_wraps_validation_errors(monkeypatch, tmp_path):
    config_path = tmp_path / "matrix.yaml"
    config_path.write_text("matrix: {}", encoding="utf-8")
    expected = SimpleNamespace(id="matrix", experiment=SimpleNamespace())
    monkeypatch.setattr("raidar.matrix.load_matrix_config", lambda path: expected)
    assert matrix_report.matrix_config_from_options({"config": config_path}) is expected

    def invalid(_path):
        raise ValidationError.from_exception_data("MatrixConfig", [])

    monkeypatch.setattr("raidar.matrix.load_matrix_config", invalid)
    with pytest.raises(click.ClickException):
        matrix_report.matrix_config_from_options({"config": config_path})


def test_matrix_echo_helpers_emit_settings_and_dry_run(capsys):
    config = SimpleNamespace(
        id="matrix-one",
        experiment=SimpleNamespace(
            timeout_sec=300,
            repeats=2,
            repeat_parallel=1,
            retry_void=True,
        ),
    )

    matrix_report.echo_matrix_settings(config, [_job()])
    matrix_report.echo_matrix_dry_run(jobs=[_job()], repeats=2)

    output = capsys.readouterr().out
    assert "Matrix 'matrix-one' defined for 1 experiments" in output
    assert "timeout=300s, repeats=2" in output
    assert "[dry-run] entry-1: demo@v001: codex-cli/openai/gpt-5.5 [low] x2" in output


def test_matrix_job_options_passes_resolved_request_to_builder(monkeypatch, tmp_path):
    captured = []
    built = object()
    monkeypatch.setattr(
        matrix_report,
        "build_run_cli_options_from_request",
        lambda request: captured.append(request) or built,
    )

    options = matrix_report.matrix_job_options(
        {
            "job": _job(),
            "experiment_config": SimpleNamespace(
                timeout_sec=100,
                repeats=3,
                repeat_parallel=2,
                retry_void=False,
            ),
            "experiments_root": tmp_path / "experiments",
            "experiment_kind": "benchmark",
        }
    )

    assert options is built
    request = captured[0]
    assert request.scenario == "scenarios/demo/v001"
    assert request.reasoning_effort == "low"
    assert request.repeats == 3


def test_matrix_jobs_run_sequential_and_parallel_successes_and_failures(capsys, tmp_path):
    jobs = [_job("ok"), _job("bad")]

    def run_job(job):
        if job.entry_id == "bad":
            raise RuntimeError("boom")
        return SuiteExecutionResult(
            scenario_path=tmp_path / "scenario",
            scenario_name="demo",
            scenario_revision="v001",
            summary_path=tmp_path / "summary.json",
            runs=[],
            retries_used=0,
        )

    assert matrix_report.run_sequential_matrix_jobs(jobs, run_job) == (1, 1)
    sequential = capsys.readouterr().out
    assert "Running experiment: ok demo@v001 codex-cli/gpt-5.5" in sequential
    assert "[bad] codex-cli/gpt-5.5 failed: boom" in sequential

    assert matrix_report.run_parallel_matrix_jobs(jobs, parallel=2, run_matrix_job=run_job) == (
        1,
        1,
    )
    parallel = capsys.readouterr().out
    assert "summary.json" in parallel
    assert "failed: boom" in parallel


def test_run_matrix_jobs_selects_parallel_or_sequential(monkeypatch, tmp_path):
    calls: list[str] = []
    monkeypatch.setattr(
        matrix_report,
        "run_sequential_matrix_jobs",
        lambda jobs, runner: calls.append("sequential") or (len(jobs), 0),
    )
    monkeypatch.setattr(
        matrix_report,
        "run_parallel_matrix_jobs",
        lambda jobs, parallel, runner: calls.append(f"parallel-{parallel}") or (len(jobs), 0),
    )

    settings = {
        "experiment_config": SimpleNamespace(),
        "experiments_root": tmp_path,
        "experiment_kind": "benchmark",
    }
    assert matrix_report.run_matrix_jobs([_job()], parallel=1, **settings) == (1, 0)
    assert matrix_report.run_matrix_jobs([_job()], parallel=3, **settings) == (1, 0)
    assert calls == ["sequential", "parallel-3"]


def test_matrix_command_dry_run_and_resolution_error(monkeypatch, tmp_path):
    config_path = tmp_path / "matrix.yaml"
    config_path.write_text("matrix: {}", encoding="utf-8")
    config = SimpleNamespace(
        id="matrix",
        experiment=SimpleNamespace(timeout_sec=1, repeats=1, repeat_parallel=1, retry_void=False),
    )
    monkeypatch.setattr(matrix_report, "matrix_config_from_options", lambda _options: config)
    monkeypatch.setattr("raidar.matrix.resolve_matrix_jobs", lambda _config, repo_root: [_job()])

    result = CliRunner().invoke(matrix_report.matrix, ["--config", str(config_path), "--dry-run"])

    assert result.exit_code == 0
    assert "[dry-run]" in result.output

    monkeypatch.setattr(
        "raidar.matrix.resolve_matrix_jobs",
        lambda _config, repo_root: (_ for _ in ()).throw(FileNotFoundError("missing scenario")),
    )
    result = CliRunner().invoke(matrix_report.matrix, ["--config", str(config_path)])
    assert result.exit_code != 0
    assert "missing scenario" in result.output


def test_report_command_outputs_csv_markdown_json_and_empty(monkeypatch, tmp_path):
    runs = [SimpleNamespace(id="run")]
    monkeypatch.setattr("raidar.storage.load_all_runs", lambda _results: runs)
    monkeypatch.setattr(
        "raidar.storage.export_to_csv", lambda _runs, output: output.write_text("csv")
    )
    monkeypatch.setattr("raidar.storage.generate_comparison_report", lambda _runs: "markdown")
    monkeypatch.setattr("raidar.storage.aggregate_results", lambda _runs: {"runs": 1})

    results_dir = tmp_path / "results"
    results_dir.mkdir()
    runner = CliRunner()

    csv_result = runner.invoke(
        matrix_report.report, ["--results", str(results_dir), "--format", "csv"]
    )
    assert csv_result.exit_code == 0
    assert (results_dir / "comparison.csv").read_text() == "csv"

    markdown_output = tmp_path / "report.md"
    markdown_result = runner.invoke(
        matrix_report.report,
        ["--results", str(results_dir), "--format", "markdown", "--output", str(markdown_output)],
    )
    assert markdown_result.exit_code == 0
    assert markdown_output.read_text() == "markdown"

    json_result = runner.invoke(
        matrix_report.report, ["--results", str(results_dir), "--format", "json"]
    )
    assert json_result.exit_code == 0
    assert '"runs": 1' in json_result.output

    monkeypatch.setattr("raidar.storage.load_all_runs", lambda _results: [])
    empty_result = runner.invoke(matrix_report.report, ["--results", str(results_dir)])
    assert empty_result.exit_code == 0
    assert "No runs found" in empty_result.output


def test_init_matrix_writes_example_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("raidar.matrix.create_example_matrix", lambda: "matrix: {}\n")

    result = CliRunner().invoke(matrix_report.init_matrix)

    assert result.exit_code == 0
    assert (tmp_path / "matrix.yaml").read_text() == "matrix: {}\n"
