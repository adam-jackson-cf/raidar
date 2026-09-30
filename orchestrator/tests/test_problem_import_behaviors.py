"""Problem import (B1): pinned source -> split task/oracle, digests, drift, resolver."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from harbor.models.task.task import Task

from raidar.oracle.snapshot import APP_SOURCE
from raidar.packs.python import image_tag, load_pack_spec
from raidar.problems.content import content_digest, hidden_content_in_task
from raidar.problems.importer import ProblemDriftError, import_problem
from raidar.problems.models import ProblemId, ProblemMode
from raidar.problems.resolver import ProblemResolver, resolver_factory
from raidar.problems.rewrite import ORACLE_VERIFIER_IMPORT_PATH
from raidar.problems.source import SourceError
from raidar.problems.store import ROOT_ENV, ProblemStore, ProblemStoreError, find_store_root
from raidar.spec.loader import load_experiment
from raidar.spec.lock import build_lock
from raidar.spec.policies import PolicyResolver
from raidar.spec.references import (
    RESOLVER_ENTRY_POINT_GROUP,
    RefKind,
    RegistryRef,
    RegistryReferenceError,
    ResolverSet,
)
from tests.experiment_spec_support import (
    experiment_document,
    fixture_resolvers,
    write_experiment,
)
from tests.problems_support import (
    COMMITTED_FIXTURE,
    NEGATIVE_REF,
    ONESHOT_REF,
    POSITIVE_REF,
    build_fake_scb_repo,
    build_fixture_store,
    fake_store,
    record_validation,
)


@pytest.fixture
def scb(tmp_path: Path) -> tuple[ProblemStore, Path, str]:
    repo, commit = build_fake_scb_repo(tmp_path / "src")
    return fake_store(tmp_path / "problems", commit), repo, commit


def _ref(commit: str, name: str = "scb/adder") -> ProblemId:
    return ProblemId.parse(f"{name}@{commit[:12]}")


def test_problem_references_name_dataset_problem_and_optional_one_shot_checkpoint() -> None:
    multi = ProblemId.parse("scb/forge@38d627ecf668")
    oneshot = ProblemId.parse("scb/circuit_eval.checkpoint_1@38d627ecf668")
    assert (multi.dataset, multi.problem, multi.checkpoint, multi.mode) == (
        "scb",
        "forge",
        None,
        ProblemMode.MULTI_CHECKPOINT,
    )
    assert (oneshot.problem, oneshot.checkpoint, oneshot.mode) == (
        "circuit_eval",
        "checkpoint_1",
        ProblemMode.ONE_SHOT,
    )
    assert str(oneshot) == "scb/circuit_eval.checkpoint_1@38d627ecf668"
    for bad in ("forge@38d627ecf668", "scb/forge", "scb/a/b@1"):
        with pytest.raises(RegistryReferenceError):
            ProblemId.parse(bad)


def test_import_splits_the_converted_task_into_agent_task_bundle_and_references(
    scb: tuple[ProblemStore, Path, str],
) -> None:
    store, repo, commit = scb
    result = import_problem(store, _ref(commit), source_checkout=repo)
    manifest, content = result.manifest, result.content_dir

    assert result.changed
    assert manifest.checkpoints == 2
    assert [step.name for step in manifest.steps] == ["checkpoint_1", "checkpoint_2"]
    assert manifest.source.commit == commit
    assert (
        manifest.source.problem_tree
        == subprocess.run(
            ["git", "-C", str(repo), "rev-parse", f"{commit}:adder"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    assert manifest.pack.image == image_tag(load_pack_spec())

    task = Task(content / "task")
    assert task.config.environment.docker_image == manifest.pack.image
    assert task.config.agent.user == "agent"
    assert [step.min_reward for step in task.config.steps or []] == [None, None]
    assert all(
        step.artifacts[0].source == APP_SOURCE  # type: ignore[union-attr]
        for step in task.config.steps or []
    )
    compose = yaml.safe_load((content / "task" / "environment" / "docker-compose.yaml").read_text())
    assert compose["services"]["main"]["init"] is True
    assert not (content / "task" / "environment" / "Dockerfile").exists()

    loaded = store.load(manifest.problem_id)
    assert hidden_content_in_task(loaded.task_dir, loaded.bundle) == []
    assert not list((content / "task").rglob("test_checkpoint_*"))
    assert not list((content / "task").rglob("solution"))
    bundle = loaded.bundle.manifest
    assert bundle.grader.base_image == manifest.pack.image
    assert bundle.grader.environment_dockerfile is None
    assert [step.test_files for step in bundle.steps] == [
        ("test_checkpoint_1.py",),
        ("test_checkpoint_1.py", "test_checkpoint_2.py"),
    ]
    assert not (loaded.bundle.tests_dir / "_ctrf_to_reward.py").exists()
    assert sorted(p.name for p in loaded.references_dir.iterdir()) == [
        "checkpoint_1",
        "checkpoint_2",
    ]
    assert ORACLE_VERIFIER_IMPORT_PATH == "raidar.oracle.verifier:RaidarOracleVerifier"


def test_reimport_reproduces_the_manifest_and_refuses_drift_unless_updating(
    scb: tuple[ProblemStore, Path, str],
) -> None:
    store, repo, commit = scb
    problem = _ref(commit)
    first = import_problem(store, problem, source_checkout=repo)
    marker = first.content_dir / "task" / "task.toml"
    inode = marker.stat().st_ino
    again = import_problem(store, problem, source_checkout=repo)
    assert not again.changed
    assert again.manifest == first.manifest
    assert marker.stat().st_ino == inode  # matching content is left in place for live readers

    marker.write_text("tampered\n")
    repaired = import_problem(store, problem, source_checkout=repo)
    assert not repaired.changed
    assert store.load(problem).manifest == first.manifest

    path = store.manifest_path(problem)
    recorded = json.loads(path.read_text())
    recorded["task_sha256"] = "0" * 64
    path.write_text(json.dumps(recorded))
    with pytest.raises(ProblemDriftError, match="task_sha256"):
        import_problem(store, problem, source_checkout=repo)
    updated = import_problem(store, problem, source_checkout=repo, update=True)
    assert updated.changed
    assert updated.manifest == first.manifest


def test_one_shot_import_keeps_only_the_first_checkpoint(
    scb: tuple[ProblemStore, Path, str],
) -> None:
    store, repo, commit = scb
    result = import_problem(store, _ref(commit, "scb/adder.checkpoint_1"), source_checkout=repo)
    assert result.manifest.mode is ProblemMode.ONE_SHOT
    assert result.manifest.checkpoints == 1
    loaded = store.load(result.manifest.problem_id)
    assert [step.name for step in Task(loaded.task_dir).config.steps or []] == ["checkpoint_1"]
    assert sorted(p.name for p in loaded.bundle.tests_dir.glob("test_*")) == [
        "test_checkpoint_1.py"
    ]
    with pytest.raises(ValueError, match="first checkpoint"):
        import_problem(store, _ref(commit, "scb/adder.checkpoint_2"), source_checkout=repo)


def test_import_refuses_sources_that_are_not_exactly_the_pin(
    scb: tuple[ProblemStore, Path, str], tmp_path: Path
) -> None:
    store, repo, commit = scb
    with pytest.raises(ProblemStoreError, match="does not match"):
        import_problem(store, ProblemId.parse("scb/adder@000000000000"), source_checkout=repo)
    (repo / "adder" / "config.yaml").write_text("edited: true\n")
    with pytest.raises(SourceError, match="local changes"):
        import_problem(store, _ref(commit), source_checkout=repo)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "--", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "moved",
        ],
        check=True,
    )
    with pytest.raises(SourceError, match="expected pinned commit"):
        import_problem(store, _ref(commit), source_checkout=repo)
    with pytest.raises(ProblemStoreError, match="not pinned"):
        import_problem(store, ProblemId.parse("other/adder@000000000000"), source_checkout=repo)


@pytest.mark.parametrize(
    ("converter", "message"),
    [("scripts/absent.py", "pinned converter scripts/absent.py not found"), ("../x.py", "inside")],
)
def test_import_runs_the_converter_the_pin_names(
    scb: tuple[ProblemStore, Path, str], converter: str, message: str
) -> None:
    store, repo, commit = scb
    sources = store.root / "sources.yaml"
    sources.write_text(sources.read_text() + f"    converter: {converter}\n")
    with pytest.raises(SourceError, match=message):
        import_problem(store, _ref(commit), source_checkout=repo)


def test_default_import_clones_the_pinned_commit_into_the_store_cache(tmp_path: Path) -> None:
    repo, commit = build_fake_scb_repo(tmp_path / "src")
    store = tmp_path / "problems"
    store.mkdir()
    (store / "sources.yaml").write_text(
        f"sources:\n  scb:\n    url: {repo}\n    commit: {commit}\n"
    )
    result = import_problem(ProblemStore(store), _ref(commit))
    assert (store / "cache" / "sources" / f"scb-{commit[:12]}" / ".git").exists()
    assert result.manifest.source.url == str(repo)


def test_loading_refuses_missing_or_drifted_content(tmp_path: Path) -> None:
    store = build_fixture_store(tmp_path / "store")
    problem = ProblemId.parse(POSITIVE_REF)
    loaded = store.load(problem)
    (loaded.task_dir / "steps" / "checkpoint_1" / "instruction.md").write_text("tampered\n")
    with pytest.raises(ProblemStoreError, match="task_sha256"):
        store.load(problem)
    shutil.rmtree(store.content_dir(problem))
    with pytest.raises(ProblemStoreError, match="raidar problem import"):
        store.load(problem)
    with pytest.raises(ProblemStoreError, match="not imported"):
        store.load(ProblemId.parse("fixture/absent@fefefefefefe"))


@pytest.mark.parametrize("area", ["task", "oracle", "references"])
@pytest.mark.parametrize("excluded_dir", ["build", "dist", "__pycache__", ".venv"])
def test_content_digests_cover_directories_a_workspace_snapshot_excludes(
    tmp_path: Path, area: str, excluded_dir: str
) -> None:
    store = build_fixture_store(tmp_path / "store")
    problem = ProblemId.parse(POSITIVE_REF)
    extra = store.content_dir(problem) / area / excluded_dir / "extra.txt"
    extra.parent.mkdir(parents=True)
    extra.write_text("not in the manifest\n")
    with pytest.raises(ProblemStoreError, match="drifted"):
        store.load(problem)


def test_loading_refuses_a_task_that_exposes_hidden_tests_even_with_matching_digests(
    tmp_path: Path,
) -> None:
    store = build_fixture_store(tmp_path / "store")
    problem = ProblemId.parse(POSITIVE_REF)
    loaded = store.load(problem)
    leaked = loaded.task_dir / "build" / "test_checkpoint_2.py"
    leaked.parent.mkdir()
    shutil.copy2(loaded.bundle.tests_dir / "test_checkpoint_2.py", leaked)
    manifest = loaded.manifest.model_copy(update={"task_sha256": content_digest(loaded.task_dir)})
    store.write_manifest(manifest)  # a manifest re-recorded over leaked content
    with pytest.raises(ProblemStoreError, match="hidden oracle content"):
        store.load(problem)


def test_problem_resolver_reports_checkpoints_and_manifest_digest(tmp_path: Path) -> None:
    store = build_fixture_store(tmp_path / "problems")
    for ref in (ONESHOT_REF, POSITIVE_REF):
        record_validation(store, ref, tmp_path / "validate")
    resolver = ProblemResolver(store.root)
    oneshot = resolver.resolve(RegistryRef.parse(RefKind.PROBLEM, ONESHOT_REF), base_dir=tmp_path)
    multi = resolver.resolve(RegistryRef.parse(RefKind.PROBLEM, POSITIVE_REF), base_dir=tmp_path)
    assert oneshot.metadata["checkpoints"] == 1
    assert oneshot.metadata["mode"] == "one_shot"
    assert multi.metadata["checkpoints"] == 2
    assert multi.digest == store.load_manifest(ProblemId.parse(POSITIVE_REF)).digest()
    assert multi.metadata["validation"]["verdict"] == "validated_partial"
    assert oneshot.metadata["validation"]["not_run"] == [
        "native_parity_positive",
        "native_parity_negative",
    ]
    with pytest.raises(RegistryReferenceError, match="not imported"):
        resolver.resolve(
            RegistryRef.parse(RefKind.PROBLEM, "fixture/absent@fefefefefefe"), base_dir=tmp_path
        )


def test_resolver_entry_point_discovers_the_store_from_the_experiment_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ROOT_ENV, raising=False)
    store = build_fixture_store(tmp_path / "problems")
    record_validation(store, POSITIVE_REF, tmp_path / "validate")
    experiment_dir = tmp_path / "examples" / "pilot"
    experiment_dir.mkdir(parents=True)
    resolver = ResolverSet(entry_point_group=RESOLVER_ENTRY_POINT_GROUP).resolver_for(
        RefKind.PROBLEM
    )
    assert isinstance(resolver, ProblemResolver)
    resolved = resolver.resolve(
        RegistryRef.parse(RefKind.PROBLEM, POSITIVE_REF), base_dir=experiment_dir
    )
    assert resolved.metadata["checkpoints"] == 2
    assert find_store_root(experiment_dir) == (tmp_path / "problems").resolve()
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "elsewhere"))
    assert find_store_root(experiment_dir) == (tmp_path / "elsewhere").resolve()
    monkeypatch.delenv(ROOT_ENV)
    with pytest.raises(ProblemStoreError, match="sources.yaml"):
        find_store_root(Path("/"))
    assert isinstance(resolver_factory(), ProblemResolver)


def test_committed_fixture_store_matches_its_builder(tmp_path: Path) -> None:
    built = build_fixture_store(tmp_path / "adder").root

    def files(root: Path) -> dict[str, bytes]:
        return {
            p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*"))
            if p.is_file() and not p.name.endswith(".validation.json")
        }

    assert files(COMMITTED_FIXTURE) == files(built)


@pytest.mark.parametrize(
    ("ref", "policy", "checkpoints"),
    [(ONESHOT_REF, "scb-canonical-oneshot@1", 1), (POSITIVE_REF, "scb-canonical@1", 2)],
)
def test_lock_resolves_imported_problems_and_selects_the_policy_by_k(
    tmp_path: Path, ref: str, policy: str, checkpoints: int
) -> None:
    store = build_fixture_store(tmp_path / "problems")
    record_validation(store, ref, tmp_path / "validate")
    path = write_experiment(tmp_path / "exp", experiment_document(problems=[{"ref": ref}]))
    others = [r for r in fixture_resolvers() if r.kind is not RefKind.PROBLEM]
    resolvers = ResolverSet(
        [ProblemResolver(store.root), *others, PolicyResolver()], entry_point_group=None
    )
    lock: Any = build_lock(
        load_experiment(path), lock_path=path.with_name("experiment.lock.json"), resolvers=resolvers
    )
    assert lock["headline_policy"] == {
        "ref": policy,
        "selection": "default_by_k",
        "checkpoints": [checkpoints],
    }
    manifest = store.load_manifest(ProblemId.parse(ref))
    assert lock["problems"] == [
        {"label": ref, "checkpoints": checkpoints, "digest": manifest.digest()}
    ]
    assert lock["references"]["problem"][ref]["metadata"]["source"]["commit"] == "fe" * 20


def test_hidden_content_check_flags_renamed_copies_and_solutions(tmp_path: Path) -> None:
    store = build_fixture_store(tmp_path / "problems")
    loaded = store.load(ProblemId.parse(POSITIVE_REF))
    assert hidden_content_in_task(loaded.task_dir, loaded.bundle) == []
    shutil.copy2(loaded.bundle.tests_dir / "test_checkpoint_2.py", loaded.task_dir / "notes.txt")
    solution = loaded.task_dir / "steps" / "checkpoint_1" / "solution" / "solve.sh"
    solution.parent.mkdir()
    solution.write_text("#!/bin/bash\n")
    assert hidden_content_in_task(loaded.task_dir, loaded.bundle) == [
        "notes.txt",
        "steps/checkpoint_1/solution/solve.sh",
    ]


def _resolve(store: ProblemStore, ref: str, tmp_path: Path) -> object:
    return ProblemResolver(store.root).resolve(
        RegistryRef.parse(RefKind.PROBLEM, ref), base_dir=tmp_path
    )


def test_only_a_usable_verdict_for_the_current_manifest_resolves(tmp_path: Path) -> None:
    store = build_fixture_store(tmp_path / "problems")
    with pytest.raises(RegistryReferenceError, match="not oracle-validated"):
        _resolve(store, POSITIVE_REF, tmp_path)

    excluded = record_validation(store, NEGATIVE_REF, tmp_path / "negative")
    assert excluded.verdict.value == "excluded"
    with pytest.raises(RegistryReferenceError, match="verdict excluded"):
        _resolve(store, NEGATIVE_REF, tmp_path)

    record_validation(store, POSITIVE_REF, tmp_path / "positive")
    report_path = store.validation_path(ProblemId.parse(POSITIVE_REF))
    report = json.loads(report_path.read_text())
    for verdict in ("infra_failure", "validated_without_native_parity"):
        report_path.write_text(json.dumps({**report, "verdict": verdict}))
        with pytest.raises(RegistryReferenceError, match="not"):
            _resolve(store, POSITIVE_REF, tmp_path)

    report_path.write_text(json.dumps({**report, "manifest_sha256": "0" * 64}))
    with pytest.raises(RegistryReferenceError, match="re-validate"):
        _resolve(store, POSITIVE_REF, tmp_path)
    report_path.write_text(json.dumps(report))
    assert _resolve(store, POSITIVE_REF, tmp_path) is not None
