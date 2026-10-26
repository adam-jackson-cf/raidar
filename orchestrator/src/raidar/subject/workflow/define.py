"""Define in the builder's primary session, using the pinned FXL deterministic tool."""

from __future__ import annotations

import hashlib
import json
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.subject.workflow.definition import DEFINE_TOKEN, DefineSettings
from raidar.subject.workflow.stage import Checkpoint, Stage

SOURCE_COMMIT = "ac5db0f28364911ef2298e428748120be8b0d56c"
PYTHON_PIN = "3.12.12"
RECORD_DIR = "workflow/define"
PAYLOAD = Path(__file__).with_name("define_tool")
REPAIR = (
    "The definition could not be used: {error}. Run `define check`, fix what it "
    "lists, then run `define done ready` (or `define done ask` when you are "
    "asking questions). Then finish the build against `/app/.raidar/feature-definition.md`."
)


@dataclass(frozen=True)
class DefinePaths:
    workspace: str = "/app"
    home: str = "/opt/raidar-define"
    command: str = "/usr/local/bin/define"

    @property
    def state(self) -> str:
        return f"{self.workspace}/.raidar/define"

    @property
    def python(self) -> str:
        return f"{self.home}/tools/bin/python"

    def invocation(self, module: str, expression: str) -> list[str]:
        script = (
            f"import sys; sys.path.insert(0, {self.home!r}); "
            f"from define_tool import {module}; {expression}"
        )
        return [self.python, "-I", "-B", "-c", script]

    @property
    def inspector(self) -> list[str]:
        return self.invocation(
            "status",
            "raise SystemExit(status.main(['--write', '--context', "
            f"{self.home + '/context.json'!r}], "
            f"{{'RAIDAR_DEFINE_STATE': {self.state!r}}}))",
        )


PATHS = DefinePaths()


def tools_install_command(paths: DefinePaths) -> str:
    """An independent exact-pinned interpreter, never the project's interpreter."""
    home = shlex.quote(paths.home)
    return "\n".join(
        [
            "set -eu",
            f"[ -x {shlex.quote(paths.python)} ] && exit 0",
            f"export UV_PYTHON_INSTALL_DIR={home}/python",
            f"uv python install {PYTHON_PIN}",
            f"uv venv -q --python {PYTHON_PIN} {home}/tools",
        ]
    )


class Define(Stage):
    name = "define"
    token = DEFINE_TOKEN

    def __init__(self, settings: DefineSettings) -> None:
        self.settings = settings
        self.paths = PATHS
        self.instructions = []
        self.context: dict[str, Any] = {}
        self.units = ""
        self.checks: list[dict[str, Any]] = []
        self.repairs: list[dict[str, Any]] = []
        self.questions: list[dict[str, Any]] = []
        self.ending: str | None = None
        self.definition_sha256: str | None = None
        self.install_seconds: float | None = None
        self.errors: list[str] = []

    async def prepare(self, checkpoint: Checkpoint) -> None:
        records = checkpoint.logs_dir / RECORD_DIR
        records.mkdir(parents=True, exist_ok=True)
        self.context = {
            "issue": checkpoint.task,
            "said": [],
            "prior": "",
            "open": [],
            "workdir": self.paths.workspace,
            "unit_rules": self.settings.unit_rules,
            "probes_owed": None,
        }
        started = time.monotonic()
        await self._exec(checkpoint, tools_install_command(self.paths))
        self.install_seconds = round(time.monotonic() - started, 3)
        await self._exec(checkpoint, f"mkdir -p {shlex.quote(self.paths.home + '/define_tool')}")
        for source in sorted(PAYLOAD.glob("*.py")):
            await checkpoint.environment.upload_file(
                source, f"{self.paths.home}/define_tool/{source.name}"
            )
        launcher = records / "define"
        argv = self.paths.invocation(
            "tool",
            f"raise SystemExit(tool.main(environ={{'RAIDAR_DEFINE_STATE': {self.paths.state!r}}}))",
        )
        launcher.write_text("#!/bin/sh\nexec " + shlex.join(argv) + ' "$@"\n', encoding="utf-8")
        await checkpoint.environment.upload_file(launcher, self.paths.command)
        await self._exec(
            checkpoint,
            f'if [ "$(id -u)" = 0 ]; then chown -R root:root '
            f"{shlex.quote(self.paths.home)} {shlex.quote(self.paths.command)}; fi\n"
            f"chmod -R go-w {shlex.quote(self.paths.home)} && "
            f"chmod 755 {shlex.quote(self.paths.command)} && "
            f"rm -rf {shlex.quote(self.paths.state)} && "
            f"mkdir -p {shlex.quote(self.paths.state)} && "
            f"rm -f {shlex.quote(self.paths.workspace + '/.raidar/feature-definition.md')}",
        )
        await self._inputs(checkpoint)
        owner = (await self._exec(checkpoint, "id -un", root=False)).strip()
        await self._exec(
            checkpoint,
            f"chown -R {shlex.quote(owner + ':')} {shlex.quote(self.paths.workspace + '/.raidar')}",
        )

    async def _inputs(self, checkpoint: Checkpoint) -> None:
        from raidar.subject.workflow.define_tool import compact  # noqa: PLC0415
        from raidar.subject.workflow.define_tool.unitrules import VERSIONS  # noqa: PLC0415

        units = compact.units(self.context["issue"], self.context["said"], VERSIONS[2])
        self.units = units.text()
        records = checkpoint.logs_dir / RECORD_DIR
        inputs = records / "inputs.json"
        inputs.write_text(json.dumps(self.context, indent=2) + "\n", encoding="utf-8")
        (records / "units.md").write_text(self.units + "\n", encoding="utf-8")
        (records / "units.json").write_text(
            json.dumps(
                [
                    {"id": u.id, "text": u.text, "required": u.required, "citable": u.citable}
                    for u in units.items
                ],
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        await checkpoint.environment.upload_file(inputs, f"{self.paths.state}/context.json")
        await checkpoint.environment.upload_file(inputs, f"{self.paths.home}/context.json")
        immutable = shlex.quote(self.paths.home + "/context.json")
        await self._exec(
            checkpoint,
            f'if [ "$(id -u)" = 0 ]; then chown root:root {immutable}; fi\nchmod 644 {immutable}',
        )

    def fragment(self) -> str:
        return self.settings.prompt.replace("%%%UNITS%%%", self.units)

    async def check(self, checkpoint: Checkpoint, after: str) -> str | None:
        if self.ending == "definition_unusable":
            return None
        result = await checkpoint.environment.exec(shlex.join(self.paths.inspector), user="root")
        try:
            inspected: dict[str, Any] = json.loads(result.stdout or "")
        except ValueError:
            inspected = {
                "state": "error",
                "error": f"exit {result.return_code}: {result.stderr or ''}",
            }
        state = inspected["state"]
        findings = [*inspected.get("blocking", []), *inspected.get("schema_violations", [])]
        if state not in ("ready", "asking") and not findings:
            findings = [inspected.get("error", f"definition is {state}: run `define check`")]
        record = {
            "after": after,
            "sealed": inspected.get("draft", {}).get("sealed", ""),
            "status": state,
            "errors": findings,
            "blocking": findings,
        }
        self.checks.append(record)
        await self._keep(checkpoint)
        if state == "ready":
            text = str(inspected["checked"]["file"]["rendered"])
            path = checkpoint.logs_dir / RECORD_DIR / "feature-definition.md"
            path.write_text(text, encoding="utf-8")
            self.definition_sha256 = hashlib.sha256(text.encode()).hexdigest()
            self.ending = "definition_ready"
            return None
        if state == "asking":
            return await self._accept(checkpoint, after, inspected["reply"]["questions"])
        if not self.repairs:
            self.repairs.append({"after": after, "findings": findings})
            return REPAIR.format(error="; ".join(findings))
        self.ending = "definition_unusable"
        self.definition_sha256 = None
        return None

    async def _accept(
        self, checkpoint: Checkpoint, after: str, asked: list[dict[str, Any]]
    ) -> str | None:
        number = len(self.questions) + 1
        questions = [{"id": f"Q{number}.{i}", **q} for i, q in enumerate(asked, 1)]
        accepted = number <= self.settings.max_refinement_rounds
        answers = [{**q, "outcome": "accepted", "quote": ""} for q in questions] if accepted else []
        self.questions.append(
            {
                "round": number,
                "after": after,
                "questions": questions,
                "answers": answers,
            }
        )
        if not accepted:
            self.ending = "definition_unusable"
            return None
        records = checkpoint.logs_dir / RECORD_DIR
        archive = records / f"round-{number}"
        archive.mkdir()
        for name in ("inputs.json", "state.json", "units.md", "units.json"):
            source = records / name
            if source.is_file():
                (archive / name).write_bytes(source.read_bytes())
        self.context["said"] += [
            f"- {q['id']} ({q['feature']}) {q['question']} → accepted: {q['proposal']}"
            for q in questions
        ]
        script = (
            "import json; from pathlib import Path; "
            f"tool.next_round(Path({self.paths.state!r}), tool.Context.read("
            f"json.loads({json.dumps(self.context)!r})))"
        )
        await self._exec(checkpoint, shlex.join(self.paths.invocation("tool", script)))
        await self._inputs(checkpoint)
        return (
            "Every proposal is accepted (benchmark policy). Build a fresh definition with these "
            "answers; seal it with `define done ready`, then finish building against the "
            "definition.\n\n" + self.units
        )

    async def _keep(self, checkpoint: Checkpoint) -> None:
        for name in ("state.json", "define-calls.jsonl", "define-events.jsonl"):
            result = await checkpoint.environment.exec(
                f"cat {shlex.quote(self.paths.state + '/' + name)}", user="root"
            )
            if result.return_code == 0:
                (checkpoint.logs_dir / RECORD_DIR / name).write_text(
                    result.stdout or "", encoding="utf-8"
                )

    async def close(self, checkpoint: Checkpoint, cut: str | None) -> None:
        if cut is not None:
            self.ending = cut
        try:
            await self._keep(checkpoint)
        except Exception as exc:  # noqa: BLE001 - retain stage evidence even on interruption
            self.errors.append(f"{type(exc).__name__}: {exc}")

    async def _exec(self, checkpoint: Checkpoint, command: str, *, root: bool = True) -> str:
        result = await checkpoint.environment.exec(
            "umask 022\n" + command, user="root" if root else None
        )
        if result.return_code != 0:
            detail = (result.stderr or result.stdout or "")[-1000:]
            raise RuntimeError(f"define setup failed (exit {result.return_code}): {detail}")
        return result.stdout or ""

    def record(self) -> dict[str, Any]:
        return {
            "source_commit": SOURCE_COMMIT,
            "python": PYTHON_PIN,
            "ending": self.ending,
            "unit_rules": 2,
            "inputs": f"{RECORD_DIR}/inputs.json",
            "state": f"{RECORD_DIR}/state.json",
            "calls": f"{RECORD_DIR}/define-calls.jsonl",
            "events": f"{RECORD_DIR}/define-events.jsonl",
            "units": f"{RECORD_DIR}/units.md",
            "checks": self.checks,
            "repairs": self.repairs,
            "questions": self.questions,
            "max_refinement_rounds": self.settings.max_refinement_rounds,
            "definition": f"{RECORD_DIR}/feature-definition.md" if self.definition_sha256 else None,
            "definition_sha256": self.definition_sha256,
            "install_seconds": self.install_seconds,
            "errors": self.errors,
        }
