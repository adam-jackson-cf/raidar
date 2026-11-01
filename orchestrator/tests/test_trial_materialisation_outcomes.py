"""Trial task materialisation outcomes: refusals, pinned workspace and the lab's own checks.

Every task, step and record lives in an in-memory file system (``VirtualFiles``) mounted under
``/vfs`` by intercepting ``Path`` and ``shutil``; paths elsewhere keep their real behaviour. So
the production parsing, copying, removal and mode changes run for real over virtual state and no
task or run tree is ever built on disk.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from harbor.models.task.config import TaskConfig

from raidar.contract.trial import ProgressionRule
from raidar.engine.trial import lab_checks, materialise, task_config
from raidar.engine.trial.lab_checks import lab_checks as run_lab_checks
from raidar.engine.trial.lab_checks import seed_parent
from raidar.engine.trial.materialise import (
    MaterialiseError,
    compose_init,
    environment_cold,
    environment_image_id,
    materialise_task,
)
from raidar.engine.trial.provision import PROVISION_DIR_NAME
from raidar.engine.trial.task_config import configure_trial_task
from raidar.engine.trial.workspace import (
    ABSENT,
    PIP_KEY,
    PROJECT_FILE,
    SEED_SLUG,
    SHA_KEY,
    lock_file,
    seed_check,
    seed_duration,
    seeded_digest,
    workspace_seed,
)

CUTOFF = "2026-10-27T12:00:00Z"
HALT = ProgressionRule(mode="halt_on_failure", reward_key="reward", threshold=1.0)
CONTINUE = ProgressionRule(mode="continue", reward_key="reward")
ROOT = Path("/vfs")


class VirtualFiles:
    """Files, directories and modes held in memory for paths under ``/vfs``."""

    def __init__(self) -> None:
        self.root = ROOT
        self.files: dict[Path, bytes] = {}
        self.dirs: set[Path] = {ROOT}
        self.modes: dict[Path, int] = {}

    def owns(self, path: Path) -> bool:
        return path == ROOT or ROOT in path.parents

    def mkdir(self, path: Path, parents: bool = False, exist_ok: bool = False) -> None:
        if path in self.dirs:
            if exist_ok:
                return
            raise FileExistsError(path)
        if path.parent not in self.dirs:
            if not parents:
                raise FileNotFoundError(path.parent)
            self.mkdir(path.parent, parents=True, exist_ok=True)
        self.dirs.add(path)

    def write(self, path: Path, data: bytes) -> None:
        if path.parent not in self.dirs:
            raise FileNotFoundError(path.parent)
        self.files[path] = data
        self.modes.setdefault(path, stat.S_IFREG | 0o644)

    def put(self, path: Path, text: str) -> Path:
        self.mkdir(path.parent, parents=True, exist_ok=True)
        self.write(path, text.encode())
        return path

    def read(self, path: Path) -> bytes:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def under(self, path: Path) -> list[Path]:
        """Every file and directory strictly below ``path``."""
        return sorted(p for p in {*self.files, *self.dirs} if path in p.parents)

    def children(self, path: Path) -> list[Path]:
        if path not in self.dirs:
            raise NotADirectoryError(path)
        return [p for p in self.under(path) if p.parent == path]

    def copytree(self, src: Path, dst: Path, symlinks: bool = False) -> None:
        if src not in self.dirs:
            raise FileNotFoundError(src)
        if dst in self.dirs:
            raise FileExistsError(dst)
        self.mkdir(dst, parents=True)
        for path in self.under(src):
            target = dst / path.relative_to(src)
            if path in self.dirs:
                self.dirs.add(target)
            else:
                self.files[target] = self.files[path]
                self.modes[target] = self.modes[path]

    def rmtree(self, path: Path, ignore_errors: bool = False) -> None:
        if path not in self.dirs:
            if ignore_errors:
                return
            raise FileNotFoundError(path)
        for item in self.under(path):
            self.files.pop(item, None)
            self.modes.pop(item, None)
            self.dirs.discard(item)
        self.dirs.discard(path)

    def digest(self, path: Path) -> str:
        parts = [
            f"{p.relative_to(path)}={self.files[p].decode()}"
            for p in self.under(path)
            if p in self.files
        ]
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()

    def digest_or_file(self, path: Path, original: Any) -> str:
        return self.digest(path) if self.owns(path) else original(path)


def _intercept_path(
    monkeypatch: pytest.MonkeyPatch,
    fs: VirtualFiles,
    name: str,
    original: Any,
    virtual: Any,
) -> None:
    def method(self: Path, *args: Any, **kwargs: Any) -> Any:
        if fs.owns(self):
            return virtual(self, *args, **kwargs)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, name, method)


@pytest.fixture
def vfs(monkeypatch: pytest.MonkeyPatch) -> VirtualFiles:
    fs = VirtualFiles()
    real = {
        name: getattr(Path, name)
        for name in (
            "read_text",
            "write_text",
            "read_bytes",
            "write_bytes",
            "exists",
            "is_dir",
            "is_file",
            "mkdir",
            "chmod",
            "stat",
            "iterdir",
        )
    }
    real_copytree, real_rmtree = shutil.copytree, shutil.rmtree

    def mkdir(self: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False) -> None:
        fs.mkdir(self, parents, exist_ok)

    def chmod(self: Path, mode: int, **_: Any) -> None:
        fs.modes[self] = (fs.modes[self] & ~0o7777) | mode

    virtual: dict[str, Any] = {
        "read_text": lambda self, *a, **k: fs.read(self).decode(),
        "write_text": lambda self, data, *a, **k: fs.write(self, data.encode()),
        "read_bytes": lambda self: fs.read(self),
        "write_bytes": lambda self, data: fs.write(self, data),
        "exists": lambda self, **k: self in fs.files or self in fs.dirs,
        "is_dir": lambda self, **k: self in fs.dirs,
        "is_file": lambda self, **k: self in fs.files,
        "mkdir": mkdir,
        "chmod": chmod,
        "stat": lambda self, **k: SimpleNamespace(st_mode=fs.modes[self]),
        "iterdir": lambda self: iter(fs.children(self)),
    }

    for name in real:
        _intercept_path(monkeypatch, fs, name, real[name], virtual[name])

    def copytree(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:
        if fs.owns(Path(src)):
            return fs.copytree(Path(src), Path(dst), *args, **kwargs)
        return real_copytree(src, dst, *args, **kwargs)

    def rmtree(path: Any, *args: Any, **kwargs: Any) -> Any:
        if fs.owns(Path(path)):
            return fs.rmtree(Path(path), *args, **kwargs)
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "copytree", copytree)
    monkeypatch.setattr(shutil, "rmtree", rmtree)
    real_digest = materialise.content_digest
    monkeypatch.setattr(
        materialise,
        "content_digest",
        lambda path: fs.digest_or_file(path, real_digest),
    )
    return fs


def _task_toml(*steps: str) -> str:
    return "".join(f'[[steps]]\nname = "{step}"\n' for step in steps)


def _task(fs: VirtualFiles, name: str, *steps: str) -> Path:
    root = fs.root / name
    fs.put(root / "task.toml", _task_toml(*steps))
    for step in steps:
        fs.put(root / "steps" / step / "instruction.md", step)
    return root


def _step_names(fs: VirtualFiles, task: Path) -> list[str]:
    config = TaskConfig.model_validate_toml(fs.read(task / "task.toml").decode())
    return [step.name for step in config.steps or []]


# --- task configuration -------------------------------------------------------------------


@pytest.fixture
def network_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, str, str]]:
    calls: list[tuple[Path, str, str]] = []
    monkeypatch.setattr(
        task_config,
        "restrict_trial_network",
        lambda task_dir, *, runtime, provider: calls.append((task_dir, runtime, provider)),
    )
    return calls


def test_a_trial_task_keeps_only_planned_steps_and_pins_their_progression(
    vfs: VirtualFiles, network_calls: list[tuple[Path, str, str]]
) -> None:
    task = _task(vfs, "task", "checkpoint_1", "checkpoint_2", "checkpoint_3")

    configure_trial_task(
        task, ("checkpoint_1", "checkpoint_3"), HALT, runtime="python", provider="openai"
    )

    steps = TaskConfig.model_validate_toml(vfs.read(task / "task.toml").decode()).steps or []
    assert [step.name for step in steps] == ["checkpoint_1", "checkpoint_3"]
    assert all(step.min_reward == {"reward": 1.0} for step in steps)
    assert all(len(step.artifacts) == 1 for step in steps)
    assert (task / "steps" / "checkpoint_1").is_dir()
    assert (task / "steps" / "checkpoint_3" / "instruction.md").is_file()
    assert not (task / "steps" / "checkpoint_2").exists()


def test_a_continuing_trial_sets_no_halt_threshold_on_its_steps(
    vfs: VirtualFiles, network_calls: list[tuple[Path, str, str]]
) -> None:
    task = _task(vfs, "task", "checkpoint_1", "checkpoint_2")

    configure_trial_task(task, ("checkpoint_1",), CONTINUE, runtime="python", provider="openai")

    steps = TaskConfig.model_validate_toml(vfs.read(task / "task.toml").decode()).steps or []
    assert [(step.name, step.min_reward) for step in steps] == [("checkpoint_1", None)]
    assert not (task / "steps" / "checkpoint_2").exists()


def test_a_plan_naming_steps_the_task_lacks_is_refused_before_anything_is_changed(
    vfs: VirtualFiles, network_calls: list[tuple[Path, str, str]]
) -> None:
    task = _task(vfs, "task", "checkpoint_1", "checkpoint_2")
    before = dict(vfs.files)

    with pytest.raises(ValueError, match=r"no steps \['checkpoint_9'\]"):
        configure_trial_task(
            task, ("checkpoint_1", "checkpoint_9"), HALT, runtime="python", provider="openai"
        )

    assert vfs.files == before
    assert (task / "steps" / "checkpoint_2").is_dir()
    assert network_calls == []


# --- compose init ---------------------------------------------------------------------------


def _compose(fs: VirtualFiles, task: Path, text: str) -> None:
    fs.put(task / materialise.COMPOSE_FILE, text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, False),
        ("services:\n  main:\n    init: true\n", True),
        ("services:\n  main:\n    init: false\n", False),
        ("services:\n  main:\n    init: 'true'\n", False),
        ("services:\n  other:\n    init: true\n", False),
        ("services: [main]\n", False),
        ("services:\n  main: busy\n", False),
        ("- not\n- a mapping\n", False),
        ("", False),
    ],
)
def test_compose_init_is_true_only_when_the_main_service_runs_with_init(
    vfs: VirtualFiles, text: str | None, expected: bool
) -> None:
    task = _task(vfs, "task", "checkpoint_1")
    if text is not None:
        _compose(vfs, task, text)

    assert compose_init(task) is expected


# --- materialisation -----------------------------------------------------------------------


class _Resolved:
    def __init__(self, digest: str) -> None:
        self._digest = digest
        self.agent = SimpleNamespace(provider="openai")

    def identity(self) -> dict[str, str]:
        return {"digest": self._digest}


def _arm(init: bool = False) -> SimpleNamespace:
    spec = SimpleNamespace(environment=SimpleNamespace(compose_init=init), runtime="python")
    return SimpleNamespace(spec=spec)


class _Lab:
    """Arm identity under the test's control; the step configuration is the real one."""

    digest = "a" * 64


@pytest.fixture
def lab(monkeypatch: pytest.MonkeyPatch, vfs: VirtualFiles) -> type[_Lab]:
    _Lab.digest = "a" * 64
    monkeypatch.setattr(materialise, "resolve_arm", lambda spec, arm_id: _Resolved(_Lab.digest))
    monkeypatch.setattr(
        task_config, "restrict_trial_network", lambda task_dir, *, runtime, provider: None
    )

    def setup_scripts(dest: Path, steps: list[str]) -> None:
        for step in steps:
            vfs.put(dest / "steps" / step / "workdir" / "setup.sh", "reset")

    monkeypatch.setattr(materialise, "write_setup_scripts", setup_scripts)
    return _Lab


def test_a_materialised_task_is_a_copy_with_the_planned_arm_applied(
    vfs: VirtualFiles, lab: type[_Lab]
) -> None:
    source = _task(vfs, "source", "checkpoint_1", "checkpoint_2")
    dest = vfs.root / "trial"
    pristine = dict(vfs.files)

    task = materialise_task(
        source, dest, steps=("checkpoint_2",), rule=HALT, arm=(_arm(), "guided", "a" * 64)
    )

    assert task.path == dest
    assert _step_names(vfs, dest) == ["checkpoint_2"]
    assert (dest / "steps" / "checkpoint_2" / "workdir" / "setup.sh").is_file()
    assert not (dest / "steps" / "checkpoint_1").exists()
    assert not (dest / "steps" / "checkpoint_2" / "solution").exists()
    assert task.digest == vfs.digest(dest)
    assert task.arm_apply_s >= 0.0
    # the pinned source is never rewritten
    assert {p: vfs.files[p] for p in pristine} == pristine
    assert _step_names(vfs, source) == ["checkpoint_1", "checkpoint_2"]


def test_reference_solutions_are_written_as_executable_solve_scripts_for_planned_steps_only(
    vfs: VirtualFiles, lab: type[_Lab]
) -> None:
    source = _task(vfs, "source", "checkpoint_1", "checkpoint_2")
    payload = vfs.root / "reference"
    vfs.put(payload / "pkg" / "mod.py", "x = 1\n")
    unplanned = vfs.root / "unplanned"
    vfs.put(unplanned / "other.py", "y = 2\n")
    dest = vfs.root / "trial"

    materialise_task(
        source,
        dest,
        steps=("checkpoint_1",),
        rule=CONTINUE,
        arm=(_arm(), "guided", "a" * 64),
        references={"checkpoint_1": payload, "checkpoint_2": unplanned},
    )

    solution = dest / "steps" / "checkpoint_1" / "solution"
    assert (solution / "payload" / "pkg" / "mod.py").read_text() == "x = 1\n"
    assert (solution / "solve.sh").read_text().strip() != ""
    mode = (solution / "solve.sh").stat().st_mode
    assert mode & stat.S_IXUSR
    assert mode & stat.S_IXGRP
    assert mode & stat.S_IXOTH
    assert not (dest / "steps" / "checkpoint_2").exists()
    assert not any(p.name == "other.py" for p in vfs.files if dest in p.parents)


def test_a_trial_task_is_never_built_over_an_existing_directory(
    vfs: VirtualFiles, lab: type[_Lab]
) -> None:
    source = _task(vfs, "source", "checkpoint_1")
    dest = vfs.root / "trial"
    vfs.mkdir(dest)

    with pytest.raises(MaterialiseError, match="never reused"):
        materialise_task(
            source, dest, steps=("checkpoint_1",), rule=HALT, arm=(_arm(), "guided", "a" * 64)
        )

    assert vfs.children(dest) == []


def test_a_task_whose_compose_init_differs_from_the_experiment_is_refused_uncopied(
    vfs: VirtualFiles, lab: type[_Lab]
) -> None:
    source = _task(vfs, "source", "checkpoint_1")
    _compose(vfs, source, "services:\n  main:\n    init: true\n")
    dest = vfs.root / "trial"

    with pytest.raises(MaterialiseError, match="compose init is True.*compose_init=False"):
        materialise_task(
            source, dest, steps=("checkpoint_1",), rule=HALT, arm=(_arm(False), "g", "a" * 64)
        )

    assert not dest.exists()


def test_an_arm_whose_identity_moved_since_planning_is_refused(
    vfs: VirtualFiles, lab: type[_Lab]
) -> None:
    source = _task(vfs, "source", "checkpoint_1")
    lab.digest = "b" * 64
    dest = vfs.root / "trial"

    with pytest.raises(MaterialiseError, match=r"'guided' resolves to b{12}, planned a{12}"):
        materialise_task(
            source, dest, steps=("checkpoint_1",), rule=HALT, arm=(_arm(), "guided", "a" * 64)
        )

    assert _step_names(vfs, dest) == ["checkpoint_1"]  # copied but never configured
    assert not (dest / "steps" / "checkpoint_1" / "workdir").exists()


def test_a_step_configuration_refusal_surfaces_as_a_materialisation_error(
    vfs: VirtualFiles, lab: type[_Lab]
) -> None:
    source = _task(vfs, "source", "checkpoint_1")
    dest = vfs.root / "trial"

    with pytest.raises(MaterialiseError, match=r"no steps \['checkpoint_9'\]"):
        materialise_task(
            source, dest, steps=("checkpoint_9",), rule=HALT, arm=(_arm(), "guided", "a" * 64)
        )

    assert not (dest / "steps" / "checkpoint_9").exists()
    assert not (dest / "steps" / "checkpoint_1" / "workdir").exists()


# --- environment image ---------------------------------------------------------------------


def _image_task(fs: VirtualFiles, body: str) -> Path:
    return fs.put(fs.root / "image-task" / "task.toml", body).parent


@pytest.mark.parametrize("body", ["", "[environment]\n", "[environment]\ndocker_image = 7\n"])
def test_a_task_without_a_named_image_is_neither_cold_nor_identified(
    vfs: VirtualFiles, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    def refuse(tag: str) -> Any:
        raise AssertionError("the image store must not be asked about an unnamed image")

    monkeypatch.setattr(materialise, "image_present", refuse)
    monkeypatch.setattr(materialise, "inspect_image", refuse)
    task = _image_task(vfs, body)

    assert environment_cold(task) is False
    assert environment_image_id(task) is None


def test_a_named_image_missing_from_the_store_is_cold_and_has_no_id(
    vfs: VirtualFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(materialise, "image_present", lambda tag: False)
    monkeypatch.setattr(materialise, "inspect_image", lambda tag: None)
    task = _image_task(vfs, '[environment]\ndocker_image = "raidar/x:1"\n')

    assert environment_cold(task) is True
    assert environment_image_id(task) is None


def test_a_named_image_in_the_store_is_warm_and_reports_its_id(
    vfs: VirtualFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = {"raidar/x:1": SimpleNamespace(image_id="sha256:abc")}
    monkeypatch.setattr(materialise, "image_present", lambda tag: tag in store)
    monkeypatch.setattr(materialise, "inspect_image", lambda tag: store.get(tag))
    task = _image_task(vfs, '[environment]\ndocker_image = "raidar/x:1"\n')

    assert environment_cold(task) is False
    assert environment_image_id(task) == "sha256:abc"


# --- workspace seed -------------------------------------------------------------------------


def _record(fs: VirtualFiles, agent_dir: Path, *records: dict[str, Any]) -> None:
    path = agent_dir / PROVISION_DIR_NAME / f"{SEED_SLUG}.jsonl"
    fs.put(path, "\n".join(json.dumps(r) for r in records) + "\n")


def _seed_record(*, seeded: str | None, found: str | None, pip: str | None = "enforced") -> dict:
    values = {SHA_KEY: found, PIP_KEY: pip}
    return {"facts": {SHA_KEY: seeded}, "check": {"values": values}}


def test_a_step_without_a_seed_record_fails_the_lab_check(vfs: VirtualFiles) -> None:
    passed, detail = seed_check(vfs.root / "agent")

    assert not passed
    assert "no provision record before the agent started" in detail


def test_a_seeded_project_present_and_matching_at_start_passes(vfs: VirtualFiles) -> None:
    agent = vfs.root / "agent"
    _record(vfs, agent, _seed_record(seeded="abc", found="abc"))

    passed, detail = seed_check(agent)

    assert passed
    assert detail == f"{SEED_SLUG}: seeded {PROJECT_FILE} present"


def test_the_latest_seed_record_decides_the_check(vfs: VirtualFiles) -> None:
    agent = vfs.root / "agent"
    _record(
        vfs,
        agent,
        _seed_record(seeded="abc", found="zzz"),
        _seed_record(seeded="abc", found="abc"),
    )

    assert seed_check(agent)[0] is True


def test_a_changed_project_at_the_start_fails_and_names_both_digests(vfs: VirtualFiles) -> None:
    agent = vfs.root / "agent"
    _record(vfs, agent, _seed_record(seeded="abc", found="def"))

    passed, detail = seed_check(agent)

    assert not passed
    assert "was def at the start, seeded abc" in detail


def test_a_failed_install_reports_the_project_as_unchecked(vfs: VirtualFiles) -> None:
    agent = vfs.root / "agent"
    _record(vfs, agent, _seed_record(seeded="abc", found=None))

    passed, detail = seed_check(agent)

    assert not passed
    assert "unchecked (the install failed)" in detail


def test_a_record_that_never_declared_a_seeded_digest_fails(vfs: VirtualFiles) -> None:
    agent = vfs.root / "agent"
    _record(vfs, agent, {"facts": None, "check": None})

    passed, detail = seed_check(agent)

    assert not passed
    assert "seeded None" in detail


def test_an_agent_pip_that_cannot_honour_the_cutoff_fails_an_otherwise_good_seed(
    vfs: VirtualFiles,
) -> None:
    agent = vfs.root / "agent"
    _record(vfs, agent, _seed_record(seeded="abc", found="abc", pip="unsupported"))

    passed, detail = seed_check(agent)

    assert not passed
    assert "cannot honour the dependency cutoff" in detail


def test_an_absent_project_digest_is_not_a_seed(vfs: VirtualFiles) -> None:
    agent = vfs.root / "agent"
    _record(vfs, agent, _seed_record(seeded="abc", found=ABSENT))

    passed, detail = seed_check(agent)

    assert not passed
    assert f"was {ABSENT} at the start" in detail


def test_the_seeded_digest_is_the_recorded_install_snapshot(vfs: VirtualFiles) -> None:
    agent = vfs.root / "agent"
    _record(vfs, agent, {"install": {"snapshot_digest": "sha256:aa"}})

    assert seeded_digest(agent) == "sha256:aa"


@pytest.mark.parametrize(
    "record",
    [{}, {"install": None}, {"install": {"snapshot_digest": 5}}, {"install": {}}],
)
def test_a_record_without_a_snapshot_digest_has_no_seeded_digest(
    vfs: VirtualFiles, record: dict[str, Any]
) -> None:
    agent = vfs.root / "agent"
    _record(vfs, agent, record)

    assert seeded_digest(agent) is None


def test_with_no_record_at_all_there_is_no_seeded_digest(vfs: VirtualFiles) -> None:
    assert seeded_digest(vfs.root / "agent") is None


def _step_records(
    fs: VirtualFiles, trial: Path, installs: dict[str, dict[str, Any] | None]
) -> None:
    for step, install in installs.items():
        record: dict[str, Any] = {} if install is None else {"install": install}
        _record(fs, trial / "steps" / step / "agent", record)


def test_the_seed_duration_comes_from_the_first_step_that_recorded_one(
    vfs: VirtualFiles,
) -> None:
    trial = vfs.root / "trial"
    _step_records(
        vfs,
        trial,
        {
            "checkpoint_1": None,
            "checkpoint_2": {"duration_s": 12},
            "checkpoint_3": {"duration_s": 99.5},
        },
    )

    duration = seed_duration(trial, ["checkpoint_1", "checkpoint_2", "checkpoint_3"])

    assert duration == 12.0
    assert isinstance(duration, float)


def test_a_boolean_or_missing_duration_is_not_a_seed_duration(vfs: VirtualFiles) -> None:
    trial = vfs.root / "trial"
    _step_records(
        vfs,
        trial,
        {"checkpoint_1": {"duration_s": True}, "checkpoint_2": {"duration_s": "3"}},
    )

    assert seed_duration(trial, ["checkpoint_1", "checkpoint_2", "checkpoint_3"]) is None
    assert seed_duration(trial, []) is None


# --- workspace seed provision ---------------------------------------------------------------


def test_a_seed_with_dependencies_pins_one_lock_per_architecture_and_installs_from_them() -> None:
    locks = {"amd64": "# amd64\n", "arm64": "# arm64\n"}

    seed = workspace_seed("scb/adder@1", ["numpy>=2"], "3.12", CUTOFF, locks)

    assert {lock_file("amd64"), lock_file("arm64")} <= set(seed.payload)
    assert seed.payload[lock_file("amd64")].content == b"# amd64\n"
    assert seed.install != workspace_seed("scb/adder@1", [], "3.12", CUTOFF, {}).install
    assert set(seed.facts["lock_sha256"]) == {"amd64", "arm64"}
    assert seed.facts["dependencies"] == ["numpy>=2"]


_LOCK = {"amd64": "# amd64\n"}


def test_a_seed_without_dependencies_installs_no_locked_closure() -> None:
    seed = workspace_seed("scb/adder@1", [], "3.12", CUTOFF, {})

    assert seed.install != workspace_seed("scb/adder@1", ["numpy"], "3.12", CUTOFF, _LOCK).install
    assert seed.facts["lock_sha256"] == {}
    assert not any(name.startswith("pylock.") for name in seed.payload)


def test_a_seed_with_locks_but_no_dependencies_is_refused() -> None:
    with pytest.raises(ValueError, match="needs their locks, and only then"):
        workspace_seed("scb/adder@1", [], "3.12", CUTOFF, {"amd64": "# lock\n"})


# --- lab checks -----------------------------------------------------------------------------


@pytest.fixture
def checks(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "reset": (True, "ok"),
        "provisions": {},
        "seed": (True, "seeded"),
        "digest": "sha256:seed",
        "seed_calls": 0,
    }

    monkeypatch.setattr(
        lab_checks,
        "reset_check",
        lambda agent_dir: SimpleNamespace(passed=state["reset"][0], detail=state["reset"][1]),
    )
    monkeypatch.setattr(
        lab_checks, "provision_check", lambda agent_dir, slug: state["provisions"][slug]
    )

    def seed(agent_dir: Path) -> tuple[bool, str]:
        state["seed_calls"] += 1
        return state["seed"]

    monkeypatch.setattr(lab_checks, "seed_check", seed)
    monkeypatch.setattr(lab_checks, "seeded_digest", lambda agent_dir: state["digest"])
    return state


def test_a_step_whose_reset_and_provisions_hold_passes_the_lab_checks(
    checks: dict[str, Any],
) -> None:
    checks["provisions"] = {"tool": (True, "tool: provisioned")}
    checks["seed"] = (False, "seed missing")  # a first step without the seed never consults it

    summary = run_lab_checks(ROOT, ["tool"], first_step=True)

    assert summary.passed is True
    assert summary.failures == ()
    assert checks["seed_calls"] == 0


def test_every_failed_lab_check_is_reported_in_order(checks: dict[str, Any]) -> None:
    checks["reset"] = (False, "state left behind")
    checks["provisions"] = {"tool": (False, "tool: no record"), SEED_SLUG: (True, "x")}
    checks["seed"] = (False, "seed missing")

    summary = run_lab_checks(ROOT, ["tool", SEED_SLUG], first_step=True)

    assert summary.passed is False
    assert summary.failures == (
        "context-reset: state left behind",
        "tool: no record",
        "seed missing",
    )


def test_the_seeded_project_is_checked_only_at_checkpoint_one(checks: dict[str, Any]) -> None:
    checks["provisions"] = {SEED_SLUG: (True, "x")}
    checks["seed"] = (False, "seed missing")

    later = run_lab_checks(ROOT, [SEED_SLUG], first_step=False)

    assert later.passed is True
    assert checks["seed_calls"] == 0


def test_checkpoint_one_has_a_seed_parent_only_when_the_seed_is_provisioned(
    checks: dict[str, Any],
) -> None:
    assert seed_parent(ROOT, [SEED_SLUG]) == "sha256:seed"
    assert seed_parent(ROOT, ["tool"]) is None
