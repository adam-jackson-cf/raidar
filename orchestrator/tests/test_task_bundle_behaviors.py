import json
from pathlib import Path
from types import SimpleNamespace

from raidar.runtime import task_bundle


class _Contract:
    harness = None
    runtime_profile = SimpleNamespace(
        archive_excludes=(),
        copy_excludes=(),
        docker_registry_allowlist=(),
    )

    @staticmethod
    def evidence_runner_payload(*, workspace: str, output_dir: str) -> dict[str, object]:
        return {
            "runner_contract": "python-evidence-runner@2",
            "workspace": workspace,
            "output_dir": output_dir,
            "actions": [],
            "aliases": {},
            "policy": {
                "file_bytes": 1,
                "text_chars": 1,
                "list_items": 1,
                "list_item_chars": 1,
                "archive_excludes": [],
            },
        }


def test_create_bundle_stages_immutable_baseline_workspace(tmp_path: Path, monkeypatch):
    baseline_workspace = tmp_path / "baseline-workspace"
    baseline_workspace.mkdir()
    (baseline_workspace / "starter.txt").write_text("baseline", encoding="utf-8")
    candidate_workspace = tmp_path / "candidate-workspace"
    candidate_workspace.mkdir()
    (candidate_workspace / "starter.txt").write_text("candidate", encoding="utf-8")
    (candidate_workspace / "candidate-only.txt").write_text("candidate", encoding="utf-8")
    request = SimpleNamespace(
        scenario=SimpleNamespace(visual=None),
        scenario_dir=tmp_path,
        config=SimpleNamespace(harness=SimpleNamespace(value="codex-cli")),
    )
    context = SimpleNamespace(
        baseline_workspace=baseline_workspace,
        workspace=candidate_workspace,
        injected_rules=None,
    )
    contract = SimpleNamespace(
        environment=SimpleNamespace(),
        runtime_profile=_Contract.runtime_profile,
    )
    monkeypatch.setattr(task_bundle, "_load_scenario_prompt", lambda *_: "prompt")
    monkeypatch.setattr(task_bundle, "_render_environment_dockerfile", lambda *_a, **_k: "")
    monkeypatch.setattr(task_bundle, "_validate_public_base_images", lambda *_a, **_k: None)
    monkeypatch.setattr(task_bundle, "_write_runner_evidence_artifacts", lambda *_: None)
    monkeypatch.setattr(task_bundle, "task_image_reference", lambda *_: None)
    monkeypatch.setattr(task_bundle, "_render_task_toml", lambda *_: "")

    bundle_dir = task_bundle.create_harbor_task_bundle(
        request,
        context,
        tmp_path / "bundle",
        contract,
    )

    baseline_staged = bundle_dir / "tests" / "baseline-workspace" / "starter.txt"
    candidate_staged = bundle_dir / "environment" / "app" / "starter.txt"
    assert baseline_staged.read_text(encoding="utf-8") == "baseline"
    assert not (baseline_staged.parent / "candidate-only.txt").exists()
    assert candidate_staged.read_text(encoding="utf-8") == "candidate"


def test_runner_evidence_artifacts_render_baseline_and_final_plans(tmp_path: Path):
    task_bundle._write_runner_evidence_artifacts(
        SimpleNamespace(),
        tmp_path,
        _Contract(),
    )

    baseline_plan = json.loads(
        (tmp_path / "baseline-verification-plan.json").read_text(encoding="utf-8")
    )
    final_plan = json.loads((tmp_path / "verification-plan.json").read_text(encoding="utf-8"))
    script = (tmp_path / "test.sh").read_text(encoding="utf-8")

    assert baseline_plan["workspace"] == "/tests/baseline-workspace"
    assert baseline_plan["output_dir"] == "/logs/verifier/baseline"
    assert final_plan["workspace"] == "/app"
    assert final_plan["output_dir"] == "/logs/verifier"
    assert '"$SCRIPT_DIR/baseline-verification-plan.json"' in script
    assert '"$SCRIPT_DIR/verification-plan.json"' in script
