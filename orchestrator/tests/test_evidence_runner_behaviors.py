"""Behavior tests for deterministic source-surface evidence snapshots."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from raidar.assets.evidence_runner import run_evidence_plan
from raidar.scorers.evidence_contract import EvidenceOutput


def _plan(tmp_path: Path, workspace: Path, actions: list[dict]) -> Path:
    plan_path = tmp_path / "verification-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "runner_contract": "python-evidence-runner@2",
                "workspace": str(workspace),
                "output_dir": str(tmp_path / "evidence"),
                "actions": actions,
                "aliases": {action["id"]: action["id"] for action in actions},
                "limits": {
                    "file_bytes": 1024,
                    "text_chars": 1024,
                    "list_items": 10,
                    "list_item_chars": 200,
                },
                "archive_excludes": [],
            }
        ),
        encoding="utf-8",
    )
    return plan_path


def _action(action_id: str, kind: str, paths: list[str]) -> dict:
    return {
        "id": action_id,
        "kind": kind,
        "timeout_sec": None,
        "required": True,
        "depends_on": [],
        "config": {"paths": paths},
    }


def test_source_snapshots_are_typed_and_stable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "module.py").write_text(
        """# comment
PUBLIC_VALUE = 1

def public_api(value: int) -> str:
    return str(value)

def _private() -> None:
    pass

class PublicType:
    pass
""",
        encoding="utf-8",
    )
    plan_path = _plan(
        tmp_path,
        workspace,
        [
            _action("structure", "structural_snapshot", ["module.py"]),
            _action("contract", "public_contract_snapshot", ["module.py"]),
        ],
    )

    first = EvidenceOutput.model_validate(run_evidence_plan(plan_path))
    second = EvidenceOutput.model_validate(run_evidence_plan(plan_path))

    assert first.actions[0].result == second.actions[0].result
    assert first.actions[1].result == second.actions[1].result
    assert first.actions[0].result.files[0].line_count == 11
    assert first.actions[0].result.files[0].code_line_count == 7
    assert first.actions[1].result.files[0].declarations == [
        "PUBLIC_VALUE",
        "def public_api(value: int) -> str",
        "class PublicType",
    ]


@pytest.mark.parametrize(
    ("paths", "match"),
    [
        ([], "non-empty text list"),
        (["../outside.py"], "workspace-relative"),
        (["missing.py"], "unavailable"),
    ],
)
def test_snapshot_runner_rejects_invalid_or_missing_selected_paths(
    tmp_path: Path, paths: list[str], match: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan_path = _plan(tmp_path, workspace, [_action("structure", "structural_snapshot", paths)])

    if paths == ["missing.py"]:
        output = run_evidence_plan(plan_path)
        assert output["actions"][0]["status"] == "error"
        assert match in output["actions"][0]["error"]["message"]
    else:
        with pytest.raises(ValueError, match=match):
            run_evidence_plan(plan_path)
