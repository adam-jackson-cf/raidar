"""Tests for the bundled evidence runner asset."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from raidar.assets import evidence_runner


def _write_plan(tmp_path: Path, plan: dict) -> Path:
    path = tmp_path / "verification-plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def test_evidence_runner_executes_actions_and_writes_stable_outputs(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    output_dir = tmp_path / "out"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (workspace / "payload.json").write_text(
        json.dumps({"token": "abcdefghijklmnop", "ok": True}),
        encoding="utf-8",
    )
    (workspace / "actual.png").write_bytes(b"actual")
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "ignored.py").write_text("ignored\n", encoding="utf-8")

    plan_path = _write_plan(
        tmp_path,
        {
            "workspace": str(workspace),
            "actions": [
                {
                    "id": "cmd",
                    "kind": "command",
                    "command": [
                        "python3",
                        "-c",
                        "print('TOKEN=abcdefghijklmnop')",
                    ],
                    "timeout_sec": 5,
                    "required": True,
                },
                {"id": "text", "kind": "read_file", "path": "src/app.py"},
                {"id": "json", "kind": "read_json", "path": "payload.json"},
                {"id": "glob", "kind": "glob", "pattern": "**/*.py"},
                {
                    "id": "visual",
                    "kind": "visual_diff",
                    "actual": "actual.png",
                    "expected": "missing.png",
                },
                {
                    "id": "archive",
                    "kind": "archive",
                    "paths": ["src", "node_modules"],
                    "archive": "bundle.tar.gz",
                },
            ],
            "artifacts": [{"path": "bundle.tar.gz", "kind": "archive"}],
        },
    )

    evidence = evidence_runner.run_evidence_plan(plan_path, output_dir=output_dir)
    persisted = json.loads((output_dir / "evidence.json").read_text(encoding="utf-8"))

    assert evidence == persisted
    assert persisted["metadata"]["action_count"] == 6
    assert persisted["metadata"]["required_error_count"] == 0
    command = persisted["actions"][0]["result"]
    assert command["exit_code"] == 0
    assert "abcdefghijklmnop" not in command["stdout"]
    assert "..." in command["stdout"]
    assert persisted["actions"][1]["result"]["content"] == "print('ok')"
    assert persisted["actions"][2]["result"]["content"]["token"] == "abcd...mnop"
    assert persisted["actions"][3]["result"]["matches"] == ["src/app.py"]
    assert persisted["actions"][4]["result"]["actual"] == {
        "exists": True,
        "path": "actual.png",
    }
    assert persisted["actions"][4]["result"]["reference"] == {
        "exists": False,
        "path": "missing.png",
    }
    assert persisted["actions"][4]["result"]["global"] is None
    assert (output_dir / "action-logs" / "001-cmd.json").exists()

    archive_path = output_dir / "bundle.tar.gz"
    with tarfile.open(archive_path, "r:gz") as archive:
        assert archive.getnames() == ["src/app.py"]


def test_evidence_runner_accepts_normalized_config_actions(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    output_dir = tmp_path / "out"
    workspace.mkdir()
    (workspace / ".raidar").mkdir()
    (workspace / ".raidar" / "coverage.json").write_text(
        json.dumps({"totals": {"percent_covered": 80}}),
        encoding="utf-8",
    )
    plan_path = _write_plan(
        tmp_path,
        {
            "workspace": str(workspace),
            "actions": [
                {
                    "id": "python-code-task.coverage.report",
                    "kind": "read_json",
                    "required": False,
                    "config": {"path": ".raidar/coverage.json"},
                },
                {
                    "id": "python-code-task.sources",
                    "kind": "glob",
                    "config": {"pattern": "**/*.json"},
                },
            ],
        },
    )

    evidence = evidence_runner.run_evidence_plan(plan_path, output_dir=output_dir)

    assert evidence["actions"][0]["result"]["content"]["totals"]["percent_covered"] == 80
    assert evidence["actions"][1]["result"]["matches"] == []


def test_evidence_runner_computes_visual_diff_with_odiff_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    output_dir = tmp_path / "out"
    workspace.mkdir()
    for name in ("reference.png", "actual.png", "reference-region.png", "actual-region.png"):
        (workspace / name).write_bytes(name.encode())

    def fake_run(command, **_kwargs):
        Path(command[4]).write_bytes(b"diff")
        return SimpleNamespace(returncode=1, stdout="12.5% pixels differ", stderr="")

    monkeypatch.setattr(evidence_runner.subprocess, "run", fake_run)
    plan_path = _write_plan(
        tmp_path,
        {
            "workspace": str(workspace),
            "actions": [
                {
                    "id": "visual",
                    "kind": "visual_diff",
                    "config": {
                        "reference": "reference.png",
                        "actual": "actual.png",
                        "diff": "diff.png",
                        "regions": [
                            {
                                "name": "hero",
                                "reference_image": "reference-region.png",
                                "actual_image": "actual-region.png",
                                "diff_image": "diff-region.png",
                                "weight": 2,
                            }
                        ],
                    },
                }
            ],
        },
    )

    evidence = evidence_runner.run_evidence_plan(plan_path, output_dir=output_dir)
    result = evidence["actions"][0]["result"]

    assert result["global"]["similarity"] == 0.875
    assert result["global"]["diff_path"] == "diff.png"
    assert result["regions"][0]["normalized_weight"] == 1.0
    assert result["regions"][0]["comparison"]["similarity"] == 0.875
    assert result["available_region_count"] == 1


def test_evidence_runner_resolves_relative_workspace_and_zero_similarity_visual_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "reference.png").write_bytes(b"reference")
    (workspace / "actual.png").write_bytes(b"actual")

    def fake_run(command, **_kwargs):
        Path(command[4]).write_bytes(b"diff")
        return SimpleNamespace(returncode=1, stdout="images differ", stderr="")

    monkeypatch.setattr(evidence_runner.subprocess, "run", fake_run)
    plan_path = _write_plan(
        tmp_path,
        {
            "workspace": "workspace",
            "output_dir": str(tmp_path / "out"),
            "actions": [
                {
                    "id": "visual",
                    "kind": "visual_diff",
                    "config": {
                        "reference": "reference.png",
                        "actual": "actual.png",
                        "diff": "diff.png",
                        "regions": "not-a-list",
                    },
                }
            ],
        },
    )

    evidence = evidence_runner.run_evidence_plan(plan_path)
    result = evidence["actions"][0]["result"]

    assert result["global"]["similarity"] == 0.0
    assert result["regions"] == []
    assert (tmp_path / "out" / "evidence.json").exists()


def test_evidence_runner_records_required_action_errors_without_scorecard_fields(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    output_dir = tmp_path / "out"
    workspace.mkdir()
    plan_path = _write_plan(
        tmp_path,
        {
            "workspace": str(workspace),
            "evidence": {
                "actions": [
                    {
                        "id": "missing",
                        "kind": "read_file",
                        "path": "missing.txt",
                        "required": True,
                    },
                    {"id": "bad-kind", "kind": "scorecard", "required": False},
                ],
                "artifacts": [{"path": "evidence.json"}],
            },
        },
    )

    evidence = evidence_runner.run_evidence_plan(plan_path, output_dir=output_dir)

    assert evidence["metadata"]["error_count"] == 2
    assert evidence["metadata"]["required_error_count"] == 1
    assert evidence["actions"][0]["status"] == "error"
    assert evidence["actions"][0]["error"]["type"] == "FileNotFoundError"
    assert evidence["actions"][1]["error"]["message"] == (
        "unsupported evidence action kind: scorecard"
    )
    assert "scorecard" not in evidence
    assert "metric_scores" not in evidence
    assert "checks" not in evidence


def test_evidence_runner_bounds_file_reads_and_rejects_workspace_escape(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "large.txt").write_text("abcdef", encoding="utf-8")
    plan_path = _write_plan(
        tmp_path,
        {
            "workspace": str(workspace),
            "actions": [
                {"id": "large", "kind": "read_file", "path": "large.txt", "max_bytes": 3},
                {"id": "escape", "kind": "read_file", "path": "../outside.txt", "required": True},
            ],
        },
    )

    evidence = evidence_runner.run_evidence_plan(plan_path, output_dir=tmp_path / "out")

    assert evidence["actions"][0]["result"]["content"] == "abc"
    assert evidence["actions"][0]["result"]["truncated"] is True
    assert evidence["actions"][1]["status"] == "error"
    assert evidence["actions"][1]["error"]["message"] == "path escapes workspace: ../outside.txt"


def test_evidence_runner_handles_plan_fallbacks_and_command_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    plan_path = _write_plan(
        tmp_path,
        {
            "output_dir": str(tmp_path / "out"),
            "evidence_actions": [
                {"name": "invalid-command", "kind": "command", "command": []},
                {
                    "id": "timeout",
                    "kind": "command",
                    "config": {
                        "argv": [
                            "python3",
                            "-c",
                            "import time; time.sleep(1)",
                        ]
                    },
                    "timeout_sec": 0.01,
                },
                {
                    "id": "missing-bin",
                    "kind": "command",
                    "config": {"argv": ["definitely-missing-raidar-command"]},
                },
            ],
        },
    )

    evidence = evidence_runner.run_evidence_plan(plan_path)

    assert evidence["metadata"]["workspace"] == str(workspace.resolve())
    assert evidence["actions"][0]["id"] == "invalid-command"
    assert evidence["actions"][0]["error"]["message"] == "command action requires config.argv"
    assert evidence["actions"][1]["result"]["exit_code"] == 124
    assert evidence["actions"][1]["result"]["timed_out"] is True
    assert evidence["actions"][2]["result"]["exit_code"] == 127


def test_evidence_runner_handles_empty_and_invalid_plan_roots(tmp_path: Path) -> None:
    empty_plan = _write_plan(tmp_path, {"declared_artifacts": [{"path": "x"}]})

    evidence = evidence_runner.run_evidence_plan(empty_plan, output_dir=tmp_path / "out")

    assert evidence["actions"] == []
    assert evidence["artifacts"] == [{"path": "x"}]

    invalid_plan = tmp_path / "invalid-plan.json"
    invalid_plan.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="verification plan must be a JSON object"):
        evidence_runner.run_evidence_plan(invalid_plan)


def test_evidence_runner_archive_glob_and_secret_edge_branches(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    output_dir = tmp_path / "out"
    workspace.mkdir()
    (workspace / "single.txt").write_text("secret", encoding="utf-8")
    (workspace / "nested").mkdir()
    (workspace / "nested" / "item.txt").write_text("item", encoding="utf-8")
    (workspace / "payload.json").write_text(
        json.dumps({"items": [{"pwd": "short"}, {"value": "plain"}]}),
        encoding="utf-8",
    )
    plan_path = _write_plan(
        tmp_path,
        {
            "app_dir": str(workspace),
            "actions": [
                {"id": "json", "kind": "read_json", "path": "payload.json"},
                {"id": "bad-glob", "kind": "glob", "pattern": "../*.py"},
                {
                    "id": "bad-archive",
                    "kind": "archive",
                    "paths": "not-a-list",
                },
                {
                    "id": "archive-file",
                    "kind": "archive",
                    "config": {
                        "path": "single.txt",
                        "archive": "single.tar.gz",
                    },
                },
                {
                    "id": "archive-missing",
                    "kind": "archive",
                    "config": {
                        "paths": ["missing", "nested"],
                        "archive": "nested.tar.gz",
                    },
                },
            ],
        },
    )

    evidence = evidence_runner.run_evidence_plan(plan_path, output_dir=output_dir)

    assert evidence["actions"][0]["result"]["content"]["items"][0]["pwd"] == "<redacted>"
    assert evidence["actions"][0]["result"]["content"]["items"][1]["value"] == "plain"
    assert evidence["actions"][1]["error"]["message"] == "glob pattern must be workspace-relative"
    assert evidence["actions"][2]["error"]["message"] == "archive action requires paths list"
    assert evidence["actions"][3]["result"]["archived"] == ["single.txt"]
    assert evidence["actions"][4]["result"]["archived"] == ["nested/item.txt"]


def test_evidence_runner_main_uses_cli_args(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    output_dir = tmp_path / "out"
    workspace.mkdir()
    plan_path = _write_plan(tmp_path, {"workspace_dir": str(workspace), "actions": []})

    assert evidence_runner.main([str(plan_path), "--output-dir", str(output_dir)]) == 0
    assert (output_dir / "evidence.json").exists()
