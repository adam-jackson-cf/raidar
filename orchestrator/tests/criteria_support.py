"""Fixtures for the ``acceptance-reply/1`` and ``jev-judge/1`` behaviour tests.

The container is a shell that runs Raidar's own ``python3 -c`` scripts in this process, the
criteria are a ``criteria/1`` document and the gate is the record ``gate/1`` seals: no package,
container image, subprocess or model is involved.
"""

from __future__ import annotations

import hashlib
import io
import json
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from raidar_contract.criteria import validate_criteria

from raidar.subject.workflow.acceptance import Acceptance

TESTS = """\
from app import parse


def test_parse_object():
    assert parse('{"a": 1}') == {"a": 1}


def test_parse_rejects():
    assert parse("[]") is None
"""
OBJECT = "tests/test_app.py::test_parse_object"
REJECTS = "tests/test_app.py::test_parse_rejects"
FILE = "tests/test_app.py"
CRITERIA = {
    "contract": "criteria/1",
    "features": [
        {
            "id": "F1",
            "name": "Parsing",
            "outcome": "parse",
            "criteria": [
                {
                    "id": "F1.AC1",
                    "criterion": "parse rejects a JSON value that is not an object",
                    "verification": "tests prove it",
                    "sources": ["Reject any document that is not a JSON object"],
                    "kind": "new",
                    "aspects": [{"situation": "a JSON array", "outcome": "it is rejected"}],
                    "examples": [],
                },
                {
                    "id": "F1.AC2",
                    "criterion": "earlier behaviour still works",
                    "verification": "tests prove it",
                    "sources": ["Everything else keeps working"],
                    "kind": "regression",
                    "aspects": [],
                    "examples": [],
                },
            ],
        }
    ],
}


class ScriptShell:
    """The container's shell, for the ``python3 -c <script> <args>`` commands the host sends.

    The script is Raidar's own, run here against the real files of the workspace it names
    (stdout captured, ``sys.exit`` as the exit code); a command of any other shape is refused.
    """

    def __init__(self) -> None:
        self.commands: list[str] = []

    async def exec(self, command: str, **_: Any) -> Any:
        self.commands.append(command)
        words = shlex.split(command)
        if words[0] != "python3" or "-c" not in words:
            return SimpleNamespace(return_code=127, stdout="", stderr=f"{words[0]}: not found")
        at = words.index("-c")
        script, argv = words[at + 1], words[at + 2 :]
        out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="")
        saved = sys.argv, sys.stdout
        sys.argv, sys.stdout = ["-c", *argv], out
        code = 0
        try:
            exec(compile(script, "<script>", "exec"), {"__name__": "__main__"})
        except SystemExit as stop:
            code = int(stop.code or 0)
        finally:
            sys.argv, sys.stdout = saved
        out.flush()
        stdout = out.buffer.getvalue().decode("utf-8")  # type: ignore[attr-defined]
        return SimpleNamespace(return_code=code, stdout=stdout, stderr="")


def acceptance(mapping: dict[str, list[str]] | None = None) -> Acceptance:
    """The criteria document, loaded, with ``mapping`` (criterion id to tests) merged."""
    found = Acceptance()
    found.load(validate_criteria(CRITERIA))
    found.ac_tests.update(mapping or {})
    return found


def reply(*tests: str, account: str = "built", **extra: Any) -> str:
    """A builder's reply: its account, no ADR and each given test mapped to F1.AC1."""
    mapped = [{"criterion": "F1.AC1", "tests": list(tests)}] if tests else []
    return "Done.\n" + json.dumps({"account": account, "adrs": [], "ac_tests": mapped, **extra})


def file_digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def gate_record(
    run: int,
    files: dict[str, str],
    *,
    verdict: str = "passed",
    seconds: float = 1.0,
    outcomes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """A sealed ``gate/1`` record: the tree's files and the pytest check's per-test outcomes."""
    collected = [OBJECT, REJECTS]
    results = outcomes or dict.fromkeys(collected, "passed")
    return {
        "who": "seal",
        "run": run,
        "verdict": verdict,
        "tree": f"tree-{run}",
        "files": {name: file_digest(text) for name, text in files.items()},
        "output": f"gate output {run}",
        "seconds": seconds,
        "checks": [
            {
                "name": "verify.tests",
                "category": "pytest",
                "status": verdict,
                "tests": {"collected": collected, "outcomes": results},
            }
        ],
    }


def write_workspace(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
