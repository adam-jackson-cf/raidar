"""The generic schema-2 runner path: locked loading, stage lifecycle and the invalid host record.

A scripted native agent and the lifecycle container double stand in for the model and the sandbox;
the lock, ``PackageStage``, ``PackageCheckpoint`` and the primitives' lifecycle run for real. The
gate's in-container run is a scripted double whose sealed outcomes drive the fix budget.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from harbor.models.agent.context import AgentContext
from raidar_contract.canonical import digest_json
from raidar_contract.package.manifest import StagePackage
from raidar_contract.package.registry import load_stage_package, stage_folder
from raidar_contract.releases import Release
from raidar_contract.workflow import PackageWorkflowDefinition

from raidar.subject.primitive import calls
from raidar.subject.primitive.calls import TransportError, TreeManifest
from raidar.subject.primitive.canary import StageCheckRecord
from raidar.subject.primitive.gate import Attribution, GateInvalid, GateOutcome
from raidar.subject.primitive.install import InstalledPackage, InstallError
from raidar.subject.primitive.site import TrialInvalid
from raidar.subject.workflow import package as package_module
from raidar.subject.workflow import package_trial
from raidar.subject.workflow.identity import package_primitives
from raidar.subject.workflow.package_stage import PackageStage
from raidar.subject.workflow.runner import RECORD_FILE, WorkflowRunner
from raidar.subject.workflow.stage import GATE_FIX, REPAIR, REWORK, Checkpoint, Spent
from raidar.subject.workflow.turns import WORKFLOW_DIR
from tests.lifecycle_support import (
    NOTE_STATE,
    PACK,
    PRIMITIVES,
    FakeContainer,
    ScriptedRunner,
    ToolOutcome,
    install_container,
    installed,
    stage_check_record,
)
from tests.native_support import CodexNative
from tests.workflow_support import LocalEnvironment

pytestmark = pytest.mark.slow

BUILD = "Build:\n%%%NOTE_STATE%%%\nTask: %%%TASK%%%"
TASK = "Write the note."
TREE = "t" * 64
ARCH = "arm64"
RELEASE = Release.parse_stage("acme/note-state@1")
PLACEMENT = ".agents/skills"
WATCH = "/app/.raidar/note-state"


def definition_json(params: dict[str, Any] | None = None, fix_rounds: int = 0) -> dict[str, Any]:
    use: dict[str, Any] = {"use": "acme/note-state@1"}
    if params:
        use["params"] = params
    return {
        "schema": 2,
        "name": "note-flow",
        "build": {"prompt": BUILD},
        "stages": [use],
        "gate": {"max_fix_rounds": fix_rounds},
    }


class Env(FakeContainer):
    """The lifecycle container as a Harbor environment whose logs are mounted.

    Commands the runner runs itself (the session-store ctime read) execute on the host shell, as
    the existing workflow environment double does.
    """

    capabilities = SimpleNamespace(mounted=True)

    async def exec(self, command: str, **options: Any) -> Any:
        return await LocalEnvironment(dict(os.environ)).exec(command, **options)


class ScriptedGate:
    """The gate's in-container run: sealed outcomes are scripted, retained prefixes recorded."""

    outcomes: ClassVar[list[GateOutcome | Exception]] = []
    retained: ClassVar[list[int]] = []
    seconds: ClassVar[float] = 0.0

    def __init__(self, environment: Any, logs_dir: Path, packages: Any, settings: Any) -> None:
        return None

    async def prepare(self, session_paths: Any) -> None:
        return None

    async def seal(self, after: str) -> GateOutcome:
        if not type(self).outcomes:
            return passed()
        outcome = type(self).outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def retain_prefix(self, round_: int) -> None:
        type(self).retained.append(round_)

    def record(self) -> dict[str, Any]:
        return {"gates": [{"seconds": type(self).seconds}]}

    async def close(self) -> None:
        return None


def passed() -> GateOutcome:
    return GateOutcome(Attribution.PASSED, {"checks": []}, "")


def refused(reason: str = "lint failed") -> GateOutcome:
    record = {
        "checks": [
            {"name": "note-ready", "status": "failed"},
            {"name": "other", "status": "passed"},
        ]
    }
    return GateOutcome(Attribution.REFUSED, record, reason)


class Installs:
    """``install/1`` for the container: the first call installs, later calls measure that."""

    def __init__(self, lab: Lab) -> None:
        self.lab = lab
        self.count = 0
        self.current: InstalledPackage | None = None
        self.failure: str | None = None

    async def reuse(self, *args: Any, **kwargs: Any) -> InstalledPackage | None:
        return self.current

    async def install(self, *args: Any, **kwargs: Any) -> InstalledPackage:
        if self.failure:
            raise InstallError(self.failure)
        self.count += 1
        self.current = replace(
            installed(self.lab.package, self.lab.container),
            digest=self.lab.trees["digest"],
            record={"schema": "install/1", "arch": ARCH},
        )
        return self.current


@dataclass
class Lab:
    """A repository tree holding the note-state package, a container and the trial's lock."""

    tmp_path: Path
    repo: Path
    container: Env
    package: StagePackage
    trees: dict[str, str]
    stored_check: bool = True
    primitives: dict[str, str] | None = None
    """The primitives the recorded stage-check carries: ``None`` is the package's own code."""
    installs: Installs = field(init=False)

    def __post_init__(self) -> None:
        self.installs = Installs(self)

    def expected_record(self) -> StageCheckRecord:
        """The stage-check record of the package: its real adapter-independent primitive hashes
        unless ``primitives`` says the record was taken against other code."""
        primitives = self.primitives or package_primitives([self.package])
        return stage_check_record(self.package, self.container, primitives)

    def lock(self, drop: Sequence[str] = (), **overrides: Any) -> dict[str, dict[str, Any]]:
        entry: dict[str, Any] = {
            "sha256": self.package.digest,
            "installed": {ARCH: TREE},
            "check": digest_json(self.expected_record().as_json()),
        }
        entry.update(overrides)
        for key in drop:
            entry.pop(key)
        return {"acme/note-state": entry}

    def without_instructions(self) -> None:
        (stage_folder(RELEASE, self.repo) / "instructions.md").unlink()
        self.package = load_stage_package(stage_folder(RELEASE, self.repo))

    def stage(
        self,
        locked: dict[str, dict[str, Any]] | None = None,
        *,
        params: dict[str, Any] | None = None,
        fix_rounds: int = 0,
        deadline: float | None = None,
    ) -> tuple[PackageStage, Checkpoint]:
        logs = self.tmp_path / "stage-logs"
        logs.mkdir(exist_ok=True)
        stage = PackageStage(
            PackageWorkflowDefinition.model_validate(definition_json(params, fix_rounds)),
            locked=self.lock() if locked is None else locked,
            pack=PACK,
            skills_placement=PLACEMENT,
            repo_root=self.repo,
        )
        return stage, Checkpoint(self.container, logs, TASK, deadline=deadline)


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Lab:
    repo = tmp_path / "repo"
    folder = stage_folder(RELEASE, repo)
    shutil.copytree(NOTE_STATE, folder)
    container = Env(tmp_path / "container")
    container.root.mkdir()
    install_container(monkeypatch, container)
    found = Lab(tmp_path, repo, container, load_stage_package(folder), {"digest": TREE})

    def check_record(digest: str, root: Path | None = None) -> dict[str, Any] | None:
        if not found.stored_check or digest != found.package.digest:
            return None
        return found.expected_record().as_json()  # type: ignore[return-value]

    async def upload_file(environment: Any, source: Path, target: str) -> None:
        destination = environment.host(target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

    async def tree_manifest(environment: Any, path: str, **_: Any) -> TreeManifest:
        return TreeManifest(path, True, (), found.trees["digest"], 0, 0, None)

    monkeypatch.setattr(package_trial, "reuse_installed", found.installs.reuse)
    monkeypatch.setattr(package_trial, "install_package", found.installs.install)
    monkeypatch.setattr(package_trial, "check_record", check_record)
    monkeypatch.setattr(
        package_trial, "stage_folder", lambda release, repo_root=None: stage_folder(release, repo)
    )
    monkeypatch.setattr(calls, "upload_file", upload_file)
    monkeypatch.setattr(calls, "tree_manifest", tree_manifest)
    monkeypatch.setattr(
        package_module, "CallRunner", lambda environment, logs: ScriptedRunner(environment, logs)
    )
    monkeypatch.setattr(package_module, "GateRun", ScriptedGate)
    ScriptedGate.outcomes = []
    ScriptedGate.retained = []
    ScriptedGate.seconds = 0.0
    return found


async def test_an_unusable_judge_invalidates_before_any_package_code_runs(lab: Lab) -> None:
    body = definition_json()
    body["build"]["reply"] = "acceptance-reply/1"
    body["judge"] = {
        "criteria": "note-state.note",
        "model": "someone/else-1",
        "closed_at": 0.8,
        "open_at": 0.3,
        "max_passes": 1,
        "rework": "Fix:\n%%%PUSHBACK%%%\n%%%GATE%%%",
    }
    stage = PackageStage(
        PackageWorkflowDefinition.model_validate(body),
        locked=lab.lock(),
        pack=PACK,
        skills_placement=PLACEMENT,
        repo_root=lab.repo,
    )
    checkpoint = Checkpoint(lab.container, lab.tmp_path / "judge-logs", TASK)

    with pytest.raises(TrialInvalid) as invalid:
        await stage.prepare(checkpoint)

    assert invalid.value.kind == "instrument"
    assert lab.installs.count == 0
    assert lab.container.calls == []
    assert stage.package_record()["jev-judge/1"]["passes"] == 0


# the lock holds the package to its bytes, installed tree and check record ------------------------


async def test_a_package_the_lock_does_not_list_is_refused_before_anything_installs(
    lab: Lab,
) -> None:
    stage, checkpoint = lab.stage({})

    with pytest.raises(TrialInvalid, match="not in the lock") as refused_:
        await stage.prepare(checkpoint)

    assert refused_.value.kind == "integrity"
    assert lab.installs.count == 0


async def test_package_bytes_other_than_the_locked_digest_are_refused(lab: Lab) -> None:
    stage, checkpoint = lab.stage(lab.lock(sha256="0" * 64))

    with pytest.raises(TrialInvalid, match="on disk, the lock holds 000000000000") as refused_:
        await stage.prepare(checkpoint)

    assert refused_.value.kind == "integrity"
    assert lab.installs.count == 0


async def test_a_package_missing_from_the_tree_is_an_integrity_failure_not_an_os_error(
    lab: Lab,
) -> None:
    shutil.rmtree(stage_folder(RELEASE, lab.repo))
    stage, checkpoint = lab.stage()

    with pytest.raises(TrialInvalid) as refused_:
        await stage.prepare(checkpoint)

    assert refused_.value.kind == "integrity"
    assert str(RELEASE) in refused_.value.detail


async def test_a_changed_installed_tree_is_refused_and_never_reinstalled_over(lab: Lab) -> None:
    stage, checkpoint = lab.stage(lab.lock(installed={ARCH: "u" * 64}))

    with pytest.raises(TrialInvalid, match="differs from the locked uuuuuuuuuuuu") as first:
        await stage.prepare(checkpoint)
    with pytest.raises(TrialInvalid, match="differs from the locked"):
        await lab.stage(lab.lock(installed={ARCH: "u" * 64}))[0].prepare(checkpoint)

    assert first.value.kind == "integrity"
    assert first.value.record == {"schema": "install/1", "arch": ARCH}
    assert lab.installs.count == 1  # the second checkpoint measured the first install


async def test_a_lock_without_this_architecture_is_refused(lab: Lab) -> None:
    stage, checkpoint = lab.stage(lab.lock(installed={"amd64": TREE}))

    with pytest.raises(TrialInvalid, match=r"none for arm64"):
        await stage.prepare(checkpoint)


async def test_an_install_failure_is_an_integrity_failure_naming_the_cause(lab: Lab) -> None:
    lab.installs.failure = "wheel hash mismatch"
    stage, checkpoint = lab.stage()

    with pytest.raises(TrialInvalid, match="wheel hash mismatch") as refused_:
        await stage.prepare(checkpoint)

    assert refused_.value.kind == "integrity"
    assert lab.container.calls == []  # nothing ran


@pytest.mark.parametrize(
    ("overrides", "stored", "message"),
    [
        ({"check": "f" * 64}, True, "differs from the locked one"),
        ({}, False, "the stage-check record is missing"),
        ({"drop": ["check"]}, False, "no stage-check record"),
    ],
    ids=["different-record", "locked-record-not-stored", "nothing-recorded"],
)
async def test_a_stop_needs_the_locked_stage_check_record(
    lab: Lab, overrides: dict[str, Any], stored: bool, message: str
) -> None:
    lab.stored_check = stored
    stage, checkpoint = lab.stage(lab.lock(**overrides))

    with pytest.raises(TrialInvalid, match=message) as refused_:
        await stage.prepare(checkpoint)

    assert refused_.value.kind == "instrument"
    assert lab.container.calls == []  # the agent's setup never ran


# the stage's prepare: skills, params, prompt and instructions -----------------------------------


async def test_prepare_places_the_packages_skills_and_records_their_bytes(lab: Lab) -> None:
    stage, checkpoint = lab.stage(params={"min_words": 6})

    await stage.prepare(checkpoint)

    (skill,) = [item for item in lab.package.files if item.path.startswith("skills/")]
    relative = f"{PLACEMENT}/note-keeping/SKILL.md"
    assert lab.container.host(f"/app/{relative}").read_bytes() == skill.data
    record = f"workflow/components/note-state/{relative}"
    assert stage.components == [{"path": relative, "sha256": skill.sha256, "record": record}]
    assert (checkpoint.logs_dir / record).read_bytes() == skill.data


async def test_prepare_frames_one_prompt_with_overridden_params_and_runs_setup_with_them(
    lab: Lab,
) -> None:
    stage, checkpoint = lab.stage(params={"min_words": 6})

    await stage.prepare(checkpoint)

    assert "at least 6 words" in stage.prompt
    assert stage.prompt.endswith(f"Task: {TASK}")
    assert "Say what you changed." in stage.prompt
    assert [c["params"]["min_words"] for c in lab.container.tool_calls("setup/1")] == [6]


async def test_the_workflow_instructions_are_each_packages_text_in_order(lab: Lab) -> None:
    stage, _ = lab.stage()
    stage.load()
    assert stage.instructions_text == (NOTE_STATE / "instructions.md").read_text().strip("\n")

    lab.without_instructions()
    bare, _ = lab.stage(lab.lock())
    bare.load()

    assert bare.instructions_text is None


def test_the_prompt_is_not_available_before_prepare(lab: Lab) -> None:
    stage, _ = lab.stage()

    with pytest.raises(AssertionError):
        _ = stage.prompt


# the stage's check, close and record -------------------------------------------------------------


async def test_close_collects_outputs_then_refuses_an_installed_tree_that_changed(
    lab: Lab,
) -> None:
    stage, checkpoint = lab.stage()
    await stage.prepare(checkpoint)
    lab.container.write_note("a note of enough words here")
    lab.trees["digest"] = "x" * 64  # the agent changed the tool tree during the trial

    with pytest.raises(TrialInvalid, match="changed during the trial") as refused_:
        await stage.close(checkpoint, None)

    assert refused_.value.kind == "integrity"
    outputs = stage.package_record()["outputs/1"]["note-state"]
    assert outputs["note"]["error"] is None  # the evidence was kept before the refusal
    assert outputs["cut"] is None


async def test_a_stage_that_never_prepared_closes_empty_and_records_nothing(lab: Lab) -> None:
    stage, checkpoint = lab.stage()

    await stage.close(checkpoint, "invalid")

    assert stage.package_record() == {}
    assert stage.checkpoint is None


# gate fix budget and order -----------------------------------------------------------------------


async def test_gate_fixes_stop_at_the_budget_and_retain_each_rounds_evidence(lab: Lab) -> None:
    stage, checkpoint = lab.stage(fix_rounds=2)
    await stage.prepare(checkpoint)
    lab.container.write_note("a note of enough words here")
    ScriptedGate.outcomes = [refused(), refused(), refused()]

    first = await stage.check(checkpoint, "turn-1")
    second = await stage.check(checkpoint, "turn-2")
    third = await stage.check(checkpoint, "turn-3")

    message = "The checks did not pass (note-ready): lint failed. Fix your work, then finish."
    assert first is not None
    assert (first.kind, first.message) == (GATE_FIX, message)
    assert second is not None
    assert (second.kind, second.message) == (GATE_FIX, message)
    assert third is None  # the budget is spent: the failing gate is left for scoring
    assert ScriptedGate.retained == [1, 2]


async def test_no_fix_budget_leaves_a_refused_gate_unresumed(lab: Lab) -> None:
    stage, checkpoint = lab.stage(fix_rounds=0)
    await stage.prepare(checkpoint)
    lab.container.write_note("a note of enough words here")
    ScriptedGate.outcomes = [refused()]

    assert await stage.check(checkpoint, "turn-1") is None
    assert ScriptedGate.retained == []


async def test_an_agent_error_is_fixed_like_a_refusal_but_a_pass_is_not(lab: Lab) -> None:
    stage, checkpoint = lab.stage(fix_rounds=2)
    await stage.prepare(checkpoint)
    lab.container.write_note("a note of enough words here")
    broke = GateOutcome(Attribution.AGENT_ERROR, {"checks": []}, "a check was killed")
    ScriptedGate.outcomes = [passed(), broke]

    assert await stage.check(checkpoint, "turn-1") is None
    resume = await stage.check(checkpoint, "turn-2")

    assert resume is not None
    assert resume.message == (
        "The checks did not pass: a check was killed. Fix your work, then finish."
    )
    assert ScriptedGate.retained == [1]


async def test_a_gate_fix_that_cannot_fit_the_deadline_with_its_rerun_is_not_started(
    lab: Lab,
) -> None:
    stage, checkpoint = lab.stage(fix_rounds=1, deadline=time.monotonic() + 10)
    checkpoint.turns.append(Spent(GATE_FIX, 6.0))
    ScriptedGate.seconds = 5.0
    await stage.prepare(checkpoint)
    lab.container.write_note("a note of enough words here")
    ScriptedGate.outcomes = [refused()]

    assert await stage.check(checkpoint, "turn-1") is None
    assert ScriptedGate.retained == []  # no round was consumed or retained


async def test_a_refused_gate_is_fixed_before_any_stop_is_asked(lab: Lab) -> None:
    stage, checkpoint = lab.stage(fix_rounds=1)
    await stage.prepare(checkpoint)  # no note: the stop would ask for a rework
    lab.container.calls.clear()
    ScriptedGate.outcomes = [refused()]

    resume = await stage.check(checkpoint, "turn-1")

    assert resume is not None
    assert resume.kind == GATE_FIX
    assert lab.container.tool_calls("stop/1") == []


async def test_the_canary_compares_the_loaded_packages_own_code_not_the_arms_locked_identity(
    lab: Lab,
) -> None:
    """The stage-check record carries ``package_primitives`` (adapter-independent); the arm's
    locked ``primitive_code`` is adapter-inclusive. A package whose record was taken against other
    code is refused before the agent starts; the real record runs the stop, and a ``done`` stop
    keeps its output."""
    lab.primitives = {"stop/1": "p" * 64}
    stale, checkpoint = lab.stage()
    with pytest.raises(TrialInvalid) as refused_:
        await stale.prepare(checkpoint)
    assert refused_.value.kind == "instrument"

    lab.primitives = None
    stage, checkpoint = lab.stage()
    await stage.prepare(checkpoint)
    lab.container.write_note("a note of enough words here")

    assert await stage.check(checkpoint, "turn-1") is None
    await stage.close(checkpoint, None)

    record = stage.package_record()
    (decision,) = [
        item for item in record["stop/1"]["note-state"]["decisions"] if item["action"] == "done"
    ]
    assert decision["outputs"]["note"]["error"] is None
    kept = record["outputs/1"]["note-state"]["note"]
    assert kept["sha256"] == hashlib.sha256(b"a note of enough words here").hexdigest()


@pytest.mark.parametrize(
    ("attribution", "kind"),
    [(Attribution.INSTRUMENT, "instrument"), (Attribution.INTEGRITY, "integrity")],
)
async def test_an_invalid_gate_is_an_invalid_trial_even_with_fix_budget_left(
    lab: Lab, attribution: Attribution, kind: str
) -> None:
    stage, checkpoint = lab.stage(fix_rounds=3)
    await stage.prepare(checkpoint)
    ScriptedGate.outcomes = [GateOutcome(attribution, {"why": "broker"}, "gate state changed")]

    with pytest.raises(TrialInvalid, match="gate state changed") as refused_:
        await stage.check(checkpoint, "turn-1")

    assert refused_.value.kind == kind
    assert refused_.value.record == {"why": "broker"}
    assert ScriptedGate.retained == []


async def test_a_gate_that_cannot_run_or_reach_the_container_is_an_invalid_trial(
    lab: Lab,
) -> None:
    stage, checkpoint = lab.stage(fix_rounds=1)
    await stage.prepare(checkpoint)
    ScriptedGate.outcomes = [GateInvalid("instrument", "gate payload missing")]
    with pytest.raises(TrialInvalid, match="gate payload missing") as invalid:
        await stage.check(checkpoint, "turn-1")
    ScriptedGate.outcomes = [TransportError("exec failed")]
    with pytest.raises(TrialInvalid, match="container transport failed: exec failed") as moved:
        await stage.check(checkpoint, "turn-2")

    assert invalid.value.kind == "instrument"
    assert moved.value.kind == "integrity"


async def test_a_stop_call_over_a_changed_tool_tree_is_an_invalid_trial(lab: Lab) -> None:
    stage, checkpoint = lab.stage()
    await stage.prepare(checkpoint)
    lab.container.tampered = True

    with pytest.raises(TrialInvalid, match="installed tree changed") as refused_:
        await stage.check(checkpoint, "turn-1")

    assert refused_.value.kind == "integrity"


async def test_a_stop_that_gives_unusable_output_repairs_then_ends_the_checkpoint(
    lab: Lab,
) -> None:
    stage, checkpoint = lab.stage()
    await stage.prepare(checkpoint)
    healthy = lab.container.tool

    def corrupt_agent_state(
        container: FakeContainer, argv: Sequence[str], context: dict[str, Any]
    ) -> ToolOutcome:
        if context["state"] == WATCH:  # only the agent's own state; the canary control stays sound
            return ToolOutcome(b"junk")
        return healthy(container, argv, context)

    lab.container.tool = corrupt_agent_state

    first = await stage.check(checkpoint, "turn-1")
    second = await stage.check(checkpoint, "turn-2")

    assert first is not None
    assert first.kind == REPAIR
    assert second is None
    assert stage.package_record()["stop/1"]["note-state"]["closed"] is True


# the whole runner over a scripted agent ----------------------------------------------------------

Script = Callable[[Env], None]


class ScriptedInner(CodexNative):
    """The model's seat: each turn runs the next script step against the container."""

    script: ClassVar[list[Script]] = []
    prompts: ClassVar[list[tuple[bool, str]]] = []
    specs: ClassVar[list[dict[str, Any]]] = []
    container: ClassVar[Env]
    failure: ClassVar[Exception | None] = None

    @staticmethod
    def name() -> str:
        return "scripted-inner"

    def version(self) -> str | None:
        return None

    async def setup(self, environment: Any) -> None:
        return None

    async def run(self, instruction: str, environment: Any, context: AgentContext) -> None:
        self._turn(instruction, False, context)

    async def resume(self, instruction: str, environment: Any, context: AgentContext) -> None:
        self._turn(instruction, True, context)

    def _turn(self, instruction: str, resumed: bool, context: AgentContext) -> None:
        cls = type(self)
        cls.prompts.append((resumed, instruction))
        turns = len(cls.prompts)
        context.n_input_tokens, context.n_cache_tokens = 1000 * turns, 600 * turns
        context.n_output_tokens, context.cost_usd = 50 * turns, None
        (self.logs_dir / "trajectory.json").write_text(json.dumps({"session_id": "session-1"}))
        cls.script.pop(0)(cls.container)
        if cls.failure is not None:
            raise cls.failure


def write(text: str) -> Script:
    return lambda env: env.write_note(text)


def close_it(env: Env) -> None:
    env.host(WATCH).mkdir(parents=True, exist_ok=True)
    env.host(f"{WATCH}/abort").write_text("")


def tamper(env: Env) -> None:
    env.tampered = True


@pytest.fixture
def inner(lab: Lab, monkeypatch: pytest.MonkeyPatch) -> type[ScriptedInner]:
    ScriptedInner.script = []
    ScriptedInner.prompts = []
    ScriptedInner.specs = []
    ScriptedInner.failure = None
    ScriptedInner.container = lab.container

    def create(runner: Any, spec: Any, logs_dir: Path, *, model_name: str | None) -> Any:
        ScriptedInner.specs.append(dict(spec.kwargs))
        return ScriptedInner(logs_dir=logs_dir, model_name=model_name)

    monkeypatch.setattr("raidar.subject.workflow.runner.create_inner", create)
    return ScriptedInner


async def run_workflow(
    lab: Lab,
    *,
    locked: dict[str, dict[str, Any]] | None = None,
    params: dict[str, Any] | None = None,
    fix_rounds: int = 0,
) -> tuple[dict[str, Any], WorkflowRunner]:
    logs = lab.tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    runner = WorkflowRunner(
        logs_dir=logs,
        environment_logs_dir=PurePosixPath(logs),
        model_name="openai/gpt-6-luna",
        inner_agent={"name": "codex"},
        workflow="acme/note-flow@1",
        definition=definition_json(params, fix_rounds),
        locked_packages=lab.lock() if locked is None else locked,
        primitives=PRIMITIVES,
        pack=PACK,
        skills_placement=PLACEMENT,
    )
    await runner.run(TASK, lab.container, AgentContext())  # type: ignore[arg-type]
    return recorded(lab), runner


def recorded(lab: Lab) -> dict[str, Any]:
    record: dict[str, Any] = json.loads(
        (lab.tmp_path / "logs" / WORKFLOW_DIR / RECORD_FILE).read_text()
    )
    return record


async def test_a_gate_fix_then_a_rework_then_done_resume_one_session_in_that_order(
    lab: Lab, inner: type[ScriptedInner]
) -> None:
    inner.script = [write("two words"), write("still two"), write("now a note of seven words")]
    ScriptedGate.outcomes = [refused(), passed(), passed()]

    record, runner = await run_workflow(lab, params={"min_words": 6}, fix_rounds=1)

    assert [(t["stage"], t["kind"], t["resumed"]) for t in record["turns"]] == [
        ("build", "first_build", False),
        ("build", GATE_FIX, True),
        ("build", REWORK, True),
    ]
    first, fix, rework = (text for _, text in inner.prompts)
    assert runner.package_stage is not None
    assert first == runner.package_stage.prompt
    assert "at least 6 words" in first
    assert first.endswith(f"Task: {TASK}")
    assert fix == "The checks did not pass (note-ready): lint failed. Fix your work, then finish."
    assert rework == "Write a note of at least 6 words."
    assert ScriptedGate.retained == [1]
    assert record["stop/1"]["note-state"]["closed"] is False
    assert record["outputs/1"]["note-state"]["note"]["error"] is None
    assert record["outputs/1"]["note-state"]["cut"] is None
    assert "invalid" not in record
    assert record["integrity"] == []
    for key in ("frame/1", "install/1", "setup/1", "gate/1"):
        assert key in record
    (skill,) = [item for item in lab.package.files if item.path.startswith("skills/")]
    assert [c["sha256"] for c in record["components"]] == [skill.sha256]
    expected = (NOTE_STATE / "instructions.md").read_text().strip("\n")
    assert {spec["instructions"] for spec in inner.specs} == {expected}


async def test_a_package_that_closes_ends_after_one_turn_with_its_stop_recorded_closed(
    lab: Lab, inner: type[ScriptedInner]
) -> None:
    inner.script = [close_it]

    record, _ = await run_workflow(lab)

    assert [t["kind"] for t in record["turns"]] == ["first_build"]
    assert record["stop/1"]["note-state"]["closed"] is True
    assert "invalid" not in record


@pytest.mark.parametrize(
    ("lock", "kind", "fragment"),
    [
        ({"sha256": "0" * 64}, "integrity", "on disk"),
        ({"installed": {ARCH: "u" * 64}}, "integrity", "differs from the locked"),
        ({"check": "f" * 64}, "instrument", "differs from the locked one"),
    ],
    ids=["bytes", "installed-tree", "check-record"],
)
async def test_a_locked_difference_is_an_invalid_trial_and_the_agent_never_starts(
    lab: Lab,
    inner: type[ScriptedInner],
    lock: dict[str, Any],
    kind: str,
    fragment: str,
) -> None:
    record, _ = await run_workflow(lab, locked=lab.lock(**lock))

    assert inner.prompts == []
    assert record["turns"] == []
    assert record["invalid"]["kind"] == kind
    assert fragment in record["invalid"]["detail"]
    assert record["integrity"] == [
        {"name": f"package_{kind}", "detail": record["invalid"]["detail"], "turn": "packages"}
    ]
    assert "outputs/1" not in record  # no checkpoint ever began


async def test_an_unlocked_package_is_invalid_with_nothing_installed(
    lab: Lab, inner: type[ScriptedInner]
) -> None:
    record, _ = await run_workflow(lab, locked={})

    assert record["invalid"]["kind"] == "integrity"
    assert "not in the lock" in record["invalid"]["detail"]
    assert inner.prompts == []
    assert lab.installs.count == 0


async def test_a_tool_tree_changed_during_a_turn_cuts_the_checkpoint_as_invalid(
    lab: Lab, inner: type[ScriptedInner]
) -> None:
    inner.script = [tamper]

    record, _ = await run_workflow(lab)

    assert len(record["turns"]) == 1  # the agent ran; its check found the instrument changed
    assert record["invalid"]["kind"] == "integrity"
    assert "installed tree changed" in record["invalid"]["detail"]
    assert record["outputs/1"]["note-state"]["cut"] == "invalid"


async def test_an_installed_tree_changed_by_the_end_marks_invalid_after_keeping_outputs(
    lab: Lab, inner: type[ScriptedInner]
) -> None:
    def change_tree(env: Env) -> None:
        env.write_note("a note of enough words here")
        lab.trees["digest"] = "x" * 64

    inner.script = [change_tree]

    record, _ = await run_workflow(lab)

    assert [t["kind"] for t in record["turns"]] == ["first_build"]
    assert record["invalid"]["kind"] == "integrity"
    assert "changed during the trial" in record["invalid"]["detail"]
    assert record["outputs/1"]["note-state"]["note"]["error"] is None


async def test_a_failing_turn_still_reaches_harbor_and_cuts_the_outputs_as_a_turn_error(
    lab: Lab, inner: type[ScriptedInner]
) -> None:
    inner.script = [write("a note of enough words here")]
    inner.failure = RuntimeError("agent crashed")

    with pytest.raises(RuntimeError, match="agent crashed"):
        await run_workflow(lab)

    record = recorded(lab)
    assert record["turns"][0]["error"] == "RuntimeError: agent crashed"
    assert record["outputs/1"]["note-state"]["cut"] == "turn_error"
    assert "invalid" not in record


def plant(path: Path, text: str) -> Script:
    def step(env: Env) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    return step


def both(*steps: Script) -> Script:
    def run_all(env: Env) -> None:
        for step in steps:
            step(env)

    return run_all


async def test_a_native_session_log_rewritten_between_turns_is_a_named_integrity_violation(
    lab: Lab, inner: type[ScriptedInner]
) -> None:
    session = lab.tmp_path / "logs" / "sessions" / "rollout.jsonl"
    inner.script = [
        both(write("two words"), plant(session, '{"turn": 1}\n')),
        both(
            write("now a note of seven words"),
            plant(session, '{"turn": "rewritten"}\n{"turn": 2}\n'),
        ),
    ]

    record, _ = await run_workflow(lab, params={"min_words": 6})

    assert [(i["name"], i["turn"]) for i in record["integrity"]] == [
        ("session_log_rewritten", "workflow/build-2")
    ]
    assert record["turns"][1]["integrity"][0]["name"] == "session_log_rewritten"


# published evidence -------------------------------------------------------------------------------


async def test_a_published_output_path_leads_to_the_kept_evidence_below_the_step_logs(
    lab: Lab, inner: type[ScriptedInner]
) -> None:
    inner.script = [write("a note of enough words here")]

    record, runner = await run_workflow(lab)

    logs = lab.tmp_path / "logs"
    copied = record["outputs/1"]["note-state"]["note"]
    relative = PurePosixPath(copied["host_path"])
    assert copied["error"] is None
    assert not relative.is_absolute()
    assert ".." not in relative.parts
    assert relative.parts[0] == "stages"
    kept = logs.joinpath(*relative.parts)
    assert kept.is_file()
    assert hashlib.sha256(kept.read_bytes()).hexdigest() == copied["sha256"]
    assert not runner.staging.exists()  # the record never needed the deleted staging directory
    assert str(runner.staging) not in json.dumps(record)


async def test_the_context_metadata_carries_the_same_portable_record(
    lab: Lab, inner: type[ScriptedInner]
) -> None:
    inner.script = [write("a note of enough words here")]
    context = AgentContext()
    logs = lab.tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    runner = WorkflowRunner(
        logs_dir=logs,
        environment_logs_dir=PurePosixPath(logs),
        model_name="openai/gpt-6-luna",
        inner_agent={"name": "codex"},
        workflow="acme/note-flow@1",
        definition=definition_json(),
        locked_packages=lab.lock(),
        primitives=PRIMITIVES,
        pack=PACK,
        skills_placement=PLACEMENT,
    )

    await runner.run(TASK, lab.container, context)  # type: ignore[arg-type]

    assert context.metadata is not None
    assert context.metadata["workflow"] == recorded(lab)


async def test_evidence_the_agent_planted_under_a_published_name_is_replaced(
    lab: Lab, inner: type[ScriptedInner]
) -> None:
    forged = lab.tmp_path / "logs" / "stages" / "forged.txt"
    inner.script = [both(write("a note of enough words here"), plant(forged, "forged"))]

    await run_workflow(lab)

    assert not forged.exists()
