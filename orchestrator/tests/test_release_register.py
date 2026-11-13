"""The release register: what ``name@N`` resolves to, what drift it refuses, and the check."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from raidar.cli import main
from raidar.contract.paths import agents_root
from raidar.contract.releases import ReleaseKind, parse_register
from raidar.subject import agent_checks, release_check
from raidar.subject.agent.definition import load_agent
from raidar.subject.package.registry import load_stage_package
from raidar.subject.release_check import history_issues, release_base, release_issues
from raidar.subject.releases import released_agent, released_workflow
from raidar_contract.canonical import digest_json
from raidar_contract.references import Release, ReleaseError
from tests.release_support import (
    REAL_PACKAGE,
    edit_agent,
    one_shot_digest,
    release_tree,
    write_register,
    write_workflow,
)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return release_tree(tmp_path)


def test_the_registered_latest_releases_resolve(tree: Path) -> None:
    assert str(released_agent("codex@1", repo_root=tree).release) == "codex@1"
    assert released_workflow("raidar/one-shot@1", repo_root=tree).definition.name == "one-shot"


@pytest.mark.parametrize("ref", ["ghost@1", "codex@2"])
def test_an_unregistered_agent_release_is_refused(tree: Path, ref: str) -> None:
    with pytest.raises(ReleaseError, match="not in lab/releases.yaml"):
        released_agent(ref, repo_root=tree)


def test_an_unregistered_workflow_release_is_refused(tree: Path) -> None:
    with pytest.raises(ReleaseError, match="not in lab/releases.yaml"):
        released_workflow("raidar/one-shot@2", repo_root=tree)


@pytest.mark.parametrize("ref", ["one-shot@1", "one-shot-verify@3", "define-build-verify@4"])
def test_a_bare_workflow_reference_never_resolves_though_frozen_in_the_register(
    tree: Path, ref: str
) -> None:
    path = tree / "lab" / "releases.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["workflows"][ref] = "0" * 64
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ReleaseError, match="a namespace is required"):
        released_workflow(ref, repo_root=tree)
    with pytest.raises(ReleaseError, match="a namespace is required"):
        released_workflow(ref, repo_root=tree, draft=True)
    assert release_issues(tree) == []


def test_the_register_accepts_a_bare_workflow_only_as_history_not_as_a_tree_name() -> None:
    register = parse_register(f"workflows:\n  old@1: {'a' * 64}\n  raidar/new@1: {'b' * 64}\n", "t")

    assert register.names(ReleaseKind.WORKFLOW) == {"raidar/new"}
    assert Release("old", 1) in register.of(ReleaseKind.WORKFLOW)


def test_only_the_latest_version_of_a_name_resolves(tree: Path) -> None:
    digest = load_agent(agents_root(tree), "codex").digest
    write_register(tree, extra_agents={"codex@2": digest})

    assert str(released_agent("codex@2", repo_root=tree).release) == "codex@2"
    with pytest.raises(ReleaseError, match=r"codex@1 is not .* the tree holds \(codex@2\)"):
        released_agent("codex@1", repo_root=tree)


def test_a_definition_changed_since_its_release_is_refused(tree: Path) -> None:
    edit_agent(tree, lambda doc: doc["models"]["gpt-6-luna"].update(reasoning=[]))

    with pytest.raises(ReleaseError, match="changed since codex@1 was registered"):
        released_agent("codex@1", repo_root=tree)


def test_a_workflow_changed_since_its_release_is_refused(tree: Path) -> None:
    write_workflow(
        tree,
        {"schema": 2, "name": "one-shot", "build": {"prompt": "Different %%%TASK%%%"}},
    )

    with pytest.raises(ReleaseError, match=r"changed since raidar/one-shot@1 was registered"):
        released_workflow("raidar/one-shot@1", repo_root=tree)


def test_a_namespaced_release_keeps_its_namespace_and_leaf_distinct() -> None:
    release = Release.parse("raidar/review-notes@12")
    assert release.name == "raidar/review-notes"
    assert release.leaf == "review-notes"
    assert release.version == 12


@pytest.mark.parametrize(
    "ref",
    [
        "codex",
        "codex@0",
        "codex@one",
        "Codex@1",
        "@1",
        "codex@01",
        "../codex@1",
        "/codex@1",
        "a/b/c@1",
        "a//b@1",
        "codex@latest",
        "codex@main",
    ],
)
def test_a_malformed_release_reference_is_refused(tree: Path, ref: str) -> None:
    with pytest.raises(ReleaseError, match="invalid release"):
        released_agent(ref, repo_root=tree)


def test_a_definition_that_no_longer_loads_is_a_release_error(tree: Path) -> None:
    edit_agent(tree, lambda doc: doc["settings"].update(isolated_home=False))

    with pytest.raises(ReleaseError, match="invalid agent definition"):
        released_agent("codex@1", repo_root=tree)


# ---- release_issues --------------------------------------------------------------------


def test_a_consistent_tree_has_no_issues(tree: Path) -> None:
    assert release_issues(tree) == []


@pytest.fixture
def stage_evidence_tree(tree: Path) -> tuple[Path, Path, Path]:
    package = tree / "lab" / "stages" / "raidar" / "docs-check"
    shutil.copytree(REAL_PACKAGE, package)
    digest = load_stage_package(package).digest
    register_path = tree / "lab" / "releases.yaml"
    document = yaml.safe_load(register_path.read_text())
    document["stages"] = {"raidar/docs-check@1": digest}
    register_path.write_text(yaml.safe_dump(document))
    record = tree / "lab" / "stage-checks" / f"{digest}.installed.json"
    record.parent.mkdir()
    record.write_text(json.dumps({"arm64": {"tree_digest": "a" * 64, "wheels": []}}))
    lock = tree / "lab" / "experiments" / "smoke" / "evidence" / "experiment.lock.json"
    lock.parent.mkdir(parents=True)
    lock.write_text(
        json.dumps(
            {
                "execution": {
                    "arms": {
                        "checked": {
                            "packages": {
                                "raidar/docs-check": {
                                    "release": "raidar/docs-check@1",
                                    "sha256": digest,
                                    "installed": {"arm64": "a" * 64},
                                }
                            }
                        }
                    }
                }
            }
        )
    )
    return tree, record, lock


def test_committed_stage_evidence_needs_no_var_or_host_architecture(
    stage_evidence_tree: tuple[Path, Path, Path],
) -> None:
    tree, _record, _lock = stage_evidence_tree
    assert not (tree / "var").exists()
    assert release_issues(tree) == []


@pytest.mark.parametrize(
    ("contents", "reason"),
    [
        (None, "unusable installed-tree record"),
        ("not json", "unusable installed-tree record"),
        ("{}", "no installed-tree record for arm64"),
        (
            {"amd64": {"tree_digest": "a" * 64, "wheels": []}},
            "no installed-tree record for arm64",
        ),
        ({"arm64": {"tree_digest": "b" * 64, "wheels": []}}, "differs from locked"),
        (
            {"arm64": {"tree_digest": "not-a-digest", "wheels": []}},
            "unusable installed-tree record",
        ),
        ({"arm64": {"tree_digest": "a" * 64}}, "unusable installed-tree record"),
    ],
)
def test_lock_pinned_stage_evidence_must_be_usable(
    stage_evidence_tree: tuple[Path, Path, Path], contents: object, reason: str
) -> None:
    tree, record, _lock = stage_evidence_tree
    if contents is None:
        record.unlink()
    else:
        record.write_text(contents if isinstance(contents, str) else json.dumps(contents))
    issues = release_issues(tree)
    assert len(issues) == 1
    assert reason in issues[0]
    assert "checked" in issues[0]
    assert "raidar/docs-check" in issues[0]


def test_each_committed_lock_contributes_its_pinned_architectures(
    stage_evidence_tree: tuple[Path, Path, Path],
) -> None:
    tree, _record, lock = stage_evidence_tree
    document = json.loads(lock.read_text())
    document["execution"]["arms"]["checked"]["packages"]["raidar/docs-check"]["installed"] = {
        "amd64": "b" * 64
    }
    other = lock.parent.with_name("another") / "oneshot.experiment.lock.json"
    other.parent.mkdir()
    other.write_text(json.dumps(document))
    issues = release_issues(tree)
    assert len(issues) == 1
    assert "oneshot.experiment.lock.json" in issues[0]
    assert "no installed-tree record for amd64" in issues[0]


@pytest.mark.parametrize("suffix", [".json", ".installed.json"])
def test_stage_records_cannot_name_unregistered_digests(
    stage_evidence_tree: tuple[Path, Path, Path], suffix: str
) -> None:
    tree, record, _lock = stage_evidence_tree
    (record.parent / f"{'f' * 64}{suffix}").write_text("{}")
    assert any("unregistered stage digest" in issue for issue in release_issues(tree))


def test_registered_historical_stage_evidence_remains_valid(
    stage_evidence_tree: tuple[Path, Path, Path],
) -> None:
    tree, record, lock = stage_evidence_tree
    path = tree / "lab" / "releases.yaml"
    document = yaml.safe_load(path.read_text())
    document["stages"]["raidar/docs-check@2"] = document["stages"]["raidar/docs-check@1"]
    document["stages"]["raidar/docs-check@1"] = "f" * 64
    path.write_text(yaml.safe_dump(document))
    (record.parent / f"{'f' * 64}.installed.json").write_text(record.read_text())
    locked = json.loads(lock.read_text())
    locked["execution"]["arms"]["checked"]["packages"]["raidar/docs-check"]["sha256"] = "f" * 64
    lock.write_text(json.dumps(locked))
    assert release_issues(tree) == []


@pytest.mark.parametrize(
    "scope", ["no-architectures", "no-packages", "omitted-packages", "no-arms"]
)
def test_locks_without_pinned_architectures_do_not_require_host_evidence(
    stage_evidence_tree: tuple[Path, Path, Path], scope: str
) -> None:
    tree, record, lock = stage_evidence_tree
    document = json.loads(lock.read_text())
    document["execution"]["arms"]["checked"]["packages"]["raidar/docs-check"]["installed"] = {}
    if scope == "no-packages":
        document["execution"]["arms"]["checked"]["packages"] = {}
    elif scope == "omitted-packages":
        document["execution"]["arms"]["checked"] = {}
    elif scope == "no-arms":
        document["execution"]["arms"] = {}
    lock.write_text(json.dumps(document))
    record.unlink()
    assert release_issues(tree) == []


def test_var_evidence_cannot_replace_committed_evidence(
    stage_evidence_tree: tuple[Path, Path, Path],
) -> None:
    tree, record, _lock = stage_evidence_tree
    transient = tree / "var" / "stage-checks" / record.name
    transient.parent.mkdir(parents=True)
    record.rename(transient)
    assert any("unusable installed-tree record" in issue for issue in release_issues(tree))


@pytest.mark.parametrize("contents", ["not json", "{}", '{"execution": {"arms": []}}'])
def test_unreadable_lock_reports_why_stage_evidence_cannot_be_checked(
    stage_evidence_tree: tuple[Path, Path, Path], contents: str
) -> None:
    tree, _record, lock = stage_evidence_tree
    lock.write_text(contents)
    assert any("cannot check locked stage evidence" in issue for issue in release_issues(tree))


@pytest.mark.parametrize("release", ["raidar/ghost@1", "invalid"])
def test_lock_package_identity_must_be_registered(
    stage_evidence_tree: tuple[Path, Path, Path], release: str
) -> None:
    tree, _record, lock = stage_evidence_tree
    document = json.loads(lock.read_text())
    document["execution"]["arms"]["checked"]["packages"]["raidar/docs-check"]["release"] = release
    lock.write_text(json.dumps(document))
    issues = release_issues(tree)
    assert len(issues) == 1
    assert "not registered" in issues[0] or "cannot check locked stage evidence" in issues[0]


@pytest.mark.parametrize(
    ("contents", "reason"),
    [
        (None, "unusable stage-check record"),
        ("not json", "unusable stage-check record"),
        ([], "wrong package identity"),
        ({"package": "f" * 64}, "wrong package identity"),
        ({"package": "correct", "changed": True}, "differs from the lock"),
        ({"package": "correct"}, None),
    ],
)
def test_locked_canary_record_identity_and_digest_are_checked(
    stage_evidence_tree: tuple[Path, Path, Path], contents: object, reason: str | None
) -> None:
    tree, installed, lock = stage_evidence_tree
    package_digest = installed.name.removesuffix(".installed.json")
    expected = {"package": package_digest}
    document = json.loads(lock.read_text())
    document["execution"]["arms"]["checked"]["packages"]["raidar/docs-check"]["check"] = (
        digest_json(expected)
    )
    lock.write_text(json.dumps(document))
    record = installed.with_name(f"{package_digest}.json")
    if isinstance(contents, dict) and contents.get("package") == "correct":
        contents = {**contents, "package": package_digest}
    if contents is not None:
        record.write_text(contents if isinstance(contents, str) else json.dumps(contents))
    issues = release_issues(tree)
    if reason is None:
        assert issues == []
    else:
        assert any(reason in issue for issue in issues)


def test_a_definition_with_no_registration_is_reported(tree: Path) -> None:
    shutil.copy(agents_root(tree) / "codex.yaml", agents_root(tree) / "extra.yaml")
    edit_agent_named = agents_root(tree) / "extra.yaml"
    edit_agent_named.write_text(
        edit_agent_named.read_text(encoding="utf-8").replace("name: codex", "name: extra"),
        encoding="utf-8",
    )

    issues = release_issues(tree)

    assert len(issues) == 1
    assert "agents/extra is not released" in issues[0]
    assert "extra@1" in issues[0]


def test_hash_drift_is_reported_with_the_line_to_update(tree: Path) -> None:
    write_workflow(
        tree,
        {"schema": 2, "name": "one-shot", "build": {"prompt": "Different %%%TASK%%%"}},
    )

    issues = release_issues(tree)

    assert len(issues) == 1
    assert "workflows/raidar/one-shot changed since raidar/one-shot@1" in issues[0]
    assert f"update raidar/one-shot@1: {one_shot_digest(tree)}" in issues[0]
    assert "raidar/one-shot@2" not in issues[0]


def test_a_registered_name_with_no_definition_is_reported(tree: Path) -> None:
    write_register(tree, extra_agents={"ghost@1": "0" * 64})

    issues = release_issues(tree)

    assert len(issues) == 1
    assert "'ghost'" in issues[0]


def test_an_agent_pin_that_differs_from_the_runtime_pack_is_reported(tree: Path) -> None:
    def repin(doc: dict) -> None:
        doc["tool"].update(version="0.0.1")
        doc["capabilities"].update(version_pin="0.0.1")

    edit_agent(tree, repin)
    write_register(tree)  # registered at the new hash: only the pack pin disagrees

    issues = release_issues(tree)

    assert len(issues) == 1
    assert "agents/codex" in issues[0]
    assert "differs from pack" in issues[0]
    assert "0.0.1" in issues[0]


def test_a_runtime_without_the_agents_harness_has_no_pin_obligation(
    tree: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = agent_checks.load_runtime_pack("python")
    unrelated = pack.model_copy(update={"image": pack.image.model_copy(update={"harnesses": {}})})
    monkeypatch.setattr(agent_checks, "available_runtimes", lambda: ["unrelated"])
    monkeypatch.setattr(agent_checks, "load_runtime_pack", lambda _runtime: unrelated)
    assert release_issues(tree) == []


def test_an_invalid_agent_does_not_hide_another_agents_pack_pin_mismatch(tree: Path) -> None:
    def repin(document: dict) -> None:
        document["tool"]["version"] = "0.0.1"
        document["capabilities"]["version_pin"] = "0.0.1"

    edit_agent(tree, repin)
    write_register(tree)
    document = yaml.safe_load((agents_root(tree) / "codex.yaml").read_text())
    document.update(name="broken", adapter="not-a-harness")
    (agents_root(tree) / "broken.yaml").write_text(yaml.safe_dump(document))
    issues = release_issues(tree)
    assert any("broken.yaml" in issue for issue in issues)
    assert any("agents/codex:" in issue and "0.0.1" in issue for issue in issues)


def test_an_unreadable_register_is_reported(tree: Path) -> None:
    (tree / "lab" / "releases.yaml").write_text("agents: {codex@1: not-a-hash}\n", encoding="utf-8")

    issues = release_issues(tree)

    assert len(issues) == 1
    assert "unreadable release register" in issues[0]


# ---- raidar releases check -----------------------------------------------------------------


def test_releases_cli_refuses_a_removed_release_and_accepts_an_explicit_base_without_main(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tree / "lab" / "releases.yaml"
    original = path.read_text()
    monkeypatch.setattr("raidar.cli.releases.release_base", lambda: "prior")
    monkeypatch.setattr(
        "raidar.cli.releases.release_issues",
        lambda *, base: release_issues(tree, base=base, git=_git(at_base=original)),
    )
    document = yaml.safe_load(original)
    del document["agents"]["codex@1"]
    path.write_text(yaml.safe_dump(document))
    failed = CliRunner().invoke(main, ["releases", "check"])
    assert failed.exit_code == 1
    assert "release codex@1 was removed" in failed.output
    path.write_text(original)
    sound = CliRunner().invoke(main, ["releases", "check"])
    assert sound.exit_code == 0
    assert "releases: ok" in sound.output

    def no_main() -> str:
        raise ReleaseError("no main branch")

    monkeypatch.setattr("raidar.cli.releases.release_base", no_main)
    refused = CliRunner().invoke(main, ["releases", "check"])
    assert refused.exit_code == 1
    assert "no main branch" in refused.output
    explicit = CliRunner().invoke(main, ["releases", "check", "--base", "prior"])
    assert explicit.exit_code == 0, explicit.output


# ---- release_base and history_issues: the decisions, through an injected git ----------------


def _git(
    *,
    head: str | None = "head",
    merge_bases: dict[str, str] | None = None,
    at_base: str | None = None,
    commit: bool = True,
) -> Callable[..., str | None]:
    def run(root: Path, *args: str) -> str | None:
        if args == ("rev-parse", "HEAD"):
            return head
        if args == ("rev-parse", "HEAD~1"):
            return "parent"
        if args[:2] == ("merge-base", "HEAD"):
            return (merge_bases or {}).get(args[2])
        if args[0] == "show":
            return at_base
        if args[0] == "cat-file":
            return "" if commit else None
        raise AssertionError(args)

    return run


@pytest.mark.parametrize(
    ("git", "expected"),
    [
        (_git(head=None), None),
        (_git(merge_bases={"main": "fork"}), "fork"),
        (_git(merge_bases={"origin/main": "fork"}), "fork"),
        (_git(merge_bases={"main": "head"}), "parent"),
    ],
    ids=["no-repo", "off-main", "origin-main-only", "on-main-tip"],
)
def test_the_release_base_is_the_merge_base_or_the_parent_on_main(
    git: Callable[..., str | None], expected: str | None, tmp_path: Path
) -> None:
    assert release_base(tmp_path, git=git) == expected


def test_without_a_main_branch_the_base_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ReleaseError, match="no main branch"):
        release_base(tmp_path, git=_git())


def _history(tmp_path: Path, now: str, git: Callable[..., str | None]) -> list[str]:
    (tmp_path / "lab").mkdir(exist_ok=True)
    (tmp_path / "lab" / "releases.yaml").write_text(now, encoding="utf-8")
    return history_issues("base", tmp_path, git=git)


def _agents(versions: dict[int, str]) -> str:
    return yaml.safe_dump({"agents": {f"codex@{n}": d for n, d in versions.items()}})


def test_re_hashing_a_registered_version_in_place_is_allowed(tmp_path: Path) -> None:
    issues = _history(tmp_path, _agents({1: "b" * 64}), _git(at_base=_agents({1: "a" * 64})))

    assert issues == []


def test_a_removed_released_version_is_refused(tmp_path: Path) -> None:
    issues = _history(
        tmp_path,
        _agents({1: "a" * 64}),
        _git(at_base=_agents({1: "a" * 64, 2: "b" * 64})),
    )

    assert any("release codex@2 was removed" in issue for issue in issues)


def test_a_version_appended_beside_the_released_ones_passes(tmp_path: Path) -> None:
    now = _agents({1: "a" * 64, 2: "b" * 64})

    assert _history(tmp_path, now, _git(at_base=_agents({1: "a" * 64}))) == []


def test_a_base_that_is_not_a_commit_is_reported(tmp_path: Path) -> None:
    issues = _history(tmp_path, _agents({}), _git(at_base=None, commit=False))

    assert issues == ["release base 'base' is not a commit"]


def test_a_register_absent_at_the_base_constrains_nothing(tmp_path: Path) -> None:
    assert _history(tmp_path, _agents({1: "a" * 64}), _git(at_base=None)) == []


def test_an_unreadable_register_at_the_base_is_reported(tmp_path: Path) -> None:
    issues = _history(tmp_path, _agents({1: "a" * 64}), _git(at_base="agents: {codex@1: no}\n"))

    assert len(issues) == 1
    assert "base" in issues[0]


@pytest.mark.parametrize("exit_code", [0, 1])
def test_git_process_outcomes_distinguish_a_missing_repository(
    monkeypatch: pytest.MonkeyPatch, exit_code: int
) -> None:
    def run(command, **kwargs):
        output = " head \n" if command[-1] == "HEAD" else " fork \n"
        return subprocess.CompletedProcess(command, exit_code, stdout=output)

    monkeypatch.setattr(release_check.subprocess, "run", run)
    assert release_base(Path("repo")) == ("fork" if exit_code == 0 else None)


def test_git_timeout_is_not_an_empty_or_clean_history(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr(release_check.subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        release_base(Path("repo"))
