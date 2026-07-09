import errno
import io
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

from raidar.runtime import workspace_artifacts
from raidar.runtime.models import HarborExecutionResult


def _visual_scenario(
    reference_image: str,
    regions: list[dict] | None = None,
    capture_setup_actions: list[list[str]] | None = None,
):
    return SimpleNamespace(
        visual=SimpleNamespace(
            reference_image=reference_image,
            capture_setup_actions=capture_setup_actions or [],
            screenshot_command=["bun", "run", "capture"],
            artifact_manifest=SimpleNamespace(
                actual_image="actual-test.png",
                diff_image="diff-test.png",
                post_capture_image="post-test.png",
            ),
            regions=[SimpleNamespace(**region) for region in regions or []],
        )
    )


def _request(scenario, scenario_dir):
    return SimpleNamespace(scenario=scenario, scenario_dir=scenario_dir)


def test_visual_reference_assets_and_region_names_are_scenario_local(tmp_path):
    scenario_dir = tmp_path / "scenario"
    reference_dir = scenario_dir / "reference"
    reference_dir.mkdir(parents=True)
    (reference_dir / "page.png").write_text("main", encoding="utf-8")
    (reference_dir / "page-region-header.png").write_text("header", encoding="utf-8")
    (reference_dir / "page-region-footer.png").write_text("footer", encoding="utf-8")

    scenario = _visual_scenario(
        "reference/page.png",
        regions=[
            {
                "name": "footer",
                "reference_image": "reference/page-region-footer.png",
                "actual_image": "actual-region-footer.png",
                "diff_image": "diff-region-footer.png",
            },
            {
                "name": "header",
                "reference_image": "reference/page-region-header.png",
                "actual_image": "actual-region-header.png",
                "diff_image": "diff-region-header.png",
            },
        ],
    )
    request = _request(scenario, scenario_dir)

    assert [
        relative.as_posix()
        for _source, relative in workspace_artifacts._visual_reference_assets(request)
    ] == [
        "reference/page.png",
        "reference/page-region-footer.png",
        "reference/page-region-header.png",
    ]
    assert workspace_artifacts._visual_region_names(request) == ["footer", "header"]

    configured = _visual_scenario(
        "reference/page.png",
        regions=[
            {
                "name": "hero",
                "reference_image": "reference/page-region-hero.png",
                "actual_image": "actual-region-hero.png",
                "diff_image": "diff-region-hero.png",
            }
        ],
    )
    assert workspace_artifacts._visual_region_names(_request(configured, scenario_dir)) == ["hero"]

    absolute = _visual_scenario(str((scenario_dir / "reference" / "page.png").resolve()))
    assert workspace_artifacts._visual_reference_assets(_request(absolute, scenario_dir)) == []
    missing = _visual_scenario("reference/missing.png")
    assert workspace_artifacts._visual_reference_assets(_request(missing, scenario_dir)) == []
    no_visual = SimpleNamespace(visual=None)
    assert workspace_artifacts._visual_reference_assets(_request(no_visual, scenario_dir)) == []
    assert workspace_artifacts._visual_region_names(_request(no_visual, scenario_dir)) == []


def test_homepage_capture_command_success_and_failure_modes(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output_path = tmp_path / "out" / "actual-test.png"
    scenario = _visual_scenario("reference/page.png")
    monkeypatch.setattr(
        workspace_artifacts, "_ensure_workspace_capture_dependencies", lambda *_args: None
    )

    def successful_run(*_args, **_kwargs):
        (workspace / "actual-test.png").write_text("png", encoding="utf-8")
        return subprocess.CompletedProcess(["capture"], 0, stdout="ok", stderr="")

    monkeypatch.setattr(workspace_artifacts.subprocess, "run", successful_run)
    actual, error = workspace_artifacts._run_homepage_capture_command(
        scenario, ["bun", "run", "capture"], workspace, output_path
    )
    assert actual == output_path
    assert error is None
    assert output_path.read_text(encoding="utf-8") == "png"
    assert not (workspace / "actual-test.png").exists()

    monkeypatch.setattr(
        workspace_artifacts,
        "_ensure_workspace_capture_dependencies",
        lambda *_args: "dependency failure",
    )
    assert workspace_artifacts._run_homepage_capture_command(
        scenario, ["cmd"], workspace, output_path
    ) == (None, "dependency failure")

    monkeypatch.setattr(
        workspace_artifacts, "_ensure_workspace_capture_dependencies", lambda *_args: None
    )
    monkeypatch.setattr(
        workspace_artifacts.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["capture"], 3, stdout="out", stderr="err"
        ),
    )
    assert (
        "`bun run capture` exited 3: out\nerr"
        in workspace_artifacts._run_homepage_capture_command(
            scenario, ["bun", "run", "capture"], workspace, output_path
        )[1]
    )

    monkeypatch.setattr(
        workspace_artifacts.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(["capture"], 0, stdout="", stderr=""),
    )
    assert (
        "completed without producing"
        in workspace_artifacts._run_homepage_capture_command(
            scenario, ["capture"], workspace, output_path
        )[1]
    )

    def missing_command(*_args, **_kwargs):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(workspace_artifacts.subprocess, "run", missing_command)
    assert (
        "missing"
        in workspace_artifacts._run_homepage_capture_command(
            scenario, ["missing"], workspace, output_path
        )[1]
    )


def test_capture_dependency_install_is_conditional_and_reports_failures(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    no_setup = _visual_scenario("reference/page.png")
    assert workspace_artifacts._ensure_workspace_capture_dependencies(no_setup, workspace) is None

    setup_scenario = _visual_scenario(
        "reference/page.png",
        capture_setup_actions=[["bun", "install", "--frozen-lockfile"]],
    )
    monkeypatch.setattr(
        workspace_artifacts.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["bun"], 0, stdout="installed", stderr=""
        ),
    )
    assert (
        workspace_artifacts._ensure_workspace_capture_dependencies(setup_scenario, workspace)
        is None
    )

    monkeypatch.setattr(
        workspace_artifacts.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["bun"], 1, stdout="out", stderr="err"
        ),
    )
    assert "exited 1: out\nerr" in (
        workspace_artifacts._ensure_workspace_capture_dependencies(setup_scenario, workspace)
    )

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["bun"], timeout=1)

    monkeypatch.setattr(workspace_artifacts.subprocess, "run", timeout)
    assert "Failed to run visual capture setup" in (
        workspace_artifacts._ensure_workspace_capture_dependencies(setup_scenario, workspace)
    )


def test_safe_extract_tarball_accepts_files_and_rejects_unsafe_members(tmp_path):
    archive = tmp_path / "archive.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("nested/file.txt")
        data = b"content"
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    target = tmp_path / "target"
    workspace_artifacts._safe_extract_tarball(archive, target)
    assert (target / "nested" / "file.txt").read_text(encoding="utf-8") == "content"

    unsafe = tmp_path / "unsafe.tar.gz"
    with tarfile.open(unsafe, "w:gz") as tar:
        info = tarfile.TarInfo("../escape.txt")
        data = b"bad"
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    with pytest.raises(RuntimeError, match="Unsafe tar member path"):
        workspace_artifacts._safe_extract_tarball(unsafe, tmp_path / "unsafe-target")

    link_archive = tmp_path / "link.tar.gz"
    with tarfile.open(link_archive, "w:gz") as tar:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        tar.addfile(info)
    with pytest.raises(RuntimeError, match="Unsupported tar member type"):
        workspace_artifacts._safe_extract_tarball(link_archive, tmp_path / "link-target")


def test_hydrate_workspace_reports_missing_trial_archive_and_extract_errors(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    result = HarborExecutionResult(
        terminated_early=False,
        termination_reason=None,
        job_dir=tmp_path / "job",
        trial_dir=None,
    )
    assert workspace_artifacts._hydrate_workspace_from_final_app(
        result, workspace, harness="codex-cli"
    ) == (
        None,
        "Harbor trial directory missing; cannot hydrate post-run workspace.",
    )

    trial_dir = tmp_path / "trial"
    result = HarborExecutionResult(False, None, tmp_path / "job", trial_dir)
    assert (
        "Missing final app archive"
        in workspace_artifacts._hydrate_workspace_from_final_app(
            result, workspace, harness="codex-cli"
        )[1]
    )

    archive = trial_dir / "agent" / "final-app.tar.gz"
    archive.parent.mkdir(parents=True)
    archive.write_text("not a tarball", encoding="utf-8")
    assert (
        "Failed to hydrate workspace"
        in workspace_artifacts._hydrate_workspace_from_final_app(
            result, workspace, harness="codex-cli"
        )[1]
    )

    monkeypatch.setattr(workspace_artifacts, "_safe_extract_tarball", lambda _a, _w: None)
    assert workspace_artifacts._hydrate_workspace_from_final_app(
        result, workspace, harness="codex-cli"
    ) == (
        archive,
        None,
    )


def test_remove_tree_retries_transient_errors_and_prune_reports_bytes(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    removable = workspace / "node_modules"
    removable.mkdir(parents=True)
    (removable / "file").write_text("1234", encoding="utf-8")
    calls = {"count": 0}

    def flaky_rmtree(path):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError(errno.ENOTEMPTY, "busy")
        for child in path.iterdir():
            child.unlink()
        path.rmdir()

    monkeypatch.setattr(workspace_artifacts, "_directory_size_bytes", lambda _path: 4)
    monkeypatch.setattr(workspace_artifacts.shutil, "rmtree", flaky_rmtree)
    monkeypatch.setattr(workspace_artifacts, "wait_for_remove_tree_retry", lambda _delay: None)

    result = workspace_artifacts._prune_workspace_artifacts(workspace)

    assert result == {"removed": ["node_modules"], "reclaimed_bytes": 4}
    assert calls["count"] == 2

    monkeypatch.setattr(
        workspace_artifacts.shutil,
        "rmtree",
        lambda _path: (_ for _ in ()).throw(FileNotFoundError()),
    )
    workspace_artifacts._remove_tree_with_retries(workspace / "missing")


def test_workspace_changes_reports_missing_baseline_and_writes_diff_artifact(tmp_path):
    run_workspace = tmp_path / "run"
    run_root = tmp_path / "run-root"
    run_workspace.mkdir()
    run_root.mkdir()

    missing = workspace_artifacts._workspace_changes_from_baseline(
        baseline_workspace=tmp_path / "missing",
        run_workspace=run_workspace,
        run_root_dir=run_root,
    )
    assert missing["error"].startswith("Missing baseline workspace")
    assert missing["changed_file_count"] == 0

    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "same.txt").write_text("same", encoding="utf-8")
    (run_workspace / "same.txt").write_text("changed", encoding="utf-8")
    (run_workspace / "added.txt").write_text("added", encoding="utf-8")

    changes = workspace_artifacts._workspace_changes_from_baseline(
        baseline_workspace=baseline,
        run_workspace=run_workspace,
        run_root_dir=run_root,
    )

    assert changes["changed_file_count"] == 2
    assert changes["error"] is None
    artifact = run_root / "workspace-diff.json"
    assert changes["artifact"] == str(artifact)
    assert '"added.txt"' in artifact.read_text(encoding="utf-8")
