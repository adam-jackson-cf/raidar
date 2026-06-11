import json
from types import SimpleNamespace

from raidar.runtime import artifact_phase


def _phase(tmp_path, *, screenshot_command=("capture",), evidence_errors=("pre",)):
    workspace = tmp_path / "workspace"
    baseline = tmp_path / "baseline"
    root = tmp_path / "run"
    for path in (workspace, baseline, root):
        path.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        screenshot_command=screenshot_command,
        evidence_errors=evidence_errors,
        layout=SimpleNamespace(
            workspace_dir=workspace,
            root_dir=root,
            verifier_dir=root / "verifier",
            harness_dir=root / "harness",
            harbor_dir=root / "harbor",
        ),
        context=SimpleNamespace(workspace=workspace, baseline_workspace=baseline),
    )


def _request(retained_files=()):
    return SimpleNamespace(
        scenario=SimpleNamespace(
            evidence=SimpleNamespace(
                retained_files=[SimpleNamespace(path=path) for path in retained_files]
            )
        )
    )


def test_initial_evidence_artifacts_preserves_command_and_errors(tmp_path):
    phase = _phase(tmp_path, screenshot_command=("bun", "capture"), evidence_errors=("old",))

    evidence = artifact_phase._initial_evidence_artifacts(phase)

    assert evidence["screenshot_command"] == ["bun", "capture"]
    assert evidence["errors"] == ["old"]
    assert evidence["visual"]["regions"] == []
    phase.screenshot_command = None
    assert artifact_phase._initial_evidence_artifacts(phase)["screenshot_command"] is None


def test_hydration_skipped_for_terminated_runs(monkeypatch, tmp_path):
    phase = _phase(tmp_path)
    execution = SimpleNamespace(terminated_early=True)
    monkeypatch.setattr(
        artifact_phase,
        "_hydrate_workspace_from_final_app",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not hydrate")),
    )
    evidence = artifact_phase._initial_evidence_artifacts(phase)

    assert artifact_phase._hydrate_final_workspace(phase, execution, evidence) is False
    assert evidence["final_workspace_archive"] is None


def test_hydration_records_archive_for_non_visual_runs(monkeypatch, tmp_path):
    phase = _phase(tmp_path, screenshot_command=None)
    execution = SimpleNamespace(terminated_early=False, harbor_result=SimpleNamespace())
    archive = tmp_path / "trial" / "agent" / "final-app.tar.gz"
    monkeypatch.setattr(
        artifact_phase,
        "_hydrate_workspace_from_final_app",
        lambda harbor_result, workspace: (archive, None),
    )
    evidence = artifact_phase._initial_evidence_artifacts(phase)

    assert artifact_phase._hydrate_final_workspace(phase, execution, evidence) is True
    assert evidence["final_workspace_archive"] == str(archive)


def test_hydration_failure_appends_error_and_skips_archive(monkeypatch, tmp_path):
    phase = _phase(tmp_path)
    execution = SimpleNamespace(terminated_early=False, harbor_result=SimpleNamespace())
    monkeypatch.setattr(
        artifact_phase,
        "_hydrate_workspace_from_final_app",
        lambda harbor_result, workspace: (None, "hydrate failed"),
    )
    evidence = artifact_phase._initial_evidence_artifacts(phase)

    assert artifact_phase._hydrate_final_workspace(phase, execution, evidence) is False
    assert evidence["errors"][-1] == "hydrate failed"
    assert evidence["final_workspace_archive"] is None


def test_visual_evidence_records_capture_artifacts_and_rebinds(monkeypatch, tmp_path):
    phase = _phase(tmp_path)
    visual = SimpleNamespace(actual_path=None)
    execution = SimpleNamespace(
        harbor_result=SimpleNamespace(),
        outputs=SimpleNamespace(visual=visual),
    )
    evidence = artifact_phase._initial_evidence_artifacts(phase)
    calls: list[str] = []

    monkeypatch.setattr(
        artifact_phase,
        "_run_homepage_capture_command",
        lambda command, workspace, output: (output, "capture stderr"),
    )
    monkeypatch.setattr(
        artifact_phase,
        "_persist_visual_evidence_artifacts",
        lambda request: {"actual": "actual.png", "regions": []},
    )
    monkeypatch.setattr(
        artifact_phase,
        "_rebind_visual_evidence_paths",
        lambda score, evidence_paths: calls.append(f"{score is visual}:{evidence_paths['actual']}"),
    )

    artifact_phase._persist_visual_evidence(SimpleNamespace(), phase, execution, evidence)

    assert evidence["homepage_post"].endswith("homepage-post.png")
    assert evidence["errors"][-1].startswith("homepage-post capture failed")
    assert evidence["visual"] == {"actual": "actual.png", "regions": []}
    assert calls == ["True:actual.png"]


def test_retained_evidence_ingests_declared_json_fields(tmp_path):
    phase = _phase(tmp_path)
    evidence_file = phase.context.workspace / "evidence" / "defect-evidence.json"
    evidence_file.parent.mkdir(parents=True)
    evidence_file.write_text(
        json.dumps(
            {
                "reproduction_note": "  bug reproduced via failing sumLedger test  ",
                "regression_tests": ["src/test/ledger.regression.test.ts", 7, "  "],
                "errors": ["spoofed platform key"],
                "nested": {"not": "ingested"},
            }
        ),
        encoding="utf-8",
    )
    evidence = artifact_phase._initial_evidence_artifacts(phase)

    artifact_phase._ingest_retained_evidence(
        _request(retained_files=("evidence/defect-evidence.json",)), phase, evidence
    )

    assert evidence["reproduction_note"] == "bug reproduced via failing sumLedger test"
    assert evidence["regression_tests"] == ["src/test/ledger.regression.test.ts"]
    assert evidence["errors"] == ["pre"]
    assert "nested" not in evidence
    assert evidence["retained_files"] == [
        {
            "path": "evidence/defect-evidence.json",
            "status": "ingested",
            "keys": ["reproduction_note", "regression_tests"],
        }
    ]


def test_retained_evidence_records_missing_oversize_and_invalid_files(tmp_path):
    phase = _phase(tmp_path)
    oversize = phase.context.workspace / "oversize.json"
    oversize.write_text("x" * (artifact_phase.MAX_EVIDENCE_FILE_BYTES + 1), encoding="utf-8")
    non_object = phase.context.workspace / "list.json"
    non_object.write_text("[1, 2]", encoding="utf-8")
    malformed = phase.context.workspace / "broken.json"
    malformed.write_text("{not json", encoding="utf-8")
    evidence = artifact_phase._initial_evidence_artifacts(phase)

    artifact_phase._ingest_retained_evidence(
        _request(retained_files=("absent.json", "oversize.json", "list.json", "broken.json")),
        phase,
        evidence,
    )

    statuses = {record["path"]: record["status"] for record in evidence["retained_files"]}
    assert statuses["absent.json"] == "missing"
    assert statuses["oversize.json"].startswith("oversize:")
    assert statuses["list.json"].startswith("invalid:")
    assert statuses["broken.json"].startswith("unreadable:")
    assert all(record["keys"] == [] for record in evidence["retained_files"])


def test_retained_evidence_noop_without_declared_files(tmp_path):
    phase = _phase(tmp_path)
    evidence = artifact_phase._initial_evidence_artifacts(phase)

    artifact_phase._ingest_retained_evidence(_request(), phase, evidence)

    assert "retained_files" not in evidence


def test_sanitize_evidence_value_truncates_and_rejects_non_text():
    long_text = "a" * (artifact_phase.MAX_EVIDENCE_TEXT_CHARS + 10)
    assert artifact_phase._sanitize_evidence_value(long_text) == (
        "a" * artifact_phase.MAX_EVIDENCE_TEXT_CHARS
    )
    assert artifact_phase._sanitize_evidence_value("   ") is None
    assert artifact_phase._sanitize_evidence_value(42) is None
    assert artifact_phase._sanitize_evidence_value({"k": "v"}) is None
    oversized_list = ["item"] * (artifact_phase.MAX_EVIDENCE_LIST_ITEMS + 5)
    assert len(artifact_phase._sanitize_evidence_value(oversized_list)) == (
        artifact_phase.MAX_EVIDENCE_LIST_ITEMS
    )


def test_persist_artifacts_phase_assembles_all_artifact_sections(monkeypatch, tmp_path):
    phase = _phase(tmp_path, screenshot_command=None)
    request = _request(retained_files=("evidence/defect-evidence.json",))
    execution = SimpleNamespace(harbor_result=SimpleNamespace(), terminated_early=False)
    evidence_file = phase.context.workspace / "evidence" / "defect-evidence.json"
    evidence_file.parent.mkdir(parents=True)
    evidence_file.write_text(json.dumps({"reproduction_note": "note"}), encoding="utf-8")

    monkeypatch.setattr(
        artifact_phase,
        "_hydrate_workspace_from_final_app",
        lambda harbor_result, workspace: (None, "hydrate error"),
    )
    monkeypatch.setattr(
        artifact_phase, "_prune_workspace_artifacts", lambda _workspace: {"removed": []}
    )
    monkeypatch.setattr(
        artifact_phase,
        "_workspace_changes_from_baseline",
        lambda **_kwargs: {"changed_file_count": 0},
    )
    monkeypatch.setattr(artifact_phase, "build_starter_meta", lambda *_args: {"starter": "meta"})
    monkeypatch.setattr(
        artifact_phase,
        "build_scenario_revision_meta",
        lambda *_args: {"scenario": "meta"},
    )
    monkeypatch.setattr(artifact_phase, "persist_verifier_artifacts", lambda *_args: {"v": "1"})
    monkeypatch.setattr(artifact_phase, "persist_harness_artifacts", lambda *_args: {"h": "1"})
    monkeypatch.setattr(artifact_phase, "persist_harbor_artifacts", lambda *_args: {"r": "1"})

    persisted = artifact_phase.persist_artifacts_phase(request, phase, execution)

    assert persisted.evidence_artifacts["errors"] == ["pre", "hydrate error"]
    assert persisted.evidence_artifacts["reproduction_note"] == "note"
    assert persisted.starter_meta == {"starter": "meta"}
    assert persisted.scenario_revision_meta == {"scenario": "meta"}
    assert persisted.verifier_artifacts == {"v": "1"}
