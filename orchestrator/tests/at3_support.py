"""A no-model agent that drives the generic package runtime in a real container (AT3).

``PackageDriveAgent`` is a Harbor agent whose ``run`` installs fixture packages with ``install/1``,
runs ``PackageCheckpoint`` (setup, canary, gate, stop, outputs) and plays scripted "agent turns" as
UID 1000, exactly where a model's tool calls would run. It records the outcome in
``at3.json`` beside the agent logs: ``invalid`` (a ``TrialInvalid``) or the primitives' record.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from raidar_contract.package.manifest import ParamScalar
from raidar_contract.package.registry import load_stage_package

from raidar.subject.primitive.canary import StageCheckRecord
from raidar.subject.primitive.install import install_package
from raidar.subject.primitive.site import TrialInvalid
from raidar.subject.workflow.package import PackageCheckpoint
from raidar.subject.workflow.package_member import PackageSpec
from raidar.subject.workflow.stage import Checkpoint

FIXTURES = Path(__file__).parent / "fixtures" / "stages" / "acme"
PACK = "at3-pack"
PRIMITIVES = {"stop/1": "p" * 64}
AGENT_UID = 1000
OUTCOME = "at3.json"
NOTE_WORDS = len((FIXTURES / "note-state/fixtures/done/state/note.md").read_text().split())
DONE = (json.dumps({"status": "done", "message": "", "tokens": {}, "carry": {}}) + "\n").encode()
"""The fault-probe tool's ``done`` stdout, byte for byte."""
NOTE_DONE = (
    json.dumps({"status": "done", "message": "", "tokens": {}, "carry": {"words": NOTE_WORDS}})
    + "\n"
).encode()


@dataclass(frozen=True)
class Scenario:
    """One package, its scripted agent turns and what is planted before the agent starts."""

    package: str
    params: Mapping[str, ParamScalar] = field(default_factory=dict)
    seed: Mapping[str, str] = field(default_factory=dict)
    """Files written (as the agent) before the agent starts: the starting workspace."""
    turns: tuple[Mapping[str, str], ...] = ()
    """Files the agent writes in each turn (later turns repeat the last when exhausted)."""
    max_turns: int = 6
    max_fix_rounds: int = 0
    stdout: bytes | None = None
    """The ``done`` stdout ``stage-check`` would record; ``None``: a package without a stop."""
    wrong_record: bool = False
    """Record a digest the container's fixture run cannot reproduce (a failing canary)."""
    no_gate: bool = False


class DriveOptions(AgentOptions):
    scenario: str = ""


def file_digests(package_root: Path, outputs: Mapping[str, str]) -> dict[str, str]:
    """Digest of each output's file in the package's ``done`` fixture state."""
    state = package_root / "fixtures" / "done" / "state"
    return {
        name: hashlib.sha256((state / Path(path).name).read_bytes()).hexdigest()
        for name, path in outputs.items()
    }


async def write_as_agent(environment: BaseEnvironment, files: Mapping[str, str]) -> None:
    for path, text in files.items():
        payload = base64.b64encode(text.encode()).decode()
        parent = shlex.quote(str(Path(path).parent))
        command = f"mkdir -p {parent} && echo {payload} | base64 -d > {shlex.quote(path)}"
        done = await environment.exec(command, user=AGENT_UID)
        if done.return_code != 0:
            raise RuntimeError(f"could not write {path}: {done.stderr}")


class PackageDriveAgent(BaseAgent):
    options_model = DriveOptions

    @staticmethod
    def name() -> str:
        return "raidar-at3-package-drive"

    def version(self) -> str | None:
        return "1"

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        assert isinstance(self.options, DriveOptions)
        outcome = await self._drive(environment, SCENARIOS[self.options.scenario])
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / OUTCOME).write_text(json.dumps(outcome, indent=2, sort_keys=True))
        context.metadata = {"scenario": self.options.scenario}

    async def _drive(self, environment: BaseEnvironment, scenario: Scenario) -> dict[str, Any]:
        package = load_stage_package(FIXTURES / scenario.package)
        manifest = package.manifest
        if scenario.no_gate:
            manifest = manifest.model_copy(update={"gate": ()})
            package = replace(package, manifest=manifest)
        tool = manifest.tool
        reference = f"{FIXTURES.name}/{package.name}"
        installed = await install_package(
            environment,
            self.logs_dir,
            reference=reference,
            package_digest=package.digest,
            files=package.files,
            commands={} if tool is None else dict(tool.commands),
        )
        expected = None
        if manifest.on_stop is not None and scenario.stdout is not None:
            sha = hashlib.sha256(scenario.stdout).hexdigest()
            outputs = file_digests(package.root, {k: v.path for k, v in manifest.outputs.items()})
            expected = StageCheckRecord(
                package=package.digest,
                pack=PACK,
                primitives=PRIMITIVES,
                stdout_sha256="0" * 64 if scenario.wrong_record else sha,
                outputs=outputs,
                disk_free=0,
                memory_available=None,
            )
        spec = PackageSpec(
            reference, package, installed, package.effective_params(scenario.params), expected
        )
        await write_as_agent(environment, scenario.seed)
        checkpoint = Checkpoint(environment, self.logs_dir, "the task")
        slot = package.name.upper().replace("-", "_")
        runtime = PackageCheckpoint(
            checkpoint,
            [spec],
            build=f"%%%{slot}%%%\n%%%TASK%%%",
            pack=PACK,
            primitives=PRIMITIVES,
            max_fix_rounds=scenario.max_fix_rounds,
        )
        outcome: dict[str, Any] = {"invalid": None, "resumes": []}
        try:
            await runtime.prepare()
            for number in range(scenario.max_turns):
                if scenario.turns:
                    await write_as_agent(
                        environment, scenario.turns[min(number, len(scenario.turns) - 1)]
                    )
                resume = await runtime.check(f"turn-{number}")
                if resume is None:
                    break
                outcome["resumes"].append(resume.kind)
        except TrialInvalid as error:
            outcome["invalid"] = {"category": error.kind, "message": str(error)}
        finally:
            try:
                await runtime.close(None)
            except TrialInvalid as error:
                outcome["invalid"] = outcome["invalid"] or {
                    "category": error.kind,
                    "message": str(error),
                }
        outcome["record"] = runtime.record()
        return outcome


PROBE_DATA = "/app/.raidar/fault-probe/data.json"
GATE_PASS = FIXTURES / "gate-check" / "fixtures" / "pass" / "state"
SCENARIOS: dict[str, Scenario] = {
    "healthy": Scenario(
        "fault-probe", stdout=DONE, turns=({PROBE_DATA: '{"ok": true}\n'},), max_turns=3
    ),
    "setup_exit_1": Scenario("fault-probe", params={"setup_exit": 1}, stdout=DONE),
    "junk_stop": Scenario("fault-probe", stdout=DONE, turns=({PROBE_DATA: "not json{"},)),
    "canary_fails": Scenario(
        "fault-probe", stdout=DONE, wrong_record=True, turns=({PROBE_DATA: '{"ok": true}\n'},)
    ),
    "rounds_exhausted": Scenario(
        "note-state",
        no_gate=True,
        stdout=NOTE_DONE,
        turns=({"/app/.raidar/note-state/scratch.txt": "not the note\n"},),
    ),
    "planted_exit_126": Scenario(
        "gate-check",
        seed={f"/app/{path.name}": path.read_text() for path in sorted(GATE_PASS.iterdir())},
        turns=({"/app/conftest.py": "import os\nos._exit(126)\n"},),
        max_turns=2,
    ),
}
SCENARIOS["planted_timeout"] = Scenario(
    "gate-check",
    seed=SCENARIOS["planted_exit_126"].seed,
    turns=({"/app/conftest.py": "import time\ntime.sleep(3600)\n"},),
    max_turns=2,
)
