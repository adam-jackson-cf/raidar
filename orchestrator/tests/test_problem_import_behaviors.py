"""Problem import (B1): pinned source -> split task/oracle, digests, drift, resolver."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from harbor.models.task.task import Task

from raidar.contract.paths import VAR_ENV, problems_root
from raidar.contract.references import (
    RESOLVER_ENTRY_POINT_GROUP,
    RefKind,
    RegistryRef,
    RegistryReferenceError,
    ResolvedReference,
    ResolverSet,
)
from raidar.contract.snapshot import APP_SOURCE
from raidar.design.experiment.loader import load_experiment
from raidar.design.experiment.lock import build_lock
from raidar.design.problem import importer
from raidar.design.problem.content import hidden_content_in_task
from raidar.design.problem.models import ProblemId
from raidar.design.problem.resolver import ProblemResolver, resolver_factory
from raidar.design.problem.rewrite import VERIFIER_IMPORT_PATH
from raidar.design.problem.source import SourceError
from raidar.design.problem.store import (
    ROOT_ENV,
    ProblemStore,
    ProblemStoreError,
    open_store,
    store_root,
)
from raidar.scoring.framework.metrics import MetricResolver
from raidar.subject.environment.python.image import image_tag
from raidar.subject.environment.python.pack_definition import load_pack_spec
from tests.experiment_spec_support import (
    experiment_document,
    fixture_resolvers,
    write_experiment,
)
from tests.problems_support import (
    NEGATIVE_REF,
    ONESHOT_REF,
    POSITIVE_REF,
    FakeResolver,
    build_fake_scb_repo,
    build_fixture_store,
    fake_store,
    record_validation,
)

pytestmark = pytest.mark.slow


@pytest.fixture
def scb(tmp_path: Path) -> tuple[ProblemStore, Path, str]:
    repo, commit = build_fake_scb_repo(tmp_path / "src")
    return fake_store(tmp_path / "problems", commit), repo, commit


def import_problem(*args: Any, **kwargs: Any) -> importer.ImportResult:
    """The real import with an offline deterministic resolver: tests never reach an index."""
    return importer.import_problem(*args, resolver=FakeResolver(), **kwargs)


def _ref(commit: str, name: str = "scb/adder") -> ProblemId:
    return ProblemId.parse(f"{name}@{commit[:12]}")


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
    # SCB config ``entry_file: app`` in the Python pack's snapshot-file format.
    assert manifest.entry_file == "app.py"
    assert manifest.lock_metadata()["entry_file"] == "app.py"

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
    assert bundle.entry_file == manifest.entry_file, "the entrypoint travels with the verifier"
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
    assert VERIFIER_IMPORT_PATH == "raidar.grading.verifier:RaidarVerifier"


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


def test_the_store_root_is_explicit_and_never_searched_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(VAR_ENV, raising=False)
    store = build_fixture_store(
        tmp_path / "lab" / "problems", data_root=tmp_path / "var" / "problems"
    )
    record_validation(store, POSITIVE_REF, tmp_path / "validate")
    experiment_dir = tmp_path / "examples" / "pilot"
    experiment_dir.mkdir(parents=True)
    monkeypatch.chdir(experiment_dir)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "lab" / "problems"))
    resolver = ResolverSet(entry_point_group=RESOLVER_ENTRY_POINT_GROUP).resolver_for(
        RefKind.PROBLEM
    )
    assert isinstance(resolver, ProblemResolver)
    resolved = resolver.resolve(
        RegistryRef.parse(RefKind.PROBLEM, POSITIVE_REF), base_dir=experiment_dir
    )
    assert resolved.metadata["checkpoints"] == 2
    assert open_store() == ProblemStore(
        (tmp_path / "lab" / "problems").resolve(), tmp_path.resolve() / "var" / "problems"
    )
    monkeypatch.setenv(VAR_ENV, str(tmp_path / "relocated"))
    assert open_store().data == (tmp_path / "relocated" / "problems").resolve()
    monkeypatch.delenv(VAR_ENV)
    assert open_store(tmp_path / "elsewhere").data == (tmp_path / "elsewhere").resolve()
    assert open_store(tmp_path / "elsewhere").root == (tmp_path / "elsewhere").resolve()
    monkeypatch.delenv(ROOT_ENV)
    assert store_root() == problems_root()
    assert store_root(tmp_path / "x") == (tmp_path / "x").resolve()
    with pytest.raises(RegistryReferenceError, match="not imported"):
        resolver.resolve(RegistryRef.parse(RefKind.PROBLEM, POSITIVE_REF), base_dir=experiment_dir)
    assert isinstance(resolver_factory(), ProblemResolver)


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
        [ProblemResolver(store.root), *others, MetricResolver()], entry_point_group=None
    )
    lock: Any = build_lock(
        load_experiment(path), lock_path=path.with_name("experiment.lock.json"), resolvers=resolvers
    )
    assert lock["assessment"]["metric"] == {
        "ref": policy,
        "selection": "default_by_k",
        "checkpoints": [checkpoints],
    }
    manifest = store.load_manifest(ProblemId.parse(ref))
    assert lock["execution"]["problems"] == [
        {"label": ref, "checkpoints": checkpoints, "digest": manifest.digest()}
    ]
    recorded = lock["execution"]["references"]["problem"][ref]
    assert recorded["metadata"]["source"]["commit"] == "fe" * 20


def _resolve(store: ProblemStore, ref: str, tmp_path: Path) -> ResolvedReference:
    return ProblemResolver(store.root).resolve(
        RegistryRef.parse(RefKind.PROBLEM, ref), base_dir=tmp_path
    )


def _refused(store: ProblemStore, ref: str, tmp_path: Path, match: str) -> None:
    """Unvalidated bindings reach the gate (``RT0``) instead of resolving silently."""
    resolved = _resolve(store, ref, tmp_path)
    assert resolved.binding["validated"] is False
    assert re.search(match, str(resolved.binding["validation_issue"]))
    assert "validation" not in resolved.metadata


def test_only_a_usable_verdict_for_the_current_manifest_resolves(tmp_path: Path) -> None:
    store = build_fixture_store(tmp_path / "problems")
    _refused(store, POSITIVE_REF, tmp_path, "not verifier-validated")

    excluded = record_validation(store, NEGATIVE_REF, tmp_path / "negative")
    assert excluded.verdict.value == "excluded"
    _refused(store, NEGATIVE_REF, tmp_path, "verdict excluded")

    record_validation(store, POSITIVE_REF, tmp_path / "positive")
    report_path = store.validation_path(ProblemId.parse(POSITIVE_REF))
    report = json.loads(report_path.read_text())
    for verdict in ("infra_failure", "validated_without_native_parity"):
        report_path.write_text(json.dumps({**report, "verdict": verdict}))
        _refused(store, POSITIVE_REF, tmp_path, "not")

    report_path.write_text(json.dumps({**report, "manifest_sha256": "0" * 64}))
    _refused(store, POSITIVE_REF, tmp_path, "re-validate")
    report_path.write_text(json.dumps(report))
    resolved = _resolve(store, POSITIVE_REF, tmp_path)
    assert resolved.binding["validated"] is True
    assert "validation" in resolved.metadata
    with pytest.raises(RegistryReferenceError, match="not imported"):
        _resolve(store, "fixture/missing@fefefefefefe", tmp_path)


def test_import_refuses_ignored_files_under_the_problem(
    scb: tuple[ProblemStore, Path, str],
) -> None:
    store, repo, commit = scb
    (repo / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("__pycache__/\n")
    cache = repo / "adder" / "tests" / "__pycache__"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "conftest.cpython-312.pyc").write_bytes(b"\0")
    with pytest.raises(SourceError, match="local changes"):
        import_problem(store, _ref(commit), source_checkout=repo)
