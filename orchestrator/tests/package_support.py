"""Builders for stage-package tests: real files in a temp tree, and the package-stage lab."""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
import yaml
from raidar_contract.canonical import digest_json
from raidar_contract.package.manifest import StagePackage
from raidar_contract.references import Release
from raidar_contract.workflow import PackageWorkflowDefinition

from raidar.subject.package.registry import load_stage_package, stage_folder
from raidar.subject.primitive import calls
from raidar.subject.primitive.calls import TreeManifest
from raidar.subject.primitive.canary import StageCheckRecord
from raidar.subject.primitive.gate import Attribution, GateOutcome
from raidar.subject.primitive.install import InstalledPackage, InstallError
from raidar.subject.workflow import identity, package_trial
from raidar.subject.workflow import package as package_module
from raidar.subject.workflow.identity import package_primitives
from raidar.subject.workflow.package_stage import PackageStage
from raidar.subject.workflow.stage import Checkpoint
from tests.lifecycle_support import (
    NOTE_STATE,
    PACK,
    FakeContainer,
    ScriptedRunner,
    install_container,
    installed,
    stage_check_record,
)

NAMESPACE = "acme"
STAGE_FIXTURES = Path(__file__).parent / "fixtures" / "stages" / NAMESPACE

BASE_MANIFEST: dict[str, Any] = {"contract": "stage/1", "name": "demo", "summary": "A demo."}


def fixture_package(name: str, destination: Path) -> Path:
    """A mutable copy of fixture package ``name`` at ``destination/acme/name``."""
    target = destination / NAMESPACE / name
    shutil.copytree(STAGE_FIXTURES / name, target)
    return target


def write_package(
    parent: Path,
    manifest: dict[str, Any] | None = None,
    files: dict[str, str | bytes] | None = None,
    *,
    prompt: str | None = "Prompt.\n",
) -> Path:
    """A package folder named by its manifest, holding ``stage.yaml``, a prompt and ``files``."""
    data = {**BASE_MANIFEST, **(manifest or {})}
    root = parent / str(data["name"])
    root.mkdir(parents=True)
    (root / "stage.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    if prompt is not None:
        (root / "prompt.md").write_text(prompt, encoding="utf-8")
    for relative, content in (files or {}).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
    return root


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
    """The lifecycle container as a Harbor environment whose logs are mounted (no host shell)."""

    capabilities = SimpleNamespace(mounted=True)


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


@cache
def note_state_package() -> StagePackage:
    """The note-state fixture package, loaded once: it is immutable and copies are identical."""
    return load_stage_package(NOTE_STATE)


def make_lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env_class: type[Env] = Env) -> Lab:
    """A ``Lab`` with the install, stage-check store, gate and call runner replaced by doubles."""
    monkeypatch.setattr(
        identity,
        "_digest",
        lambda roots, adapter: digest_json({"roots": roots, "adapter": str(adapter)}),
    )
    repo = tmp_path / "repo"
    folder = stage_folder(RELEASE, repo)
    shutil.copytree(NOTE_STATE, folder)
    container = env_class(tmp_path / "container")
    container.root.mkdir()
    install_container(monkeypatch, container)
    package = replace(note_state_package(), root=folder)  # the copy's bytes are the fixture's
    found = Lab(tmp_path, repo, container, package, {"digest": TREE})

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
