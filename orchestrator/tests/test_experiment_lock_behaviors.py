"""``raidar lock``: content hashes, versions, determinism and gaps."""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from raidar_contract.canonical import digest_json

from raidar.cli import main
from raidar.contract.references import (
    RefKind,
    RegistryRef,
    RegistryReferenceError,
    ResolverSet,
)
from raidar.design.experiment.loader import CliOverrides, load_experiment
from raidar.design.experiment.lock import (
    LOCK_VERSION,
    SECTIONS,
    LockError,
    StaleLockError,
    build_lock,
    check_lock_file,
    default_resolvers,
    describe_staleness,
    harbor_version,
    lock_integrity_issues,
    render_lock,
    stale_sections,
    toolchain_versions,
)
from raidar.scoring.framework.metrics import MetricResolver
from raidar.subject.draft import draft_resolution, drafting, set_draft_resolution
from raidar.subject.releases import released_agent
from tests.experiment_spec_support import (
    InMemoryResolver,
    experiment_document,
    fixture_resolvers,
    pin_spec_digest,
    write_experiment,
)
from tests.experiment_spec_support import lock_bytes as _lock_bytes
from tests.experiment_spec_support import lock_document as _lock

PYTHON_TASK = '[metadata]\nraidar_runtime = "python"\n'
"""A local task declares its runtime (``RT1``)."""


@pytest.fixture(autouse=True)
def _fixed_spec_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    pin_spec_digest(monkeypatch)


def test_relocking_unchanged_inputs_is_byte_identical(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, experiment_document())

    first = _lock_bytes(path)
    second = _lock_bytes(path)

    assert first == second
    assert first.endswith(b"\n")
    lock = json.loads(first)
    assert list(lock) == sorted(lock)
    execution, assessment = lock["execution"], lock["assessment"]
    assert execution["versions"]["harbor"] == harbor_version()
    assert execution["versions"]["agents"] == {
        "luna": {
            "release": "codex@3",
            "version": released_agent("codex@3").definition.tool.version,
        }
    }
    assert set(execution["references"]) == {"problem", "agent", "workflow"}
    assert set(assessment["references"]) == {"scorer", "metric"}
    # Tools are recorded with the section whose reference pins them.
    assert (execution["tools"], assessment["tools"]) == ({}, {"pytest": "8.4.1"})
    assert set(lock["toolchain"]) == {"python", "litellm", "raidar", "raidar_spec"}
    assert lock["sections"] == {name: digest_json(lock[name]) for name in SECTIONS}
    assert lock["lock_sha256"] == lock["sections"]["execution"]
    assert lock_integrity_issues(lock) == []


def test_a_lock_records_package_and_primitive_identity_without_stage_code(tmp_path: Path) -> None:
    lock = _lock(write_experiment(tmp_path, experiment_document()))

    for arm in lock["execution"]["arms"].values():
        assert "stage_code" not in arm
        assert arm["packages"] == {}  # a spike workflow runs no packages
        assert arm["primitive_code"]
    assert "draft" not in lock


def test_a_draft_lock_is_for_smoke_experiments_only(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, experiment_document())
    resolvers = ResolverSet([*fixture_resolvers(), MetricResolver()], entry_point_group=None)

    with pytest.raises(LockError, match="a draft lock is for smoke experiments"):
        build_lock(
            load_experiment(path),
            lock_path=path.with_name("experiment.lock.json"),
            resolvers=resolvers,
            draft=True,
        )


def _scorer_resolvers(implementation: str) -> ResolverSet:
    problem, agent, workflow, _ = fixture_resolvers()
    scorer = InMemoryResolver(
        RefKind.SCORER,
        {"correctness@10": {"implementation_digest": implementation}},
        tool_versions={"pytest": "8.4.1"},
    )
    return ResolverSet([problem, agent, workflow, scorer, MetricResolver()], entry_point_group=None)


def test_assessment_and_analysis_changes_keep_the_run_identity(tmp_path: Path) -> None:
    """lock_sha256 covers execution inputs only: scorer or threshold edits mean rescore."""
    path = write_experiment(tmp_path, experiment_document())
    lock_path = path.with_name("experiment.lock.json")

    def locked(implementation: str, experiment: Path = path) -> dict[str, Any]:
        loaded = load_experiment(experiment)
        resolvers = _scorer_resolvers(implementation)
        return json.loads(render_lock(build_lock(loaded, lock_path=lock_path, resolvers=resolvers)))

    base = locked("a" * 64)
    rescored = locked("b" * 64)
    document = experiment_document()
    document["analysis"]["min_pairs"] = 7
    document["analysis"]["concurrency"] = 4
    reanalysed = locked("a" * 64, write_experiment(tmp_path, document))

    assert rescored["lock_sha256"] == base["lock_sha256"]
    assert stale_sections(base, rescored) == ("assessment",)
    assert reanalysed["lock_sha256"] == base["lock_sha256"]
    # concurrency is per machine: it is part of the document, not of any section.
    assert stale_sections(base, reanalysed) == ("analysis", "document")
    assert "rescore" in describe_staleness(("assessment",))


def test_the_lock_declares_outcome_types_from_the_bound_checkpoints(tmp_path: Path) -> None:
    document = experiment_document()
    path = write_experiment(tmp_path, document)
    lock_path = path.with_name("experiment.lock.json")
    loaded = load_experiment(path)
    lock = json.loads(
        render_lock(build_lock(loaded, lock_path=lock_path, resolvers=_scorer_resolvers("a" * 64)))
    )

    declared = lock["analysis"]["outcome_types"]
    assert declared["aggregate"] == {"binary": []}
    for entry, bound in zip(document["problems"], lock["execution"]["problems"], strict=True):
        types = declared["problems"][entry["ref"]]
        assert types["checkpoints"] == bound["checkpoints"]
        assert "final_solved" in types["binary"]
        assert ("solve_rate_solvable" in types["binary"]) == (bound["checkpoints"] == 1)
        assert "headline_score" not in types["binary"]


def test_overrides_are_recorded_and_change_the_lock(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, experiment_document())

    lock = _lock(
        path, overrides=CliOverrides(flags={"attempts": 2}, assignments=("analysis.seed=1",))
    )

    assert lock["overrides"] == {"flags": {"attempts": 2}, "set": ["analysis.seed=1"]}
    assert (lock["spec"]["analysis"]["attempts"], lock["spec"]["analysis"]["seed"]) == (2, 1)
    assert lock["lock_sha256"] != _lock(path)["lock_sha256"]


@pytest.mark.parametrize(
    ("checkpoints", "expected"), [(1, "scb-canonical-oneshot@1"), (8, "scb-canonical@1")]
)
def test_headline_policy_defaults_by_checkpoint_count(
    tmp_path: Path, checkpoints: int, expected: str
) -> None:
    lock = _lock(write_experiment(tmp_path, experiment_document()), checkpoints=checkpoints)

    assert lock["assessment"]["metric"] == {
        "ref": expected,
        "selection": "default_by_k",
        "checkpoints": [checkpoints],
    }
    assert expected in lock["assessment"]["references"]["metric"]


def test_declared_policy_must_support_every_problem_k(tmp_path: Path) -> None:
    document = experiment_document()
    document["assessment"]["metric"] = "scb-canonical@1"
    path = write_experiment(tmp_path, document)

    with pytest.raises(LockError) as caught:
        _lock(path, checkpoints=1)

    assert [issue.loc for issue in caught.value.issues] == ["assessment.metric"]


def test_local_task_problems_are_hashed_and_counted(tmp_path: Path) -> None:
    task = tmp_path / "tasks" / "forge"
    task.mkdir(parents=True)
    (task / "task.toml").write_text(
        f'{PYTHON_TASK}[[steps]]\nname = "checkpoint_1"\n[[steps]]\nname = "checkpoint_2"\n',
        encoding="utf-8",
    )
    single = tmp_path / "tasks" / "oneshot"
    single.mkdir()
    (single / "task.toml").write_text(f'{PYTHON_TASK}[task]\nname = "one"\n', encoding="utf-8")
    document = experiment_document(
        problems=[{"path": "tasks/forge"}, {"path": "tasks/oneshot"}],
    )
    document["assessment"]["metric"] = "scb-canonical@1"
    path = write_experiment(tmp_path, document)

    with pytest.raises(LockError) as caught:
        _lock(path)
    assert "K=[1]" in caught.value.issues[0].message

    document["problems"] = [{"path": "tasks/forge"}]
    lock = _lock(write_experiment(tmp_path, document))
    assert lock["execution"]["problems"][0]["checkpoints"] == 2
    assert "tasks/forge/task.toml" in lock["execution"]["files"]


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
        (task / "task.toml").write_text(
            PYTHON_TASK + '[[steps]]\nname = "s"\n' * steps, encoding="utf-8"
        )
    path = write_experiment(tmp_path, experiment_document(problems=[{"path": "a"}, {"path": "b"}]))

    with pytest.raises(LockError) as caught:
        _lock(path)

    assert "mixed checkpoint counts [1, 3]" in str(caught.value)


def test_every_gap_is_reported_and_nothing_is_skipped(tmp_path: Path) -> None:
    document = experiment_document()
    document["assessment"]["scorers"] = ["correctness@10", "jev@2"]
    path = write_experiment(tmp_path, document)
    _, agent, workflow, _ = fixture_resolvers()
    resolvers = ResolverSet(
        [
            InMemoryResolver(RefKind.PROBLEM, {"scb/forge@abc123": {"checkpoints": 0}}),
            agent,
            workflow,
            InMemoryResolver(RefKind.SCORER, {"correctness@10": {}}),
        ],
        entry_point_group=None,
    )

    with pytest.raises(LockError) as caught:
        build_lock(load_experiment(path), lock_path=path, resolvers=resolvers)

    assert {issue.loc for issue in caught.value.issues} == {
        "problems.0.ref",
        "assessment.scorers.1",
    }


def test_conflicting_tool_pins_are_rejected(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, experiment_document())
    problem, agent, _, scorer = fixture_resolvers()
    workflow = InMemoryResolver(
        RefKind.WORKFLOW, {"raidar/one-shot@2": {}}, tool_versions={"pytest": "7.0.0"}
    )
    resolvers = ResolverSet(
        [problem, agent, workflow, scorer, MetricResolver()], entry_point_group=None
    )

    with pytest.raises(LockError, match="tool 'pytest' pinned to both 8.4.1 and 7.0.0"):
        build_lock(load_experiment(path), lock_path=path, resolvers=resolvers)


def test_resolver_set_rules() -> None:
    resolvers = ResolverSet([MetricResolver()], entry_point_group=None)

    assert resolvers.available_kinds() == frozenset({RefKind.METRIC})
    assert resolvers.resolver_for(RefKind.SCORER) is None
    assert RefKind.METRIC in default_resolvers().available_kinds()
    with pytest.raises(RegistryReferenceError, match="duplicate resolver"):
        ResolverSet([MetricResolver(), MetricResolver()], entry_point_group=None)
    with pytest.raises(RegistryReferenceError, match="expected name@version"):
        RegistryRef.parse(RefKind.SCORER, "correctness")
    assert str(RegistryRef.parse(RefKind.PROBLEM, "scb/forge@38d627e")) == "scb/forge@38d627e"


def test_lock_command_writes_then_checks_and_reports_pending_resolvers(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, experiment_document())
    runner = CliRunner()

    missing = runner.invoke(main, ["lock", str(path)])

    assert missing.exit_code == 1
    # The problem resolver is registered; this fixture problem is not imported anywhere.
    assert "problems.0.ref" in missing.output
    assert not (tmp_path / "experiment.lock.json").exists()


def test_lock_command_write_and_check_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = experiment_document()
    analysis = document["analysis"]
    path = write_experiment(tmp_path, document)
    monkeypatch.setattr(
        "raidar.design.experiment.lock.default_resolvers",
        lambda policies: ResolverSet(
            [*fixture_resolvers(), MetricResolver(policies)], entry_point_group=None
        ),
    )
    runner = CliRunner()
    lock_path = tmp_path / "experiment.lock.json"

    written = runner.invoke(main, ["lock", str(path)])
    current = runner.invoke(main, ["lock", str(path), "--check"])
    with monkeypatch.context() as host:
        host.setattr("platform.python_version", lambda: "3.12.0")
        drifted = runner.invoke(main, ["lock", str(path), "--check"])
        tolerated = runner.invoke(main, ["lock", str(path), "--check", "--ignore-host-python"])
    write_experiment(tmp_path, experiment_document(analysis={**analysis, "attempts": 6}))
    stale = runner.invoke(main, ["lock", str(path), "--check"])
    lock_path.write_text(lock_path.read_text().replace('"harbor"', '"harbour"'))
    tampered = runner.invoke(main, ["lock", str(path), "--check"])

    assert written.exit_code == 0, written.output
    assert json.loads(lock_path.read_bytes())["experiment"]["id"] == "python__fixture__test"
    assert current.exit_code == 0, current.output
    assert "Lock is current" in current.output
    assert drifted.exit_code == 1
    assert "Stale: toolchain:" in drifted.output
    assert tolerated.exit_code == 0, tolerated.output
    assert "host Python 3.12.0 differs" in tolerated.output
    assert stale.exit_code == 1
    assert "out of date" in stale.output
    assert "execution: execution inputs changed" in stale.output
    assert "assessment:" not in stale.output
    assert tampered.exit_code == 1
    assert "section execution does not match its digest" in tampered.output


def test_draft_locks_keep_distinct_smoke_experiments_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIDAR_VAR", str(tmp_path / "var"))
    monkeypatch.setattr(
        "raidar.design.experiment.lock.default_resolvers",
        lambda policies: ResolverSet(
            [*fixture_resolvers(), MetricResolver(policies)], entry_point_group=None
        ),
    )
    ids = ("python__fixture__first", "python__fixture__second")
    for identity in ids:
        document = experiment_document()
        document.update(kind="smoke", id=identity)
        path = write_experiment(tmp_path / identity, document)
        result = CliRunner().invoke(main, ["lock", str(path), "--draft"])
        assert result.exit_code == 0, result.output
        assert not path.with_name("experiment.lock.json").exists()
    for identity in ids:
        lock_path = tmp_path / "var" / "tmp" / "drafts" / identity / "experiment.lock.json"
        lock = json.loads(lock_path.read_bytes())
        assert lock["experiment"]["id"] == identity
        assert lock["draft"] is True


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
        return InMemoryResolver(RefKind.SCORER, {"correctness@10": {}})

    monkeypatch.setattr(
        "raidar.contract.references.entry_points",
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
        "raidar.contract.references.entry_points",
        lambda group: [make(name) for name in entries],
    )

    with pytest.raises(RegistryReferenceError, match=message):
        ResolverSet().resolver_for(RefKind.SCORER)


def _written_lock(root: Path) -> tuple[Path, dict[str, Any]]:
    """A current lock on disk, and its parsed document."""
    path = write_experiment(root, experiment_document())
    data = _lock_bytes(path)
    lock_path = root / "experiment.lock.json"
    lock_path.write_bytes(data)
    return lock_path, json.loads(data)


def test_the_lock_file_check_accepts_exactly_the_current_lock(tmp_path: Path) -> None:
    lock_path, current = _written_lock(tmp_path)

    assert check_lock_file(lock_path, current) is None


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (None, "does not exist"),
        (b"{not json", "not readable JSON"),
        (b"[1]", "not a JSON object"),
        (json.dumps({"lock_version": LOCK_VERSION - 1}).encode(), "relock"),
        (json.dumps({"lock_version": LOCK_VERSION}).encode(), "missing section"),
    ],
)
def test_the_lock_file_check_refuses_a_missing_or_unusable_file(
    tmp_path: Path, content: bytes | None, message: str
) -> None:
    lock_path, current = _written_lock(tmp_path)
    if content is None:
        lock_path.unlink()
    else:
        lock_path.write_bytes(content)

    with pytest.raises(StaleLockError, match=message):
        check_lock_file(lock_path, current)


def test_the_lock_file_check_refuses_a_lock_that_is_not_in_canonical_form(tmp_path: Path) -> None:
    lock_path, current = _written_lock(tmp_path)
    lock_path.write_text(json.dumps(current, indent=4), encoding="utf-8")

    with pytest.raises(StaleLockError, match="not in canonical form"):
        check_lock_file(lock_path, current)


def test_the_lock_file_check_names_what_a_changed_input_makes_stale(tmp_path: Path) -> None:
    lock_path, _ = _written_lock(tmp_path)
    changed = experiment_document()
    changed["analysis"]["attempts"] = 6
    current = _lock(write_experiment(tmp_path / "changed", changed))

    with pytest.raises(StaleLockError, match="out of date") as caught:
        check_lock_file(lock_path, current)

    assert set(caught.value.stale) >= {"execution"}
    assert describe_staleness(caught.value.stale) in str(caught.value)


def test_a_package_that_is_not_installed_is_recorded_as_such(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def absent(package: str) -> str:
        raise PackageNotFoundError(package)

    monkeypatch.setattr("raidar.design.experiment.lock.version", absent)

    assert harbor_version() == "not-installed"
    assert toolchain_versions()["litellm"] == "not-installed"


def test_local_task_paths_that_are_missing_or_escape_the_experiment_are_reported(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "task.toml").write_text(PYTHON_TASK, encoding="utf-8")
    root = tmp_path / "exp"
    (root / "linked-file").mkdir(parents=True)
    (root / "linked").symlink_to(outside, target_is_directory=True)
    (root / "linked-file" / "task.toml").symlink_to(outside / "task.toml")
    problems = [{"path": "gone"}, {"path": "linked"}, {"path": "linked-file"}]
    path = write_experiment(root, experiment_document(problems=problems))

    with pytest.raises(LockError) as caught:
        _lock(path)

    messages = {issue.loc: issue.message for issue in caught.value.issues}
    assert "does not exist" in messages["problems.0.path"]
    assert "outside the experiment directory" in messages["problems.1.path"]
    assert "outside the experiment directory" in messages["problems.2.path"]


def _lock_with_problem(path: Path, metadata: dict[str, object]) -> dict[str, Any]:
    _, agent, workflow, scorer = fixture_resolvers()
    problem = InMemoryResolver(RefKind.PROBLEM, {"scb/forge@abc123": metadata})
    resolvers = ResolverSet(
        [problem, agent, workflow, scorer, MetricResolver()], entry_point_group=None
    )
    lock_path = path.with_name("experiment.lock.json")
    return build_lock(load_experiment(path), lock_path=lock_path, resolvers=resolvers)


def test_a_checkpoint_selection_binds_the_first_checkpoints_of_a_multi_checkpoint_problem(
    tmp_path: Path,
) -> None:
    selecting = experiment_document(problems=[{"ref": "scb/forge@abc123", "checkpoints": 1}])
    path = write_experiment(tmp_path, selecting)

    lock = _lock_with_problem(path, {"checkpoints": 8})

    assert lock["execution"]["problems"][0]["checkpoints"] == 1


@pytest.mark.parametrize(
    "metadata",
    [{"checkpoints": 1}, {"checkpoints": 8, "mode": "one_shot"}],
    ids=["fewer", "oneshot"],
)
def test_a_checkpoint_selection_the_problem_cannot_satisfy_is_refused(
    tmp_path: Path, metadata: dict[str, object]
) -> None:
    selecting = experiment_document(problems=[{"ref": "scb/forge@abc123", "checkpoints": 2}])
    path = write_experiment(tmp_path, selecting)

    with pytest.raises(LockError) as caught:
        _lock_with_problem(path, metadata)

    issue = next(i for i in caught.value.issues if i.loc == "problems.0.checkpoints")
    assert "cannot take the first 2 checkpoint(s)" in issue.message


def test_a_reference_kind_without_a_resolver_is_reported_at_its_field(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, experiment_document())
    problem, agent, workflow, _ = fixture_resolvers()
    resolvers = ResolverSet([problem, agent, workflow, MetricResolver()], entry_point_group=None)

    with pytest.raises(LockError) as caught:
        build_lock(load_experiment(path), lock_path=path, resolvers=resolvers)

    messages = {issue.loc: issue.message for issue in caught.value.issues}
    assert "no resolver registered for scorer" in messages["assessment.scorers.0"]


def test_a_lock_inside_a_referenced_task_directory_is_not_hashed_into_itself(
    tmp_path: Path,
) -> None:
    task = tmp_path / "tasks" / "forge"
    task.mkdir(parents=True)
    (task / "task.toml").write_text(f'{PYTHON_TASK}[[steps]]\nname = "s"\n', encoding="utf-8")
    lock_file = task / "experiment.lock.json"
    lock_file.write_text("stale\n", encoding="utf-8")
    path = write_experiment(tmp_path, experiment_document(problems=[{"path": "tasks/forge"}]))
    resolvers = ResolverSet([*fixture_resolvers(), MetricResolver()], entry_point_group=None)

    lock = build_lock(load_experiment(path), lock_path=lock_file, resolvers=resolvers)

    assert "tasks/forge/task.toml" in lock["execution"]["files"]
    assert "tasks/forge/experiment.lock.json" not in lock["execution"]["files"]


def test_process_draft_resolution_is_overridden_only_for_the_nested_context() -> None:
    with draft_resolution(False):
        set_draft_resolution(True)
        assert drafting()
        with draft_resolution(False):
            assert not drafting()
        assert drafting()
        set_draft_resolution(False)
        assert not drafting()
