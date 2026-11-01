"""Plan construction: lock currency, selection and problem pins, planned trials, plan document."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import click
import pytest
from raidar_contract.canonical import digest_json

from raidar.cli import plan_build
from raidar.design.experiment.lock import LockError, StaleLockError
from raidar.design.experiment.schema import FieldIssue
from raidar.design.problem.models import ProblemId
from raidar.design.problem.store import ProblemStoreError
from raidar.engine.trial import plan as plan_module
from raidar.engine.trial.plan import (
    ExperimentPlan,
    PlanError,
    PlanOptions,
    ProblemTarget,
    _check_arm_identities,
    _pinned_grader,
    _problem_target,
    _problems,
    build_plan,
)
from raidar.subject.environment.drift import LockedPackError

REF = "scb/forge@38d627ecf668"
GRADER = {"image": "grader:1", "environment_id": "env-1", "implementation_sha256": "impl-1"}


# --- the lock on disk (cli.plan_build) ---------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "draft"),
    [
        (json.dumps({"draft": True}), True),
        (json.dumps({"draft": False}), False),
        (json.dumps({"lock_sha256": "x"}), False),
        ("{not json", False),
        (json.dumps(["a", "list"]), False),
        (None, False),
    ],
    ids=["draft", "not-draft", "no-flag", "corrupt", "not-an-object", "missing"],
)
def test_only_a_lock_flagged_draft_is_a_smoke_draft(
    tmp_path: Path, content: str | None, draft: bool
) -> None:
    lock = tmp_path / "e.lock.json"
    if content is not None:
        lock.write_text(content)

    assert plan_build._is_draft(lock) is draft


@pytest.fixture
def lock_world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, planned_inputs: None) -> Any:
    """Relocking reproduces the current experiment; a lock on disk is current only if equal.

    ``build_lock`` yields the experiment's lock (flagged draft when asked to relock as a
    draft); ``check_lock_file`` accepts a file holding exactly that lock.
    """
    loaded = _loaded(kind="smoke", path=tmp_path / "loaded.yaml")
    world = SimpleNamespace(
        loaded=loaded, note=None, build_error=None, drafts=[], store="store", pack_error=None
    )

    def build(loaded_arg: Any, *, lock_path: Path, draft: bool) -> dict[str, Any]:
        if world.build_error is not None:
            raise world.build_error
        return _full_lock(loaded_arg, draft=draft)

    def check(lock_path: Path, current: Any, *, ignore_host_python: bool) -> str | None:
        if not lock_path.is_file():
            raise StaleLockError(f"{lock_path.name} does not exist; run `raidar lock` first")
        if json.loads(lock_path.read_bytes()) != current:
            raise StaleLockError(f"{lock_path.name} is stale; relock")
        return world.note

    def runtime_pack(lock: Any) -> str:
        if world.pack_error is not None:
            raise world.pack_error
        return "pack"

    monkeypatch.setattr(plan_build, "build_lock", build)
    monkeypatch.setattr(plan_build, "check_lock_file", check)
    monkeypatch.setattr(plan_build, "render_lock", lambda lock: json.dumps(lock).encode())
    monkeypatch.setattr(plan_build, "set_draft_resolution", world.drafts.append)
    monkeypatch.setattr(plan_build, "load_cli_experiment", lambda *args: loaded)
    monkeypatch.setattr(plan_build, "lock_runtime_pack", runtime_pack)
    monkeypatch.setattr(plan_build, "open_store", lambda: world.store)
    return world


def _write_lock(path: Path, lock: dict[str, Any]) -> Path:
    path.write_text(json.dumps(lock))
    return path


def test_a_lock_on_disk_that_relocks_identically_is_the_lock_the_plan_uses(
    tmp_path: Path, lock_world: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write_lock(tmp_path / "e.lock.json", _full_lock(lock_world.loaded))

    lock = plan_build._current_lock(lock_world.loaded, path)

    assert lock == _full_lock(lock_world.loaded)
    assert capsys.readouterr().err == ""


def test_a_lock_that_no_longer_relocks_to_the_same_bytes_is_refused(
    tmp_path: Path, lock_world: Any
) -> None:
    stale = {**_full_lock(lock_world.loaded), "lock_sha256": "OLD"}
    path = _write_lock(tmp_path / "e.lock.json", stale)

    with pytest.raises(click.ClickException, match="e.lock.json is stale; relock"):
        plan_build._current_lock(lock_world.loaded, path)
    with pytest.raises(click.ClickException, match="does not exist"):
        plan_build._current_lock(lock_world.loaded, tmp_path / "absent.lock.json")


def test_a_lock_is_current_as_a_draft_only_when_it_says_it_is_one(
    tmp_path: Path, lock_world: Any
) -> None:
    loaded = lock_world.loaded
    draft = _write_lock(tmp_path / "draft.lock.json", _full_lock(loaded, draft=True))
    plain = _write_lock(tmp_path / "plain.lock.json", _full_lock(loaded, draft=False))
    # Same body, but the draft flag was dropped: relocked as a non-draft it no longer matches.
    dropped = _write_lock(
        tmp_path / "dropped.lock.json",
        {k: v for k, v in _full_lock(loaded, draft=True).items() if k != "draft"},
    )

    assert plan_build._current_lock(loaded, draft)["draft"] is True
    assert plan_build._current_lock(loaded, plain)["draft"] is False
    with pytest.raises(click.ClickException, match="dropped.lock.json is stale"):
        plan_build._current_lock(loaded, dropped)


def test_an_ignored_host_difference_is_noted_on_stderr_but_does_not_refuse(
    tmp_path: Path, lock_world: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lock_world.note = "host python 3.12.9 differs from the locked 3.12.8"
    path = _write_lock(tmp_path / "e.lock.json", _full_lock(lock_world.loaded))

    assert plan_build._current_lock(lock_world.loaded, path) == _full_lock(lock_world.loaded)

    captured = capsys.readouterr()
    assert captured.err == "note: host python 3.12.9 differs from the locked 3.12.8\n"
    assert captured.out == ""


def test_an_experiment_that_cannot_be_locked_refuses_the_plan(
    tmp_path: Path, lock_world: Any
) -> None:
    lock_world.build_error = LockError("experiment.yaml", [FieldIssue("arms", "unresolved agent")])

    with pytest.raises(click.ClickException, match="arms: unresolved agent"):
        plan_build._current_lock(lock_world.loaded, tmp_path / "e.lock.json")


# --- building the CLI plan ---------------------------------------------------------------------


def _options(tmp_path: Path, lock_path: Path | None = None, **overrides: Any) -> dict[str, Any]:
    return {
        "experiment": tmp_path / "e.yaml",
        "attempts": None,
        "seed": None,
        "assignments": (),
        "lock_path": lock_path,
        "arms": (),
        "attempts_selected": (),
        "max_checkpoints": None,
        "agent_override": None,
        "concurrency": None,
        **overrides,
    }


def test_the_cli_plan_selects_truncates_and_bounds_what_the_user_asked_for(
    tmp_path: Path, lock_world: Any
) -> None:
    _write_lock(tmp_path / "loaded.lock.json", _full_lock(lock_world.loaded))
    options = _options(
        tmp_path, arms=("a",), attempts_selected=(1,), max_checkpoints=2, concurrency=2
    )

    plan, store = plan_build.build_cli_plan(options)

    assert store == "store"
    assert plan.selected == {"b01-p0-a"}
    assert {t.steps for t in plan.trials} == {("s1", "s2")}
    assert len(plan.trials) == 4, "the selection narrows what runs, not what is planned"
    assert plan.concurrency == 2
    assert plan.lock_sha256 == "LOCK"
    assert lock_world.drafts == [False]


def test_an_explicit_draft_lock_file_replaces_the_default_lock_and_plans_from_the_tree(
    tmp_path: Path, lock_world: Any
) -> None:
    custom = _write_lock(tmp_path / "custom.lock.json", _full_lock(lock_world.loaded, draft=True))

    plan, _ = plan_build.build_cli_plan(_options(tmp_path, custom))

    assert plan.lock["draft"] is True
    assert lock_world.drafts == [True]


def test_the_default_lock_must_sit_next_to_the_experiment(tmp_path: Path, lock_world: Any) -> None:
    with pytest.raises(click.ClickException, match="loaded.lock.json does not exist"):
        plan_build.build_cli_plan(_options(tmp_path))


def test_a_selection_the_experiment_cannot_honour_is_a_command_error(
    tmp_path: Path, lock_world: Any
) -> None:
    _write_lock(tmp_path / "loaded.lock.json", _full_lock(lock_world.loaded))

    with pytest.raises(click.ClickException, match="unknown arms"):
        plan_build.build_cli_plan(_options(tmp_path, arms=("nope",)))
    with pytest.raises(click.ClickException, match=r"attempts \[5\]"):
        plan_build.build_cli_plan(_options(tmp_path, attempts_selected=(5,)))


def test_a_drifted_runtime_pack_refuses_the_plan(tmp_path: Path, lock_world: Any) -> None:
    _write_lock(tmp_path / "loaded.lock.json", _full_lock(lock_world.loaded))
    lock_world.pack_error = LockedPackError("pack image drifted")

    with pytest.raises(click.ClickException, match="pack image drifted"):
        plan_build.build_cli_plan(_options(tmp_path))


def test_an_unopenable_problem_store_refuses_the_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lock_world: Any
) -> None:
    _write_lock(tmp_path / "loaded.lock.json", _full_lock(lock_world.loaded))

    def no_store() -> None:
        raise ProblemStoreError("no problem store at /nowhere")

    monkeypatch.setattr(plan_build, "open_store", no_store)

    with pytest.raises(click.ClickException, match="no problem store at /nowhere"):
        plan_build.build_cli_plan(_options(tmp_path))


# --- the planner's problem pins ----------------------------------------------------------------


def _manifest(digest: str = "d1", steps: tuple[str, ...] = ("s1", "s2", "s3")) -> Any:
    return SimpleNamespace(digest=lambda: digest, steps=[SimpleNamespace(name=n) for n in steps])


class _Store:
    def __init__(self, manifest: Any, errata: Any = None, error: Exception | None = None) -> None:
        self.manifest, self.errata, self.error = manifest, errata, error
        self.errata_asked: list[Any] = []

    def load_manifest(self, problem: Any) -> Any:
        return self.manifest

    def pinned_errata(self, problem: Any, manifest: Any, pinned: Any) -> Any:
        self.errata_asked.append(pinned)
        if self.error is not None:
            raise self.error
        return self.errata


def _loaded(
    ref: str | None = REF,
    *,
    checkpoints: int | None = None,
    progression: str = "continue",
    kind: str = "ab",
    arms: tuple[str, ...] = ("a", "b"),
    attempts: int = 2,
    problems: int = 1,
    path: Path = Path("experiment.yaml"),
) -> Any:
    spec = SimpleNamespace(
        id="exp-1",
        kind=kind,
        problems=[SimpleNamespace(ref=ref, checkpoints=checkpoints)] * problems,
        protocol=SimpleNamespace(
            progression=progression,
            min_reward=0.5,
            model_dump=lambda mode: {"progression": progression},
        ),
        arms=[SimpleNamespace(id=arm) for arm in arms],
        analysis=SimpleNamespace(attempts=attempts, seed=7, concurrency=4),
        to_json=lambda: {"id": "exp-1", "arms": list(arms)},
    )
    return SimpleNamespace(spec=spec, path=path)


def _full_lock(loaded: Any, *, draft: bool = False) -> dict[str, Any]:
    lock = _lock()
    lock["experiment"] = {"spec_sha256": digest_json(loaded.spec.to_json())}
    lock["draft"] = draft
    return lock


def _lock(metadata: dict[str, Any] | None = None, *, checkpoints: int = 3) -> dict[str, Any]:
    meta = {"validation": {"grader": GRADER}, "solvable": {"sha256": "sv"}, "errata": {"e": 1}}
    return {
        "lock_sha256": "LOCK",
        "execution": {
            "problems": [{"digest": "d1", "checkpoints": checkpoints}],
            "references": {"problem": {REF: {"metadata": meta if metadata is None else metadata}}},
            "arms": {"a": {"digest": "A"}, "b": {"digest": "B"}},
        },
    }


@pytest.fixture(autouse=True)
def this_checkout_grades_with_impl_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_module, "grading_execution_digest", lambda: "impl-1")


def test_a_locked_problem_resolves_to_its_pinned_steps_errata_solvable_sets_and_grader() -> None:
    store = _Store(_manifest(), errata={"sha256": "er"})

    target = _problem_target(_loaded(), _lock(), store, 0, _lock()["execution"]["problems"])

    assert target == ProblemTarget(
        0,
        target.ref,
        "d1",
        ("s1", "s2", "s3"),
        {"sha256": "er"},
        {"sha256": "sv"},
        GRADER,
    )
    assert str(target.ref) == REF
    assert store.errata_asked == [{"e": 1}]


def test_a_checkpoint_binding_selects_the_first_steps_the_lock_pinned() -> None:
    lock = _lock(checkpoints=2)

    target = _problem_target(
        _loaded(checkpoints=2), lock, _Store(_manifest()), 0, lock["execution"]["problems"]
    )

    assert target.steps == ("s1", "s2")
    assert target.run_steps(1) == ("s1",)
    assert target.run_steps(None) == ("s1", "s2")


def test_a_local_task_path_cannot_be_planned() -> None:
    with pytest.raises(PlanError, match="problems.0: a local task path has no host-only verifier"):
        _problem_target(_loaded(None), _lock(), _Store(_manifest()), 0, [])


def test_a_problem_that_changed_since_the_lock_is_refused() -> None:
    store = _Store(_manifest(digest="d2"))

    with pytest.raises(PlanError, match=rf"problems.0: {REF} changed since the lock; relock"):
        _problem_target(_loaded(), _lock(), store, 0, _lock()["execution"]["problems"])


def test_a_binding_that_selects_other_checkpoints_than_the_lock_pinned_is_refused() -> None:
    store = _Store(_manifest())
    lock = _lock(checkpoints=2)

    with pytest.raises(PlanError, match=r"selects 3 checkpoint\(s\) but the lock pins 2"):
        _problem_target(_loaded(), lock, store, 0, lock["execution"]["problems"])


def test_errata_the_store_cannot_pin_refuse_the_plan_naming_the_problem() -> None:
    store = _Store(_manifest(), error=ProblemStoreError("errata file changed"))

    with pytest.raises(PlanError, match="problems.0: errata file changed"):
        _problem_target(_loaded(), _lock(), store, 0, _lock()["execution"]["problems"])


def test_a_halting_protocol_refuses_a_problem_with_no_solvable_sets() -> None:
    lock = _lock({"validation": {"grader": GRADER}})

    with pytest.raises(PlanError, match="pins no solvable sets"):
        _problem_target(
            _loaded(progression="halt_on_failure"),
            lock,
            _Store(_manifest()),
            0,
            lock["execution"]["problems"],
        )


@pytest.mark.parametrize(
    "validation",
    [
        {},
        {"grader": "not-a-mapping"},
        {"grader": {"image": "grader:1", "environment_id": "env-1"}},
        {"grader": {**GRADER, "environment_id": 7}},
    ],
    ids=["unvalidated", "not-a-mapping", "no-implementation", "not-a-string"],
)
def test_a_problem_without_a_complete_grader_pin_is_refused(validation: dict[str, Any]) -> None:
    references = {REF: {"metadata": {"validation": validation}}}

    with pytest.raises(PlanError, match="pins no complete grader identity.*re-validate"):
        _pinned_grader(0, REF, references)


def test_a_grader_validated_under_another_implementation_is_refused() -> None:
    other = {**GRADER, "implementation_sha256": "impl-0"}
    references = {REF: {"metadata": {"validation": {"grader": other}}}}

    with pytest.raises(PlanError, match="validated under grading implementation impl-0, this"):
        _pinned_grader(2, REF, references)


def test_every_locked_problem_is_resolved_in_order() -> None:
    lock = _lock()
    lock["execution"]["problems"] *= 2

    targets = _problems(_loaded(problems=2), lock, _Store(_manifest()))

    assert [t.index for t in targets] == [0, 1]


def test_a_lock_without_problems_cannot_be_planned() -> None:
    with pytest.raises(PlanError, match="lock has no execution.problems"):
        _problems(_loaded(), {"execution": {}}, _Store(_manifest()))


def test_an_arm_whose_identity_drifted_from_the_lock_is_named() -> None:
    def blueprint(digest: str) -> Any:
        return SimpleNamespace(arm=SimpleNamespace(identity=lambda: {"digest": digest}))

    lock = _lock()
    _check_arm_identities(lock, {"a": blueprint("A"), "b": blueprint("B")})

    with pytest.raises(PlanError, match=r"arm\(s\) \['b'\] no longer resolve to the identity"):
        _check_arm_identities(lock, {"a": blueprint("A"), "b": blueprint("changed")})


# --- building the plan -------------------------------------------------------------------------


def _blueprints() -> dict[str, Any]:
    def blueprint(arm_id: str, digest: str) -> Any:
        arm = SimpleNamespace(
            identity=lambda: {"digest": digest},
            workflow=SimpleNamespace(digest=f"wf-{arm_id}", ref=f"wf-{arm_id}@1"),
            packages={"pkg": arm_id},
            primitive_code={"code": arm_id},
            agent=SimpleNamespace(
                model="canon-model",
                reasoning="high",
                released=SimpleNamespace(
                    ref=f"acme/{arm_id}@1",
                    definition=SimpleNamespace(
                        capabilities=SimpleNamespace(model_dump=lambda: {"tools": True})
                    ),
                ),
            ),
        )
        config = SimpleNamespace(name=None, import_path="pkg:Agent", model_name="model-x")
        return SimpleNamespace(arm_id=arm_id, arm=arm, agent_config=config)

    return {"a": blueprint("a", "A"), "b": blueprint("b", "B")}


@pytest.fixture
def planned_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    """One fixed problem of three steps and two arms, resolved without a store or pack."""
    target = ProblemTarget(0, ProblemId.parse(REF), "d1", ("s1", "s2", "s3"))
    monkeypatch.setattr(plan_module, "_problems", lambda loaded, lock, store: (target,))
    monkeypatch.setattr(
        plan_module, "arm_blueprints", lambda loaded, pack, agent_override: _blueprints()
    )
    monkeypatch.setattr(
        plan_module,
        "agent_digest",
        lambda arm, config, override: f"agent-{override}-{arm.agent.released.ref}",
    )


@pytest.fixture
def planner(planned_inputs: None) -> Any:
    def build(loaded: Any | None = None, lock: Any | None = None, **options: Any) -> ExperimentPlan:
        loaded = loaded or _loaded()
        lock = lock or _lock()
        lock["experiment"] = {"spec_sha256": digest_json(loaded.spec.to_json())}
        return build_plan(loaded, lock, store=None, pack=None, options=PlanOptions(**options))

    return build


def test_a_plan_lists_one_trial_per_attempt_arm_and_problem_in_seeded_order(planner: Any) -> None:
    plan = planner()

    assert len(plan.trials) == 4
    assert [t.sequence for t in plan.trials] == [0, 1, 2, 3]
    assert {t.trial_id for t in plan.trials} == {
        f"b{attempt:02d}-p0-{arm}" for attempt in (0, 1) for arm in "ab"
    }
    for attempt in (0, 1):
        ordered = [t for t in plan.trials if t.attempt == attempt]
        assert [t.order for t in ordered] == [0, 1]
        assert [t.arm for t in ordered] == plan_module.arm_order(7, attempt, 0, ["a", "b"])
    assert plan.selected == {t.trial_id for t in plan.trials}
    assert plan.selected_trials() == list(plan.trials)
    assert plan.lock_sha256 == "LOCK"
    assert plan.concurrency == 4, "the experiment's default"
    assert plan.experiment_id == "exp-1"
    assert (plan.rule.mode, plan.rule.threshold) == ("continue", None)


def test_trial_hashes_separate_attempts_and_arms_but_not_machine_choices(planner: Any) -> None:
    base = planner()
    widened = planner(concurrency=2, arms=("a",))

    by_id = {t.trial_id: t.hashes for t in base.trials}
    assert len({h.trial for h in by_id.values()}) == 4
    assert by_id["b00-p0-a"].lock == "LOCK"
    assert by_id["b00-p0-a"].arm == "A"
    assert by_id["b00-p0-a"].workflow == "wf-a"
    assert by_id["b00-p0-a"].problem == "d1"
    assert by_id["b00-p0-a"].packages == digest_json({"pkg": "a"})
    assert by_id["b00-p0-a"].primitive_code == digest_json({"code": "a"})
    assert {t.trial_id: t.hashes for t in widened.trials} == by_id
    assert widened.concurrency == 2


def test_selection_changes_what_runs_and_truncation_changes_the_protocol_identity(
    planner: Any,
) -> None:
    full = planner()
    narrowed = planner(arms=("a",), attempts=(1,))
    truncated = planner(loaded=_loaded(kind="smoke"), max_checkpoints=2)

    assert narrowed.selected == {"b01-p0-a"}
    assert len(narrowed.trials) == len(full.trials)
    assert {t.steps for t in full.trials} == {("s1", "s2", "s3")}
    assert {t.steps for t in truncated.trials} == {("s1", "s2")}
    assert truncated.trials[0].hashes.protocol != full.trials[0].hashes.protocol
    assert truncated.trials[0].hashes.trial != full.trials[0].hashes.trial


def test_an_agent_override_changes_each_trials_agent_identity(planner: Any) -> None:
    overridden = planner(agent_override="nop").trials[0].hashes.agent
    assert overridden != planner().trials[0].hashes.agent


def test_a_lock_built_from_another_experiment_spec_is_refused(planner: Any) -> None:
    loaded = _loaded()
    lock = _lock()
    lock["experiment"] = {"spec_sha256": "stale"}

    with pytest.raises(PlanError, match="built from a different experiment spec; relock"):
        build_plan(loaded, lock, store=None, pack=None)


def test_a_selection_naming_an_unknown_arm_is_refused_before_the_lock_is_read(
    planner: Any,
) -> None:
    with pytest.raises(PlanError, match="unknown arms"):
        planner(arms=("zzz",))


def test_arms_that_drifted_since_the_lock_refuse_the_plan(planner: Any) -> None:
    lock = _lock()
    lock["execution"]["arms"]["a"]["digest"] = "relocked-elsewhere"

    with pytest.raises(PlanError, match=r"arm\(s\) \['a'\] no longer resolve"):
        planner(lock=lock)


def test_the_plan_document_lists_options_arms_and_marks_each_trial_selected(planner: Any) -> None:
    plan = planner(arms=("a",), attempts=(0,), concurrency=3)
    plan = replace(plan, lock={"execution": {"design": {"varies": ["agent"]}}})

    document = plan.to_json()

    assert document["kind"] == "raidar-plan"
    assert document["experiment"] == "exp-1"
    assert document["lock_sha256"] == "LOCK"
    assert document["concurrency"] == 3
    assert document["progression"]["mode"] == "continue"
    assert document["options"] == {
        "arms": ["a"],
        "attempts": [0],
        "max_checkpoints": None,
        "agent_override": None,
        "concurrency": 3,
    }
    assert document["varies"] == ["agent"]
    assert document["arms"]["a"] == {
        "agent_release": "acme/a@1",
        "workflow": "wf-a@1",
        "workflow_sha256": "wf-a",
        "harbor_agent": "pkg:Agent",
        "model": "model-x",
        "canonical_model": "canon-model",
        "reasoning": "high",
        "capabilities": {"tools": True},
    }
    selected = {t["trial_id"] for t in document["trials"] if t["selected"]}
    assert selected == {"b00-p0-a"}
    assert document["trials"][0]["hashes"]["lock"] == "LOCK"


def test_a_plan_without_a_recorded_design_reports_no_varied_factors(planner: Any) -> None:
    assert planner().to_json()["varies"] == []
