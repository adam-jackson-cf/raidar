"""``raidar lock``: content hashes, versions, determinism and gaps (WS-A: A2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.spec.loader import CliOverrides, load_experiment
from raidar.spec.lock import LockError, build_lock, default_resolvers, render_lock
from raidar.spec.policies import PolicyResolver
from raidar.spec.references import (
    RefKind,
    RegistryRef,
    RegistryReferenceError,
    ResolverSet,
)
from tests.experiment_spec_support import (
    InMemoryResolver,
    experiment_document,
    fixture_resolvers,
    write_experiment,
)


def _lock_bytes(
    path: Path, *, checkpoints: int = 8, overrides: CliOverrides | None = None
) -> bytes:
    resolvers = ResolverSet(
        [*fixture_resolvers(checkpoints=checkpoints), PolicyResolver()], entry_point_group=None
    )
    loaded = load_experiment(path, overrides)
    return render_lock(
        build_lock(loaded, lock_path=path.with_name("experiment.lock.json"), resolvers=resolvers)
    )


def _lock(path: Path, **kwargs: Any) -> dict[str, Any]:
    return json.loads(_lock_bytes(path, **kwargs))


def test_relocking_unchanged_inputs_is_byte_identical(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, experiment_document())

    first = _lock_bytes(path)
    second = _lock_bytes(path)

    assert first == second
    assert first.endswith(b"\n")
    lock = json.loads(first)
    assert list(lock) == sorted(lock)
    assert lock["files"]["components/AGENTS.md"]["sha256"]
    assert lock["experiment"]["source"] == "experiment.yaml"
    assert set(lock["versions"]) == {
        "python",
        "harbor",
        "litellm",
        "raidar",
        "raidar_spec",
        "harnesses",
        "tools",
    }
    assert lock["versions"]["harbor"] == "0.23.0"
    assert lock["versions"]["harnesses"] == {"luna": {"harness": "codex-cli", "version": "0.156.1"}}
    assert lock["versions"]["tools"] == {"pytest": "8.4.1"}
    assert set(lock["references"]) == {"problem", "component", "scorer", "policy"}


def test_changing_a_component_file_changes_its_hash_and_the_arm_digest(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, experiment_document())
    before = _lock(path)

    (tmp_path / "components" / "AGENTS.md").write_text("Always write a plan.\n", encoding="utf-8")
    after = _lock(path)

    file_key = "components/AGENTS.md"
    assert before["files"][file_key]["sha256"] != after["files"][file_key]["sha256"]
    assert before["arms"]["guided"]["digest"] != after["arms"]["guided"]["digest"]
    assert before["arms"]["baseline"]["digest"] == after["arms"]["baseline"]["digest"]
    assert before["lock_sha256"] != after["lock_sha256"]


def test_directory_components_hash_every_file_but_ignore_os_noise(tmp_path: Path) -> None:
    skill = tmp_path / "components" / "skill"
    (skill / "__pycache__").mkdir(parents=True)
    (skill / "SKILL.md").write_text("skill\n", encoding="utf-8")
    (skill / "__pycache__" / "x.pyc").write_bytes(b"\0")
    (skill / ".DS_Store").write_bytes(b"\0")
    document = experiment_document()
    document["conditions"][1]["components"][0]["files"] = ["components/skill"]

    lock = _lock(write_experiment(tmp_path, document))

    assert list(lock["files"]) == ["components/skill/SKILL.md"]


def test_relock_ignores_its_own_output_inside_a_hashed_directory(tmp_path: Path) -> None:
    document = experiment_document()
    document["conditions"][1]["components"][0]["files"] = ["components"]
    path = write_experiment(tmp_path, document)
    lock_path = tmp_path / "components" / "experiment.lock.json"
    resolvers = ResolverSet([*fixture_resolvers(), PolicyResolver()], entry_point_group=None)

    def relock() -> bytes:
        rendered = render_lock(
            build_lock(load_experiment(path), lock_path=lock_path, resolvers=resolvers)
        )
        lock_path.write_bytes(rendered)
        return rendered

    assert relock() == relock()
    assert list(json.loads(lock_path.read_bytes())["files"]) == ["components/AGENTS.md"]


def test_files_reached_through_symlinks_outside_the_experiment_are_refused(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x\n", encoding="utf-8")
    root = tmp_path / "exp"
    (root / "components").mkdir(parents=True)
    (root / "components" / "link.txt").symlink_to(outside / "secret.txt")
    document = experiment_document()
    document["conditions"][1]["components"][0]["files"] = [
        "components/link.txt",
        "components",
    ]

    with pytest.raises(LockError) as caught:
        _lock(write_experiment(root, document))

    messages = {issue.loc: issue.message for issue in caught.value.issues}
    assert "outside the experiment directory" in messages["conditions.1.components.0.files.0"]
    assert "outside the experiment directory" in messages["conditions.1.components.0.files.1"]


def test_overrides_are_recorded_and_change_the_lock(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, experiment_document())

    lock = _lock(
        path, overrides=CliOverrides(flags={"blocks": 2}, assignments=("analysis.seed=1",))
    )

    assert lock["overrides"] == {"flags": {"blocks": 2}, "set": ["analysis.seed=1"]}
    assert (lock["spec"]["analysis"]["blocks"], lock["spec"]["analysis"]["seed"]) == (2, 1)
    assert lock["lock_sha256"] != _lock(path)["lock_sha256"]


@pytest.mark.parametrize(
    ("checkpoints", "expected"), [(1, "scb-canonical-oneshot@1"), (8, "scb-canonical@1")]
)
def test_headline_policy_defaults_by_checkpoint_count(
    tmp_path: Path, checkpoints: int, expected: str
) -> None:
    lock = _lock(write_experiment(tmp_path, experiment_document()), checkpoints=checkpoints)

    assert lock["headline_policy"] == {
        "ref": expected,
        "selection": "default_by_k",
        "checkpoints": [checkpoints],
    }
    assert expected in lock["references"]["policy"]


def test_declared_policy_must_support_every_problem_k(tmp_path: Path) -> None:
    document = experiment_document()
    document["assessment"]["headline_policy"] = "scb-canonical@1"
    path = write_experiment(tmp_path, document)

    with pytest.raises(LockError) as caught:
        _lock(path, checkpoints=1)

    assert [issue.loc for issue in caught.value.issues] == ["assessment.headline_policy"]


def test_local_task_problems_are_hashed_and_counted(tmp_path: Path) -> None:
    task = tmp_path / "tasks" / "forge"
    task.mkdir(parents=True)
    (task / "task.toml").write_text(
        '[[steps]]\nname = "checkpoint_1"\n[[steps]]\nname = "checkpoint_2"\n', encoding="utf-8"
    )
    single = tmp_path / "tasks" / "oneshot"
    single.mkdir()
    (single / "task.toml").write_text('[task]\nname = "one"\n', encoding="utf-8")
    document = experiment_document(
        problems=[{"path": "tasks/forge"}, {"path": "tasks/oneshot"}],
    )
    document["assessment"]["headline_policy"] = "scb-canonical@1"
    path = write_experiment(tmp_path, document)

    with pytest.raises(LockError) as caught:
        _lock(path)
    assert "K=[1]" in caught.value.issues[0].message

    document["problems"] = [{"path": "tasks/forge"}]
    lock = _lock(write_experiment(tmp_path, document))
    assert lock["problems"][0]["checkpoints"] == 2
    assert "tasks/forge/task.toml" in lock["files"]


def test_unusable_local_task_directories_are_reported(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    (tmp_path / "no-toml").mkdir()
    (tmp_path / "no-toml" / "README.md").write_text("x\n", encoding="utf-8")
    path = write_experiment(
        tmp_path, experiment_document(problems=[{"path": "empty"}, {"path": "no-toml"}])
    )

    with pytest.raises(LockError) as caught:
        _lock(path)

    messages = {issue.loc: issue.message for issue in caught.value.issues}
    assert "is empty" in messages["problems.0.path"]
    assert "unreadable task.toml" in messages["problems.1.path"]


def test_mixed_k_without_declared_policy_is_rejected(tmp_path: Path) -> None:
    for name, steps in (("a", 1), ("b", 3)):
        task = tmp_path / name
        task.mkdir()
        (task / "task.toml").write_text('[[steps]]\nname = "s"\n' * steps, encoding="utf-8")
    path = write_experiment(tmp_path, experiment_document(problems=[{"path": "a"}, {"path": "b"}]))

    with pytest.raises(LockError) as caught:
        _lock(path)

    assert "mixed checkpoint counts [1, 3]" in str(caught.value)


def test_every_gap_is_reported_and_nothing_is_skipped(tmp_path: Path) -> None:
    document = experiment_document()
    document["conditions"][1]["components"][0]["files"] = ["components/missing.md"]
    document["assessment"]["scorers"] = ["correctness@1", "jev@2"]
    path = write_experiment(tmp_path, document)
    resolvers = ResolverSet(
        [
            InMemoryResolver(RefKind.PROBLEM, {"scb/forge@abc123": {"checkpoints": 0}}),
            InMemoryResolver(RefKind.SCORER, {"correctness@1": {}}),
        ],
        entry_point_group=None,
    )

    with pytest.raises(LockError) as caught:
        build_lock(load_experiment(path), lock_path=path, resolvers=resolvers)

    assert {issue.loc for issue in caught.value.issues} == {
        "problems.0.ref",
        "assessment.scorers.1",
        "conditions.1.components.0.use",
        "conditions.1.components.0.files.0",
    }


def test_conflicting_tool_pins_are_rejected(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, experiment_document())
    problem, _, scorer = fixture_resolvers()
    component = InMemoryResolver(
        RefKind.COMPONENT, {"rules-file@1": {}}, tool_versions={"pytest": "7.0.0"}
    )
    resolvers = ResolverSet([problem, component, scorer, PolicyResolver()], entry_point_group=None)

    with pytest.raises(LockError, match="tool 'pytest' pinned to both 8.4.1 and 7.0.0"):
        build_lock(load_experiment(path), lock_path=path, resolvers=resolvers)


def test_resolver_set_rules() -> None:
    resolvers = ResolverSet([PolicyResolver()], entry_point_group=None)

    assert resolvers.available_kinds() == frozenset({RefKind.POLICY})
    assert resolvers.resolver_for(RefKind.SCORER) is None
    assert RefKind.POLICY in default_resolvers().available_kinds()
    with pytest.raises(RegistryReferenceError, match="duplicate resolver"):
        ResolverSet([PolicyResolver(), PolicyResolver()], entry_point_group=None)
    with pytest.raises(RegistryReferenceError, match="expected name@version"):
        RegistryRef.parse(RefKind.SCORER, "correctness")
    assert str(RegistryRef.parse(RefKind.PROBLEM, "scb/forge@38d627e")) == "scb/forge@38d627e"


def test_lock_command_writes_then_checks_and_reports_pending_resolvers(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, experiment_document())
    runner = CliRunner()

    missing = runner.invoke(main, ["lock", str(path)])

    assert missing.exit_code == 1
    # The problem resolver is registered (WS-B); this fixture problem is not imported anywhere.
    assert "problems.0.ref" in missing.output
    assert not (tmp_path / "experiment.lock.json").exists()


def test_lock_command_write_and_check_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_experiment(tmp_path, experiment_document())
    monkeypatch.setattr(
        "raidar.spec.lock.default_resolvers",
        lambda policies: ResolverSet(
            [*fixture_resolvers(), PolicyResolver(policies)], entry_point_group=None
        ),
    )
    runner = CliRunner()
    lock_path = tmp_path / "experiment.lock.json"

    written = runner.invoke(main, ["lock", str(path)])
    current = runner.invoke(main, ["lock", str(path), "--check"])
    (tmp_path / "components" / "AGENTS.md").write_text("changed\n", encoding="utf-8")
    stale = runner.invoke(main, ["lock", str(path), "--check"])

    assert written.exit_code == 0, written.output
    assert json.loads(lock_path.read_bytes())["experiment"]["id"] == "fixture"
    assert current.exit_code == 0, current.output
    assert "Lock is current" in current.output
    assert stale.exit_code == 1
    assert "out of date" in stale.output


class _EntryPoint:
    def __init__(self, name: str, factory: Any) -> None:
        self.name = name
        self.value = f"fixture:{name}"
        self._factory = factory

    def load(self) -> Any:
        return self._factory


def test_entry_point_resolvers_load_lazily_by_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded: list[str] = []

    def scorer_factory() -> InMemoryResolver:
        loaded.append("scorer")
        return InMemoryResolver(RefKind.SCORER, {"correctness@1": {}})

    monkeypatch.setattr(
        "raidar.spec.references.entry_points",
        lambda group: [_EntryPoint("scorer", scorer_factory)],
    )
    resolvers = ResolverSet()

    assert RefKind.SCORER in resolvers.available_kinds()
    assert loaded == []
    resolver = resolvers.resolver_for(RefKind.SCORER)
    assert resolver is resolvers.resolver_for(RefKind.SCORER)
    assert loaded == ["scorer"]


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        (["widget"], "must be a reference kind"),
        (["scorer", "scorer"], "multiple entry-point resolvers"),
        (["problem-as-scorer"], "reports kind"),
    ],
)
def test_entry_point_resolver_misconfiguration_is_rejected(
    monkeypatch: pytest.MonkeyPatch, entries: list[str], message: str
) -> None:
    def make(name: str) -> _EntryPoint:
        if name == "problem-as-scorer":
            return _EntryPoint("scorer", lambda: InMemoryResolver(RefKind.PROBLEM))
        return _EntryPoint(name, lambda: InMemoryResolver(RefKind.SCORER))

    monkeypatch.setattr(
        "raidar.spec.references.entry_points", lambda group: [make(name) for name in entries]
    )

    with pytest.raises(RegistryReferenceError, match=message):
        ResolverSet().resolver_for(RefKind.SCORER)
