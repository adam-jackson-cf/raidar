"""``acceptance-reply/1``: the builder's structured reply, read host-side after every turn.

Raidar owns this primitive (a workflow opts in with ``build.reply: acceptance-reply/1``); no
package runs code for it. The runner appends a compact skeleton of ``{account, ac_tests, adrs}`` to
the prompt of every first-build, gate-fix, rework and repair turn (``decorate``) and, after the
turn, ``check`` reads the native adapter's normalized final text from that turn's
``last-message.txt``:

- one parser for every agent (``raidar.subject.workflow.reply``): the last top-level JSON object,
  undeclared properties dropped and recorded, every violation listed;
- a reply that cannot be read, or that breaks the schema, is a *syntax* fault; a well-formed reply
  whose ``ac_tests`` leave a criterion unmapped (a regression one may be), name an unknown
  criterion or name a test in a file the working tree lacks is a *semantic* fault (checked once the
  criteria are known); each kind is repaired once per builder turn, in the same session, so a turn
  runs up to three times;
- the builder's ``ac_tests`` are merged across turns into the checkpoint's ``Acceptance``; a reply
  still unusable after its repair is recorded and the checkpoint goes on (the gate and the judge
  decide its result).

``record`` is the primitive's evidence at ``workflow.json["acceptance-reply/1"]``::

    {"replies": [{"after", "kind", "ok", "fault", "error", "dropped", "reported", "sha256",
                  "record"}],
     "repairs": {"syntax": n, "semantic": n},
     "ac_tests": {criterion id: [test ids]},
     "adrs": [...],
     "sources": "workflow/sources/final" | None}

``record`` names ``<after>/reply.json`` (the full reply value) in the agent logs; ``sha256``
is the digest of ``<after>/last-message.txt``.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path
from typing import Any

from raidar.subject.primitive import calls
from raidar.subject.workflow.acceptance import Acceptance, rows
from raidar.subject.workflow.reply import BUILD_SCHEMA, REPAIR_PROMPT, read_reply, shape_suffix
from raidar.subject.workflow.stage import FIRST_BUILD, GATE_FIX, REPAIR, REWORK, Checkpoint, Resume

PRIMITIVE = "acceptance-reply/1"
SYNTAX = "syntax"
SEMANTIC = "semantic"
REPLY_KINDS = (FIRST_BUILD, GATE_FIX, REWORK, REPAIR)
"""The turns whose reply is the structured one."""
LAST_MESSAGE_FILE = "last-message.txt"
REPLY_RECORD = "reply.json"
PRESENT = """\
import os, sys
root = os.path.realpath(sys.argv[1])
for path in sys.argv[2:]:
    found = os.path.realpath(os.path.join(root, path))
    if found.startswith(root + os.sep) and os.path.isfile(found):
        print(path)
"""
"""The mapped files that are files inside the working tree (a path outside it is absent)."""


class AcceptanceReply:
    """The structured replies of one checkpoint and the mapping they build."""

    def __init__(self, acceptance: Acceptance, workspace: str = "/app") -> None:
        self.acceptance = acceptance
        self.workspace = workspace
        self.replies: list[dict[str, Any]] = []
        self.adrs: dict[str, dict[str, Any]] = {}
        self.repairs = {SYNTAX: 0, SEMANTIC: 0}
        self._repaired: set[str] = set()

    def decorate(self, kind: str, prompt: str) -> str:
        """The turn's prompt with the reply's shape appended (a turn of another kind: as is)."""
        if kind in REPLY_KINDS:
            return prompt + shape_suffix(BUILD_SCHEMA)
        return prompt

    async def check(self, checkpoint: Checkpoint, after: str) -> Resume | None:
        """Read the reply of the turn logged at ``after``; a repair turn when one is owed."""
        kind = checkpoint.turns[-1].kind if checkpoint.turns else FIRST_BUILD
        if kind not in REPLY_KINDS:
            return None
        if kind != REPAIR:
            self._repaired = set()
        text = self._reply_text(checkpoint.logs_dir / after / LAST_MESSAGE_FILE)
        value, error, dropped = read_reply(text, BUILD_SCHEMA)
        fault = SYNTAX if value is None else None
        reported = rows(value or {}, "ac_tests")
        if value is not None:
            self._keep_adrs(rows(value, "adrs"))
            self.acceptance.merge(reported)
            error = await self._mapping_error(checkpoint, reported)
            fault = SEMANTIC if error else None
        record = {
            "after": after,
            "kind": kind,
            "ok": fault is None,
            "fault": fault,
            "error": error,
            "dropped": dropped,
            "value": value,
        }
        self._save(checkpoint, record, reported, text)
        return self._repair(fault, error)

    @staticmethod
    def _reply_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _save(
        self,
        checkpoint: Checkpoint,
        record: dict[str, Any],
        reported: list[dict[str, Any]],
        text: str,
    ) -> None:
        """Write the turn's ``reply.json`` and keep its summary."""
        folder = checkpoint.logs_dir / record["after"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / REPLY_RECORD).write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        self.replies.append(
            {
                **record,
                "value": None,
                "reported": [row.get("criterion") for row in reported],
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "record": f"{record['after']}/{REPLY_RECORD}",
            }
        )

    def _repair(self, fault: str | None, error: str | None) -> Resume | None:
        """The one repair turn per kind of fault, None when none is owed."""
        if fault is None or fault in self._repaired:
            return None
        self._repaired.add(fault)
        self.repairs[fault] += 1
        return Resume(REPAIR_PROMPT.format(error=error), REPAIR)

    def _keep_adrs(self, reported: list[dict[str, Any]]) -> None:
        for item in reported:
            ident = item.get("id")
            if isinstance(ident, str):
                self.adrs[ident] = item

    async def _mapping_error(
        self, checkpoint: Checkpoint, reported: list[dict[str, Any]]
    ) -> str | None:
        """What is wrong with the merged mapping, or None; none without criteria to map."""
        if not self.acceptance.features:
            return None
        errors = self.acceptance.errors(reported, await self._present(checkpoint))
        return "; ".join(errors) if errors else None

    async def _present(self, checkpoint: Checkpoint) -> set[str]:
        """The mapped files that exist in the working tree."""
        files = [name for name in self.acceptance.mapped_files if name and "\0" not in name]
        if not files:
            return set()
        command = f"python3 -c {shlex.quote(PRESENT)} {shlex.quote(self.workspace)} " + " ".join(
            shlex.quote(name) for name in files
        )
        result = await calls.trusted_exec(checkpoint.environment, command, user="root")
        return set((result.stdout or "").split("\n")) - {""}

    def record(self) -> dict[str, Any]:
        """The primitive's evidence (module docstring)."""
        return {
            "replies": self.replies,
            "repairs": self.repairs,
            "ac_tests": self.acceptance.ac_tests,
            "adrs": list(self.adrs.values()),
            "sources": self.acceptance.sources,
        }
