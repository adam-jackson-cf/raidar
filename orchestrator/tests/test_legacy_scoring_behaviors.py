import subprocess
from types import SimpleNamespace

from raidar.schemas.events import GateEvent
from raidar.schemas.scenario import AcceptanceConfig, DeterministicCheck
from raidar.scoring import acceptance, functional, verification_stability, visual


def test_acceptance_checks_cover_import_file_absence_unknown_and_regex_safety(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    src = workspace / "src"
    src.mkdir(parents=True)
    (src / "index.ts").write_text(
        "import Ready from './ready';\nconst secret = true\n", encoding="utf-8"
    )
    (src / "page.tsx").write_text("<main />", encoding="utf-8")
    (workspace / "README.md").write_text("readme", encoding="utf-8")

    assert acceptance.check_import_present(workspace, "Ready") == (True, "Found in src/index.ts")
    assert acceptance.check_import_present(workspace, "Missing")[0] is False
    assert acceptance.check_import_present(tmp_path / "missing", "Ready") == (
        False,
        "src directory not found",
    )
    assert acceptance.check_file_exists(workspace, "*.md") == (True, "Found 1 matching files")
    assert acceptance.check_file_exists(workspace, "*.py") == (False, "No files matching '*.py'")
    assert acceptance.check_no_pattern(workspace, "console\\.log") == (
        True,
        "Pattern not found (good)",
    )
    assert acceptance.check_no_pattern(workspace, "secret")[0] is False
    assert acceptance.check_no_pattern(tmp_path / "missing", "secret") == (
        True,
        "src directory not found (pattern check passes)",
    )
    assert acceptance.validate_safe_regex_pattern("a" * 513)[0] is False
    assert acceptance.validate_safe_regex_pattern("(a+)+")[0] is False
    assert acceptance.validate_safe_regex_pattern("(a|aa)+")[0] is False

    checks = [
        DeterministicCheck(type="import_present", pattern="Ready", description="Ready import"),
        DeterministicCheck(type="file_exists", pattern="README.md", description="Readme"),
        DeterministicCheck(type="no_pattern", pattern="secret", description="No secret"),
    ]
    unknown = acceptance.run_deterministic_check(
        SimpleNamespace(type="unknown", pattern="x", description="Unknown"),
        workspace,
    )
    assert unknown.passed is False
    assert unknown.evidence == "Unknown check type: unknown"
    result = acceptance.evaluate_acceptance(
        workspace,
        AcceptanceConfig(requirements=[], deterministic_checks=checks),
    )
    assert [check.passed for check in result.checks] == [True, True, False]
    assert acceptance._ratio_passed(result.checks) == 2 / 3
    assert acceptance._ratio_passed([]) == 1.0
    assert acceptance._score_acceptance_checks([]).score == 1.0


def test_judge_response_parsing_and_llm_metric_delegation(monkeypatch, tmp_path):
    assert acceptance.parse_judge_response("VERDICT: PASS\nEVIDENCE: solid").passed is True
    assert acceptance.parse_judge_response("VERDICT: FAIL\nNo evidence").evidence.startswith(
        "VERDICT: FAIL"
    )
    assert acceptance.parse_judge_response("PASS: looks good").passed is True
    assert acceptance.parse_judge_response("FAIL: gap").passed is False
    assert acceptance.parse_judge_response("unclear").evidence.startswith("Could not parse")

    calls = []

    def fake_evaluate(**kwargs):
        calls.append(kwargs)
        return "metric"

    monkeypatch.setattr("raidar.scoring.llm_as_judge.evaluate_llm_as_judge_metric", fake_evaluate)
    assert (
        acceptance.evaluate_llm_as_judge_metric(
            workspace=tmp_path,
            scenario_dir=tmp_path / "scenario",
            scenario=SimpleNamespace(),
            metric_id="requirements-adherence",
            judge_path="judge.toml",
        )
        == "metric"
    )
    assert calls[0]["metric_id"] == "requirements-adherence"


def test_functional_command_and_test_evaluation_paths(monkeypatch, tmp_path):
    real_run_command = functional.run_command
    monkeypatch.setattr(
        functional.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["bun"], 0, stdout="2 pass\n1 fail", stderr=""
        ),
    )
    assert functional.run_command(["bun"], tmp_path) == (0, "2 pass\n1 fail", "")
    assert functional.parse_test_output("2 pass", "1 fail") == (2, 3)
    assert functional.parse_test_output("3 passed", "1 failed") == (3, 4)
    assert functional.run_tests(tmp_path) == (False, 2, 3)

    monkeypatch.setattr(
        functional,
        "run_command",
        lambda *_args, **_kwargs: (0, "No tests found", ""),
    )
    assert functional.run_tests(tmp_path) == (True, 0, 0)
    monkeypatch.setattr(functional, "run_command", lambda *_args, **_kwargs: (1, "", ""))
    assert functional.run_tests(tmp_path) == (False, 0, 0)
    monkeypatch.setattr(functional, "run_command", lambda *_args, **_kwargs: (0, "1 pass", ""))
    assert functional.run_tests(tmp_path) == (True, 1, 1)
    assert functional._check_package_script(tmp_path, "build", timeout=1) is True
    assert functional.evaluate_functional(tmp_path).passed is True
    monkeypatch.setattr(functional, "run_command", real_run_command)

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["bun"], timeout=1)

    monkeypatch.setattr(functional.subprocess, "run", timeout)
    assert functional.run_command(["bun"], tmp_path, timeout=1) == (-1, "", "Command timed out")

    def missing(*_args, **_kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(functional.subprocess, "run", missing)
    assert functional.run_command(["missing"], tmp_path, timeout=1) == (
        -1,
        "",
        "Command not found: missing",
    )


def test_visual_capture_compare_and_evaluate_paths(monkeypatch, tmp_path):
    reference = tmp_path / "reference.png"
    actual = tmp_path / "actual.png"
    diff = tmp_path / "diff.png"
    reference.write_text("reference", encoding="utf-8")
    actual.write_text("actual", encoding="utf-8")

    monkeypatch.setattr(
        visual.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(["capture"], 0, stdout="", stderr=""),
    )
    assert visual.capture_screenshot(tmp_path, ["capture"], actual) is True
    assert visual.compare_images(tmp_path, tmp_path / "missing.png", actual, diff) == (0.0, None)
    assert visual.compare_images(tmp_path, reference, tmp_path / "missing.png", diff) == (0.0, None)
    assert visual.compare_images(tmp_path, reference, actual, diff) == (1.0, None)

    monkeypatch.setattr(
        visual.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["odiff"], 1, stdout="12.5% different", stderr=""
        ),
    )
    diff.write_text("diff", encoding="utf-8")
    assert visual.compare_images(tmp_path, reference, actual, diff) == (0.875, str(diff))
    diff.unlink()
    assert visual.compare_images(tmp_path, reference, actual, diff) == (0.875, None)

    monkeypatch.setattr(
        visual.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(["odiff"], 1, stdout="", stderr=""),
    )
    diff.write_text("diff", encoding="utf-8")
    assert visual.compare_images(tmp_path, reference, actual, diff) == (0.0, str(diff))
    diff.unlink()
    assert visual.compare_images(tmp_path, reference, actual, diff) == (0.0, None)

    def missing(*_args, **_kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(visual.subprocess, "run", missing)
    assert visual.capture_screenshot(tmp_path, ["capture"], actual) is False
    assert visual.compare_images(tmp_path, reference, actual, diff) == (0.0, None)

    monkeypatch.setattr(visual, "capture_screenshot", lambda *_args: False)
    assert visual.evaluate_visual(tmp_path, reference, ["capture"]).capture_succeeded is False
    monkeypatch.setattr(visual, "capture_screenshot", lambda *_args: True)
    monkeypatch.setattr(visual, "compare_images", lambda **_kwargs: (0.9, "diff.png"))
    score = visual.evaluate_visual(tmp_path, reference, ["capture"])
    assert score.capture_succeeded is True
    assert score.similarity == 0.9
    assert score.diff_path == "diff.png"


def test_verification_stability_scores_repeated_failure_categories():
    assert verification_stability.calculate_verification_stability_score(2, 1, 1) == 0.3
    events = [
        GateEvent(
            timestamp="now",
            gate_name="build",
            command="bun run build",
            exit_code=0,
            stdout="",
            stderr="",
        ),
        GateEvent(
            timestamp="now",
            gate_name="test",
            command="bun run test",
            exit_code=1,
            stdout="",
            stderr="",
            failure_category="test",
        ),
        GateEvent(
            timestamp="now",
            gate_name="test",
            command="bun run test",
            exit_code=1,
            stdout="",
            stderr="",
            failure_category="test",
        ),
        GateEvent(
            timestamp="now",
            gate_name="lint",
            command="bun run lint",
            exit_code=1,
            stdout="",
            stderr="",
        ),
    ]
    score = verification_stability.evaluate_verification_stability(events)
    assert score.total_gate_failures == 3
    assert score.unique_failure_categories == 1
    assert score.repeat_failures == 1
