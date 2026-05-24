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


def test_initial_evidence_artifacts_preserves_command_and_errors(tmp_path):
    phase = _phase(tmp_path, screenshot_command=("bun", "capture"), evidence_errors=("old",))

    evidence = artifact_phase._initial_evidence_artifacts(phase)

    assert evidence["screenshot_command"] == ["bun", "capture"]
    assert evidence["errors"] == ["old"]
    assert evidence["visual"]["regions"] == []
    phase.screenshot_command = None
    assert artifact_phase._initial_evidence_artifacts(phase)["screenshot_command"] is None


def test_visual_evidence_skips_hydration_without_command_or_when_terminated(monkeypatch, tmp_path):
    phase = _phase(tmp_path, screenshot_command=None)
    execution = SimpleNamespace(terminated_early=False)
    monkeypatch.setattr(
        artifact_phase,
        "_persist_hydrated_visual_evidence",
        lambda _request: (_ for _ in ()).throw(AssertionError("should not hydrate")),
    )

    state = artifact_phase._persist_visual_evidence(SimpleNamespace(), phase, execution)

    assert state.hydrate_error is None
    assert state.evidence_artifacts["homepage_post"] is None

    phase.screenshot_command = ("capture",)
    execution.terminated_early = True
    state = artifact_phase._persist_visual_evidence(SimpleNamespace(), phase, execution)
    assert state.evidence_artifacts["final_workspace_archive"] is None


def test_hydrated_visual_evidence_records_archive_capture_visual_and_rebinds(monkeypatch, tmp_path):
    phase = _phase(tmp_path)
    visual = SimpleNamespace(actual_path=None)
    execution = SimpleNamespace(
        harbor_result=SimpleNamespace(),
        outputs=SimpleNamespace(visual=visual),
    )
    evidence_artifacts = artifact_phase._initial_evidence_artifacts(phase)
    archive = tmp_path / "trial" / "agent" / "final-app.tar.gz"
    archive.parent.mkdir(parents=True)
    archive.write_text("archive", encoding="utf-8")
    calls: list[str] = []

    monkeypatch.setattr(
        artifact_phase,
        "_hydrate_workspace_from_final_app",
        lambda harbor_result, workspace: (archive, None),
    )
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
        lambda score, evidence: calls.append(f"{score is visual}:{evidence['actual']}"),
    )

    state = artifact_phase._persist_hydrated_visual_evidence(
        artifact_phase._HydratedVisualEvidenceRequest(
            request=SimpleNamespace(),
            phase=phase,
            execution=execution,
            evidence_artifacts=evidence_artifacts,
        )
    )

    assert state.hydrate_error is None
    assert state.evidence_artifacts["final_workspace_archive"] == str(archive)
    assert state.evidence_artifacts["homepage_post"].endswith("homepage-post.png")
    assert state.evidence_artifacts["errors"][-1].startswith("homepage-post capture failed")
    assert calls == ["True:actual.png"]


def test_hydrated_visual_evidence_returns_hydrate_error_without_capture(monkeypatch, tmp_path):
    phase = _phase(tmp_path)
    execution = SimpleNamespace(
        harbor_result=SimpleNamespace(), outputs=SimpleNamespace(visual=None)
    )
    monkeypatch.setattr(
        artifact_phase,
        "_hydrate_workspace_from_final_app",
        lambda harbor_result, workspace: (None, "hydrate failed"),
    )
    monkeypatch.setattr(
        artifact_phase,
        "_run_homepage_capture_command",
        lambda *_args: (_ for _ in ()).throw(AssertionError("no capture")),
    )

    state = artifact_phase._persist_hydrated_visual_evidence(
        artifact_phase._HydratedVisualEvidenceRequest(
            request=SimpleNamespace(),
            phase=phase,
            execution=execution,
            evidence_artifacts=artifact_phase._initial_evidence_artifacts(phase),
        )
    )

    assert state.hydrate_error == "hydrate failed"


def test_persist_artifacts_phase_assembles_all_artifact_sections(monkeypatch, tmp_path):
    phase = _phase(tmp_path)
    request = SimpleNamespace()
    execution = SimpleNamespace(harbor_result=SimpleNamespace(), terminated_early=False)

    monkeypatch.setattr(
        artifact_phase,
        "_persist_visual_evidence",
        lambda _request, _phase, _execution: artifact_phase._VisualEvidenceState(
            evidence_artifacts={"errors": []},
            hydrate_error="hydrate error",
        ),
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

    assert persisted.evidence_artifacts["errors"] == ["hydrate error"]
    assert persisted.starter_meta == {"starter": "meta"}
    assert persisted.scenario_revision_meta == {"scenario": "meta"}
    assert persisted.verifier_artifacts == {"v": "1"}
