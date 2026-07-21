"""Tests for the strict bundled evidence runner asset."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from raidar.assets import evidence_runner
from raidar.scorers.evidence_contract import EvidenceOutput


def _action(
    action_id: str,
    kind: str,
    config: dict,
    *,
    timeout_sec: int | None = None,
    required: bool = True,
    depends_on: list[str] | None = None,
) -> dict:
    return {
        "id": action_id,
        "kind": kind,
        "timeout_sec": timeout_sec,
        "required": required,
        "depends_on": depends_on or [],
        "config": config,
    }


def _write_plan(
    tmp_path: Path,
    workspace: Path,
    actions: list[dict],
    *,
    aliases: dict[str, str] | None = None,
    output_dir: Path | None = None,
    limits: dict[str, int] | None = None,
    archive_excludes: list[str] | None = None,
) -> Path:
    path = tmp_path / "verification-plan.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "runner_contract": "python-evidence-runner@2",
                "workspace": str(workspace),
                "output_dir": str(output_dir or tmp_path / "out"),
                "actions": actions,
                "aliases": aliases or {action["id"]: action["id"] for action in actions},
                "limits": limits
                or {
                    "file_bytes": 65536,
                    "text_chars": 4000,
                    "list_items": 50,
                    "list_item_chars": 500,
                },
                "archive_excludes": archive_excludes or [".git", "node_modules", "__pycache__"],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_evidence_runner_executes_strict_actions_and_persists_aliases(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    output_dir = tmp_path / "out"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (workspace / "payload.json").write_text(
        json.dumps({"token": "abcdefghijklmnop", "ok": True}), encoding="utf-8"
    )
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "ignored.py").write_text("ignored\n", encoding="utf-8")
    actions = [
        _action(
            "owner.command",
            "command",
            {"argv": ["python3", "-c", "print('TOKEN=abcdefghijklmnop')"]},
            timeout_sec=5,
        ),
        _action("owner.text", "read_file", {"path": "src/app.py"}),
        _action("owner.json", "read_json", {"path": "payload.json"}),
        _action("owner.glob", "glob", {"pattern": "**/*.py"}),
        _action(
            "owner.archive",
            "archive",
            {"path": "src", "output": "bundle.tar.gz"},
        ),
    ]
    aliases = {
        "owner.command": "owner.command",
        "other.same-command": "owner.command",
        **{action["id"]: action["id"] for action in actions[1:]},
    }
    plan_path = _write_plan(tmp_path, workspace, actions, aliases=aliases, output_dir=output_dir)

    evidence = evidence_runner.run_evidence_plan(plan_path)
    persisted = json.loads((output_dir / "evidence.json").read_text(encoding="utf-8"))
    validated = EvidenceOutput.model_validate(persisted)

    assert evidence == persisted
    assert validated.aliases["other.same-command"] == "owner.command"
    assert persisted["metadata"] == {
        "action_count": 5,
        "error_count": 0,
        "required_error_count": 0,
        "workspace": str(workspace),
    }
    assert "abcdefghijklmnop" not in persisted["actions"][0]["result"]["stdout"]
    assert persisted["actions"][1]["result"]["content"] == "print('ok')"
    assert persisted["actions"][2]["result"]["content"]["token"] == "abcd...mnop"
    assert persisted["actions"][3]["result"]["matches"] == ["src/app.py"]
    assert persisted["artifacts"][0]["action_id"] == "owner.archive"
    with tarfile.open(output_dir / "bundle.tar.gz", "r:gz") as archive:
        assert archive.getnames() == ["src/app.py"]


def test_evidence_runner_executes_resolved_visual_tool_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("reference.png", "actual.png", "reference-region.png", "actual-region.png"):
        (workspace / name).write_bytes(name.encode())
    seen: list[list[str]] = []

    def fake_run(command, **kwargs):
        seen.append(command)
        (Path(kwargs["cwd"]) / command[4]).write_bytes(b"diff")
        return SimpleNamespace(returncode=1, stdout="12.5% pixels differ", stderr="")

    monkeypatch.setattr(evidence_runner.subprocess, "run", fake_run)
    action = _action(
        "design.visual",
        "visual_diff",
        {
            "reference": "reference.png",
            "actual": "actual.png",
            "diff": "diff.png",
            "argv_template": [
                "custom-visual-tool",
                "compare",
                "{reference}",
                "{actual}",
                "{diff}",
                "--limit",
                "{threshold}",
            ],
            "threshold": 0.03,
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
        timeout_sec=17,
    )

    evidence = evidence_runner.run_evidence_plan(_write_plan(tmp_path, workspace, [action]))
    result = evidence["actions"][0]["result"]

    assert seen[0] == [
        "custom-visual-tool",
        "compare",
        "reference.png",
        "actual.png",
        "diff.png",
        "--limit",
        "0.03",
    ]
    assert result["threshold"] == 0.03
    assert result["global"]["similarity"] == 0.875
    assert result["regions"][0]["normalized_weight"] == 1.0
    EvidenceOutput.model_validate(evidence)


def test_evidence_runner_records_errors_and_skips_failed_dependencies(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    actions = [
        _action("owner.missing", "read_file", {"path": "missing.txt"}),
        _action(
            "owner.dependent",
            "read_json",
            {"path": "missing.json"},
            depends_on=["owner.missing"],
        ),
    ]

    evidence = evidence_runner.run_evidence_plan(_write_plan(tmp_path, workspace, actions))

    assert evidence["actions"][0]["status"] == "error"
    assert evidence["actions"][1]["status"] == "skipped"
    assert evidence["actions"][1]["error"]["type"] == "DependencyFailed"
    assert evidence["metadata"]["required_error_count"] == 2
    EvidenceOutput.model_validate(evidence)


def test_evidence_runner_applies_profile_limits_and_archive_exclusions(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "large.txt").write_text("abcdef", encoding="utf-8")
    (workspace / "keep").mkdir()
    (workspace / "keep" / "item.txt").write_text("item", encoding="utf-8")
    (workspace / "private-cache").mkdir()
    (workspace / "private-cache" / "secret.txt").write_text("secret", encoding="utf-8")
    actions = [
        _action("owner.read", "read_file", {"path": "large.txt"}),
        _action("owner.glob", "glob", {"pattern": "**/*.txt"}),
        _action("owner.archive", "archive", {"path": ".", "output": "bundle.tar.gz"}),
    ]
    plan = _write_plan(
        tmp_path,
        workspace,
        actions,
        limits={
            "file_bytes": 3,
            "text_chars": 20,
            "list_items": 1,
            "list_item_chars": 10,
        },
        archive_excludes=["private-*"],
    )

    evidence = evidence_runner.run_evidence_plan(plan)

    assert evidence["actions"][0]["result"]["content"] == "abc"
    assert evidence["actions"][0]["result"]["truncated"] is True
    assert len(evidence["actions"][1]["result"]["matches"]) == 1
    with tarfile.open(tmp_path / "out" / "bundle.tar.gz", "r:gz") as archive:
        assert "private-cache/secret.txt" not in archive.getnames()


@pytest.mark.parametrize(
    "payload",
    [
        {"workspace": "/app", "actions": []},
        {
            "schema_version": "0",
            "runner_contract": "python-evidence-runner@2",
            "workspace": "/app",
            "output_dir": "/logs",
            "actions": [],
            "aliases": {},
            "limits": {
                "file_bytes": 1,
                "text_chars": 1,
                "list_items": 1,
                "list_item_chars": 1,
            },
            "archive_excludes": [],
        },
    ],
)
def test_evidence_runner_rejects_legacy_or_wrong_version_plans(
    tmp_path: Path, payload: dict
) -> None:
    path = tmp_path / "verification-plan.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="keys mismatch|unsupported evidence schema_version"):
        evidence_runner.run_evidence_plan(path, output_dir=tmp_path / "out")


def test_evidence_output_rejects_unknown_current_version_fields(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    evidence = evidence_runner.run_evidence_plan(_write_plan(tmp_path, workspace, []))
    evidence["legacy_scorecard"] = {}

    with pytest.raises(ValidationError):
        EvidenceOutput.model_validate(evidence)


def test_evidence_runner_main_uses_strict_plan(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    output_dir = tmp_path / "cli-out"
    workspace.mkdir()
    plan = _write_plan(tmp_path, workspace, [], output_dir=tmp_path / "ignored")

    assert evidence_runner.main([str(plan), "--output-dir", str(output_dir)]) == 0
    assert (output_dir / "evidence.json").exists()
