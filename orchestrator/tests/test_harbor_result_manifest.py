from pathlib import Path

import pytest

from raidar.harness import HarborArtifactRole, HarborResultManifest


def test_manifest_resolves_all_harbor_artifact_roles(tmp_path: Path) -> None:
    trial = tmp_path / "trial"
    manifest = HarborResultManifest.for_trial(trial, "codex-cli")

    assert manifest.path(HarborArtifactRole.TRIAL_ROOT) == trial
    assert manifest.path(HarborArtifactRole.TRIAL_RESULT) == trial / "result.json"
    assert manifest.path(HarborArtifactRole.HARNESS_OUTPUT_ROOT) == trial / "agent"
    assert manifest.path(HarborArtifactRole.RUNNER_EVIDENCE_ROOT) == trial / "verifier"
    assert manifest.path(HarborArtifactRole.RUNNER_EVIDENCE) == trial / "verifier/evidence.json"
    assert manifest.path(HarborArtifactRole.RUNNER_ACTION_LOGS) == trial / "verifier/action-logs"
    assert manifest.path(HarborArtifactRole.HARNESS_EVENT_STREAM) == trial / "agent/codex.txt"
    assert (
        manifest.path(HarborArtifactRole.FINAL_WORKSPACE_ARCHIVE)
        == trial / "agent/final-app.tar.gz"
    )


def test_manifest_validates_declared_and_relative_artifacts(tmp_path: Path) -> None:
    manifest = HarborResultManifest.for_trial(tmp_path / "trial", "gemini")

    assert manifest.harness_artifact("gemini-cli.txt").name == "gemini-cli.txt"
    assert manifest.runner_evidence_artifact("action-logs/lint.json").is_relative_to(
        tmp_path / "trial/verifier"
    )
    with pytest.raises(ValueError, match="does not declare artifact"):
        manifest.harness_artifact("unknown.txt")
    with pytest.raises(ValueError, match="Invalid runner evidence"):
        manifest.runner_evidence_artifact("../escape")


def test_manifest_discovers_harbor_trial_shape(tmp_path: Path) -> None:
    trial = tmp_path / "trial"
    assert HarborResultManifest.looks_like_trial(trial) is False
    (trial / "agent").mkdir(parents=True)
    assert HarborResultManifest.looks_like_trial(trial) is True


def test_manifest_resolves_container_roles_and_rejects_unsupported_roles() -> None:
    assert HarborResultManifest.container_path(HarborArtifactRole.RUNNER_EVIDENCE) == Path(
        "/logs/verifier/evidence.json"
    )
    assert HarborResultManifest.container_path(
        HarborArtifactRole.HARNESS_EVENT_STREAM, harness="codex-cli"
    ) == Path("/logs/agent/codex.txt")
    assert HarborResultManifest.container_path(
        HarborArtifactRole.FINAL_WORKSPACE_ARCHIVE, harness="codex-cli"
    ) == Path("/logs/agent/final-app.tar.gz")
    with pytest.raises(ValueError, match="requires a harness"):
        HarborResultManifest.container_path(HarborArtifactRole.TRIAL_ROOT)
    with pytest.raises(ValueError, match="no container path"):
        HarborResultManifest.container_path(HarborArtifactRole.TRIAL_ROOT, harness="codex-cli")
    with pytest.raises(ValueError, match="requires a harness definition"):
        HarborResultManifest.unbound_path(Path("trial"), HarborArtifactRole.HARNESS_EVENT_STREAM)


def test_manifest_lists_stable_command_and_stdout_paths(tmp_path: Path) -> None:
    manifest = HarborResultManifest.for_trial(tmp_path / "trial", "codex-cli")
    agent = manifest.path(HarborArtifactRole.HARNESS_OUTPUT_ROOT)
    (agent / "command-2").mkdir(parents=True)
    (agent / "command-1").mkdir()
    (agent / "command-file").write_text("not a directory", encoding="utf-8")
    for name in ("command-2/stdout.txt", "command-1/stdout.txt"):
        (agent / name).write_text(name, encoding="utf-8")

    assert manifest.command_dirs() == (agent / "command-1", agent / "command-2")
    assert manifest.command_stdout_paths() == (
        agent / "command-1/stdout.txt",
        agent / "command-2/stdout.txt",
    )
    assert manifest.setup_dir == agent / "setup"
    assert all(path.parent == agent for path in manifest.additional_stdout_paths())
    with pytest.raises(ValueError, match="Invalid runner evidence"):
        manifest.runner_evidence_artifact("/absolute")
