import json
from datetime import UTC, datetime
from types import SimpleNamespace

from raidar.harness import HarborResultManifest
from raidar.runtime import artifacts
from raidar.runtime.models import (
    EvaluationOutputs,
    HarborExecutionResult,
    RunLayout,
)
from raidar.schemas.events import GateEvent
from raidar.schemas.scorecard import (
    ExecutionValidityScore,
    GateCheck,
    PerformanceGatesScore,
    Scorecard,
    ScorerResult,
)


def _outputs(*, secret_output: bool = False) -> EvaluationOutputs:
    stdout = "Bearer abcdefghijklmnop token=supersecretvalue" if secret_output else "ok"
    return EvaluationOutputs(
        execution_validity=ExecutionValidityScore(
            checks=[GateCheck(name="valid", passed=True, evidence="ok")]
        ),
        performance_gates=PerformanceGatesScore(
            checks=[GateCheck(name="perf", passed=True, evidence="ok")]
        ),
        gate_history=[
            GateEvent(
                timestamp="2026-01-01T00:00:00+00:00",
                gate_name="build",
                command="bun run build --api-key=abcdef1234567890"
                if secret_output
                else "bun run build",
                exit_code=0,
                stdout=stdout,
                stderr="password=hunter2value" if secret_output else "",
            )
        ],
    )


def test_load_evidence_outputs_reports_missing_invalid_and_valid_evidence(tmp_path):
    assert artifacts._load_evidence_outputs(None) == (
        None,
        "Harbor trial directory not found.",
    )

    verifier_dir = tmp_path / "verifier"
    verifier_dir.mkdir()
    manifest = HarborResultManifest.for_trial(tmp_path, "codex-cli")
    evidence_path = verifier_dir / "evidence.json"
    assert "Evidence output missing" in artifacts._load_evidence_outputs(manifest)[1]

    evidence_path.write_text("{", encoding="utf-8")
    assert "Invalid current-version evidence" in artifacts._load_evidence_outputs(manifest)[1]

    evidence_path.write_text("[]", encoding="utf-8")
    assert "Invalid current-version evidence" in artifacts._load_evidence_outputs(manifest)[1]

    payload = {
        "schema_version": "1",
        "runner_contract": "python-evidence-runner@2",
        "aliases": {
            "design-to-code.build": "design-to-code.build",
            "design-to-code.lint": "design-to-code.lint",
        },
        "actions": [
            {
                "id": "design-to-code.build",
                "kind": "command",
                "required": True,
                "status": "ok",
                "duration_sec": 0.1,
                "result": {
                    "command": ["bun", "run", "build"],
                    "cwd": ".",
                    "duration_sec": 0.1,
                    "exit_code": 0,
                    "stdout": "ok",
                    "stderr": "",
                    "timed_out": False,
                },
            },
            {
                "id": "design-to-code.lint",
                "kind": "command",
                "required": False,
                "status": "ok",
                "duration_sec": 0.1,
                "result": {
                    "command": ["bun", "run", "lint"],
                    "cwd": ".",
                    "duration_sec": 0.1,
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": "lint failed",
                    "timed_out": False,
                },
            },
        ],
        "artifacts": [],
        "metadata": {
            "action_count": 2,
            "error_count": 0,
            "required_error_count": 0,
            "workspace": "/app",
        },
    }
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")
    loaded, error = artifacts._load_evidence_outputs(manifest)
    assert error is None
    assert loaded.gate_history[0].gate_name == "design-to-code.build"
    assert loaded.gate_history[1].command == "bun run lint"
    assert loaded.gate_history[1].exit_code == 1


def test_persist_runner_evidence_and_scoring_artifacts(tmp_path):
    trial_dir = tmp_path / "trial"
    verifier_source = trial_dir / "verifier"
    verifier_source.mkdir(parents=True)
    (verifier_source / "evidence.json").write_text('{"actions":[]}', encoding="utf-8")
    (verifier_source / "action-logs").mkdir()
    (verifier_source / "action-logs" / "001-build.json").write_text(
        '{"status":"ok"}',
        encoding="utf-8",
    )
    (verifier_source / "baseline" / "action-logs").mkdir(parents=True)
    (verifier_source / "baseline" / "evidence.json").write_text(
        '{"actions":[]}',
        encoding="utf-8",
    )
    (verifier_source / "baseline" / "action-logs" / "001-build.json").write_text(
        '{"status":"ok"}',
        encoding="utf-8",
    )

    manifest = HarborResultManifest.for_trial(trial_dir, "codex-cli")
    runner_evidence_target = tmp_path / "canonical" / "runner-evidence"
    runner_evidence_target.mkdir(parents=True)
    scoring_target = tmp_path / "canonical" / "scoring"

    copied = artifacts.persist_runner_evidence_artifacts(manifest, runner_evidence_target)
    assert set(copied) == {
        "action-logs",
        "baseline/action-logs",
        "baseline/evidence.json",
        "evidence.json",
    }
    assert (runner_evidence_target / "baseline" / "evidence.json").exists()
    assert (runner_evidence_target / "baseline" / "action-logs" / "001-build.json").exists()
    try:
        manifest.runner_evidence_artifact("baseline/../../escape.json")
    except ValueError:
        pass
    else:
        raise AssertionError("runner evidence paths must reject traversal")
    assert artifacts.persist_runner_evidence_artifacts(None, runner_evidence_target) == {}
    assert (
        artifacts.persist_runner_evidence_artifacts(
            HarborResultManifest.for_trial(tmp_path / "missing", "codex-cli"),
            runner_evidence_target,
        )
        == {}
    )

    layout = RunLayout(
        run_id="run",
        start_time=datetime.now(UTC),
        run_label="label",
        root_dir=tmp_path / "run",
        workspace_dir=tmp_path / "workspace",
        runner_evidence_dir=runner_evidence_target,
        scoring_dir=scoring_target,
        harness_dir=tmp_path / "harness",
        harbor_dir=tmp_path / "harbor",
        run_json_path=tmp_path / "run.json",
        report_path=tmp_path / "report.md",
    )
    scorecard = Scorecard(
        execution_validity=ExecutionValidityScore(checks=[GateCheck(name="valid", passed=False)]),
        performance_gates=PerformanceGatesScore(checks=[GateCheck(name="perf", passed=True)]),
    )
    artifacts.persist_scorecard_artifacts(layout, scorecard, _outputs())

    scorecard_payload = json.loads((scoring_target / "scorecard.json").read_text(encoding="utf-8"))
    assert "gate_history" in scorecard_payload
    assert {
        "functional",
        "visual",
        "verification_stability",
        "test_coverage",
        "requirements_coverage",
    }.isdisjoint(scorecard_payload)
    assert "metric_scores" in scorecard_payload
    assert "scorer_results" in scorecard_payload
    assert (scoring_target / "reward.txt").read_text(encoding="utf-8") == "0"


def test_build_scenario_revision_meta_uses_canonical_scenario_seed(tmp_path):
    scenario_dir = tmp_path / "scenario"
    scenario_dir.mkdir()
    (scenario_dir / "scenario.yaml").write_text("name: demo\n", encoding="utf-8")
    request = SimpleNamespace(
        scenario_dir=scenario_dir,
        scenario=SimpleNamespace(
            name="demo",
            scenario_revision="v001",
            starter=SimpleNamespace(root="starter"),
            scorers=[SimpleNamespace(id="design-to-code", version=1, weight=1.0)],
            metric_ids=lambda: ["visual-regression"],
            scorer_ids=lambda: ["design-to-code@1"],
            environment=SimpleNamespace(model_dump=lambda **_kwargs: {"id": "node"}),
            resolved_scorers=lambda: [
                SimpleNamespace(
                    ref="design-to-code@1",
                    requirements=SimpleNamespace(model_dump=lambda **_kwargs: {}),
                )
            ],
            model_dump=lambda **_kwargs: {"name": "demo", "scenario_revision": "v001"},
        ),
    )
    context = SimpleNamespace(starter_source=SimpleNamespace(fingerprint="starter-hash"))

    meta = artifacts.build_scenario_revision_meta(request, context)

    assert meta["scoring_schema_version"] == artifacts.SCORING_SCHEMA_VERSION
    assert meta["scenario_yaml_hash"]
    assert meta["scenario_fingerprint"]
    assert meta["evaluation_profile"] == "scorers:design-to-code@1:1"
    assert meta["metrics"] == ["visual-regression"]
    assert meta["scorer_requirements"] == [{"scorer": "design-to-code@1", "requirements": {}}]


def test_runner_evidence_artifacts_redact_secret_shaped_output(tmp_path):
    trial_dir = tmp_path / "trial"
    verifier_source = trial_dir / "verifier"
    verifier_source.mkdir(parents=True)
    (verifier_source / "evidence.json").write_text(
        json.dumps(
            {
                "actions": [
                    {
                        "id": "python-code-task.pytest",
                        "kind": "command",
                        "status": "ok",
                        "result": {
                            "command": "pytest --token=abcdefghijklmnop",
                            "stdout": "Bearer abcdefghijklmnop",
                            "stderr": "password=hunter2value",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (verifier_source / "action-logs").mkdir()
    (verifier_source / "action-logs" / "001-pytest.json").write_text(
        json.dumps({"stdout": "api_key=abcdefghijklmnop", "stderr": "ok"}),
        encoding="utf-8",
    )

    target = tmp_path / "canonical" / "runner-evidence"
    target.mkdir(parents=True)
    scoring_target = tmp_path / "canonical" / "scoring"
    harbor_result = HarborExecutionResult(False, None, tmp_path / "job", trial_dir)
    artifacts.persist_runner_evidence_artifacts(
        HarborResultManifest.for_trial(trial_dir, "codex-cli"),
        target,
    )

    copied_text = "\n".join(
        [
            (target / "evidence.json").read_text(encoding="utf-8"),
            (target / "action-logs" / "001-pytest.json").read_text(encoding="utf-8"),
        ]
    )
    assert "abcdefghijklmnop" not in copied_text
    assert "hunter2value" not in copied_text
    assert "Bear...mnop" in copied_text

    layout = RunLayout(
        run_id="run",
        start_time=datetime.now(UTC),
        run_label="label",
        root_dir=tmp_path / "run",
        workspace_dir=tmp_path / "workspace",
        runner_evidence_dir=target,
        scoring_dir=scoring_target,
        harness_dir=tmp_path / "harness",
        harbor_dir=tmp_path / "harbor",
        run_json_path=tmp_path / "run.json",
        report_path=tmp_path / "report.md",
    )
    scorecard = Scorecard(
        execution_validity=ExecutionValidityScore(
            checks=[
                GateCheck(
                    name="valid",
                    passed=False,
                    evidence="OPENAI_API_KEY=abcdefghijklmnop secret=abcdefghijklmnop",
                )
            ]
        ),
        performance_gates=PerformanceGatesScore(
            checks=[
                GateCheck(
                    name="perf",
                    passed=False,
                    evidence='{"DB_PASSWORD":"abcdefghijklmnop"}',
                )
            ]
        ),
    )
    artifacts.persist_scorecard_artifacts(layout, scorecard, _outputs(secret_output=True))
    artifacts.write_run_analysis(
        layout,
        SimpleNamespace(
            scenario=SimpleNamespace(name="scenario"),
            config=SimpleNamespace(
                harness=SimpleNamespace(value="codex-cli"),
                model=SimpleNamespace(qualified_name="model"),
            ),
        ),
        scorecard,
        harbor_result,
    )

    canonical_text = "\n".join(
        [
            (scoring_target / "scorecard.json").read_text(encoding="utf-8"),
            (scoring_target / "gate-history.json").read_text(encoding="utf-8"),
            (scoring_target / "execution-validity.json").read_text(encoding="utf-8"),
            (scoring_target / "performance-gates.json").read_text(encoding="utf-8"),
            layout.report_path.read_text(encoding="utf-8"),
        ]
    )
    assert "abcdefghijklmnop" not in canonical_text
    assert "hunter2value" not in canonical_text
    assert "OPENAI_API_KEY=<redacted>" in canonical_text
    assert "DB_PASSWORD" in canonical_text


def test_visual_evidence_persistence(tmp_path):
    scenario_dir = tmp_path / "scenario"
    reference_dir = scenario_dir / "reference"
    reference_dir.mkdir(parents=True)
    for name in ("page.png", "page-region-hero.png"):
        (reference_dir / name).write_text(name, encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("capture-main.png", "compare-main.png", "capture-hero.png", "compare-hero.png"):
        (workspace / name).write_text(name, encoding="utf-8")

    scenario = SimpleNamespace(
        visual=SimpleNamespace(
            reference_image="reference/page.png",
            artifact_manifest=SimpleNamespace(
                actual_image="capture-main.png",
                diff_image="compare-main.png",
                post_capture_image="post-main.png",
            ),
            regions=[
                SimpleNamespace(
                    name="hero",
                    reference_image="reference/page-region-hero.png",
                    actual_image="capture-hero.png",
                    diff_image="compare-hero.png",
                )
            ],
        ),
    )
    run_request = SimpleNamespace(scenario=scenario, scenario_dir=scenario_dir)
    request = artifacts.VisualEvidenceRequest(
        request=run_request,
        workspace=workspace,
        run_root_dir=tmp_path / "run",
    )

    evidence = artifacts._persist_visual_evidence_artifacts(request)

    assert evidence["actual"].endswith("visual/capture-main.png")
    assert evidence["reference"].endswith("visual/page.png")
    assert evidence["diff"].endswith("visual/compare-main.png")
    assert evidence["regions"][0]["name"] == "hero"

    no_visual_request = artifacts.VisualEvidenceRequest(
        request=SimpleNamespace(scenario=SimpleNamespace(visual=None)),
        workspace=workspace,
        run_root_dir=tmp_path / "run-no-visual",
    )
    assert artifacts._persist_visual_evidence_artifacts(no_visual_request) == {
        "actual": None,
        "reference": None,
        "diff": None,
        "regions": [],
    }


def test_harness_and_harbor_artifact_persistence(tmp_path):
    trial_dir = tmp_path / "trial"
    agent_dir = trial_dir / "agent"
    agent_dir.mkdir(parents=True)
    for name in ("trajectory.json", "codex.txt", "final-app.tar.gz"):
        (agent_dir / name).write_text(name, encoding="utf-8")
    (agent_dir / "trajectory.json").write_text(
        json.dumps({"stdout": "ANTHROPIC_API_KEY=abcdefghijklmnop"}),
        encoding="utf-8",
    )
    (agent_dir / "codex.txt").write_text("Bearer abcdefghijklmnop", encoding="utf-8")
    setup_dir = agent_dir / "setup"
    setup_dir.mkdir()
    (setup_dir / "install.log").write_text("DB_PASSWORD=abcdefghijklmnop", encoding="utf-8")
    command_dir = agent_dir / "command-001"
    command_dir.mkdir()
    (command_dir / "stdout.txt").write_text("OPENAI_API_KEY=abcdefghijklmnop", encoding="utf-8")
    (agent_dir / "command-file").write_text("skip", encoding="utf-8")

    harbor_result = HarborExecutionResult(False, None, tmp_path / "job", trial_dir)
    harness_dir = tmp_path / "harness"
    harness_dir.mkdir()
    copied = artifacts.persist_harness_artifacts(
        HarborResultManifest.for_trial(trial_dir, "codex-cli"),
        harness_dir,
    )

    assert copied["trajectory.json"].endswith("trajectory.json")
    assert copied["project.final.tar.gz"].endswith("project.final.tar.gz")
    assert copied["setup"].endswith("setup")
    assert copied["commands/command-001"].endswith("command-001")
    harness_text = "\n".join(
        [
            (harness_dir / "trajectory.json").read_text(encoding="utf-8"),
            (harness_dir / "codex.txt").read_text(encoding="utf-8"),
            (harness_dir / "setup" / "install.log").read_text(encoding="utf-8"),
            (harness_dir / "commands" / "command-001" / "stdout.txt").read_text(encoding="utf-8"),
        ]
    )
    assert "abcdefghijklmnop" not in harness_text
    assert "ANTHROPIC_API_KEY=<redacted>" in harness_text
    assert "OPENAI_API_KEY=<redacted>" in harness_text
    assert artifacts.persist_harness_artifacts(None, harness_dir) == {}
    assert (
        artifacts.persist_harness_artifacts(
            HarborResultManifest.for_trial(tmp_path / "missing", "codex-cli"),
            harness_dir,
        )
        == {}
    )

    harbor_dir = tmp_path / "harbor"
    harbor_dir.mkdir()
    (harbor_dir / "command.txt").write_text("harbor run", encoding="utf-8")
    harbor_artifacts = artifacts.persist_harbor_artifacts(harbor_result, harbor_dir)
    assert harbor_artifacts["command.txt"].endswith("command.txt")
    assert harbor_artifacts["raw_job_dir"] == str(tmp_path / "job")
    assert harbor_artifacts["raw_trial_dir"] == str(trial_dir)


def test_write_run_analysis_records_score_and_artifact_pointers(monkeypatch, tmp_path):
    layout = RunLayout(
        run_id="run-1",
        start_time=datetime(2026, 1, 1, tzinfo=UTC),
        run_label="label",
        root_dir=tmp_path / "run",
        workspace_dir=tmp_path / "workspace",
        runner_evidence_dir=tmp_path / "runner-evidence",
        scoring_dir=tmp_path / "scoring",
        harness_dir=tmp_path / "harness",
        harbor_dir=tmp_path / "harbor",
        run_json_path=tmp_path / "run.json",
        report_path=tmp_path / "report.md",
    )
    request = SimpleNamespace(
        scenario=SimpleNamespace(name="scenario"),
        config=SimpleNamespace(
            harness=SimpleNamespace(value="codex-cli"),
            model=SimpleNamespace(qualified_name="openai/gpt-5.5"),
        ),
    )
    scorecard = Scorecard(
        metadata={
            "evidence": {
                "homepage_post": "post.png",
                "final_workspace_archive": "final.tar.gz",
                "errors": ["none"],
            },
            "workspace": {
                "prune": {"removed": ["node_modules"], "reclaimed_bytes": 100},
                "changes": {
                    "changed_file_count": 2,
                    "changed_files": ["a", "b"],
                    "artifact": "workspace-diff.json",
                    "error": None,
                },
            },
        },
        execution_validity=ExecutionValidityScore(checks=[GateCheck(name="valid", passed=True)]),
        performance_gates=PerformanceGatesScore(checks=[GateCheck(name="perf", passed=True)]),
        scorer_results=[
            ScorerResult(scorer_id="quality", version=1, category="quality", weight=1, score=0.8)
        ],
    )
    monkeypatch.setattr(artifacts, "_harness_event_stream_pointer", lambda _dir, _harness: "stream")

    artifacts.write_run_analysis(
        layout,
        request,
        scorecard,
        HarborExecutionResult(False, None, tmp_path / "job", tmp_path / "trial"),
    )

    text = layout.report_path.read_text(encoding="utf-8")
    assert "- run_id: `run-1`" in text
    assert "- quality_score: `0.800000`" in text
    assert "- harness_event_stream: `stream`" in text
    assert "- workspace_changed_file_count: `2`" in text
