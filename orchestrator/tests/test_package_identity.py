"""Package identity: the register, ``stage-tree/1``, the workflow fold, AT4 and the ST refusals."""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from raidar_contract.package import StagePackageError
from raidar_contract.package.digest import read_stage_tree, stage_tree_digest
from raidar_contract.package.registry import load_stage_package
from raidar_contract.releases import (
    Release,
    ReleaseError,
    ReleaseKind,
    parse_register,
    rewritten_releases,
)

from raidar.contract.paths import agents_root, releases_file, stages_root, workflows_root
from raidar.subject.agent.definition import load_agent
from raidar.subject.release_check import release_issues
from raidar.subject.releases import released_workflow
from raidar.subject.stages import released_stage, stage_names
from raidar.subject.workflow import package_checks
from raidar.subject.workflow.package_checks import ResolvedStage, package_issues
from tests.package_support import fixture_package, write_package
from tests.release_support import release_tree

BUILD = "Work.\n\n%%%GATE_CHECK%%%\n\n%%%TASK%%%"


@pytest.fixture(autouse=True)
def _no_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    """``uv lock --check`` is the one subprocess; tests decide its verdict."""
    monkeypatch.setattr(package_checks, "uv_lock_stale", lambda tool: None)


def _workflow_document(params: dict[str, Any] | None = None, build: str = BUILD) -> dict[str, Any]:
    use: dict[str, Any] = {"use": "acme/gate-check@1"}
    if params:
        use["params"] = params
    return {"schema": 2, "name": "gated", "build": {"prompt": build}, "stages": [use]}


def _write_workflow(root: Path, document: dict[str, Any]) -> None:
    folder = workflows_root(root) / "acme" / "gated"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "workflow.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")


def _register(root: Path, *, workflow: bool = True) -> None:
    """Register codex, raidar/one-shot, acme/gate-check@1 and optionally acme/gated@1."""
    from raidar.subject.workflow.definition import load_workflow

    stages = {
        f"{name}@1": load_stage_package(stages_root(root) / name).digest
        for name in stage_names(root)
    }
    document: dict[str, Any] = {
        "agents": {"codex@1": load_agent(agents_root(root), "codex").digest},
        "workflows": {
            "raidar/one-shot@1": load_workflow(workflows_root(root), "raidar/one-shot").digest
        },
        "stages": stages,
    }
    releases_file(root).write_text(yaml.safe_dump(document), encoding="utf-8")
    if workflow:
        gated = released_workflow("acme/gated@1", repo_root=root, draft=True).digest
        document["workflows"]["acme/gated@1"] = gated
        releases_file(root).write_text(yaml.safe_dump(document), encoding="utf-8")
    imports = [
        {"name": ref.rsplit("@", 1)[0], "sha256": digest, "source": "local identity fixture"}
        for kind in ("stages", "workflows")
        for ref, digest in document.get(kind, {}).items()
        if not ref.startswith("raidar/")
    ]
    (root / "lab/imports.yaml").write_text(yaml.safe_dump(imports), encoding="utf-8")


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("RAIDAR_VAR", str(tmp_path / "var"))
    root = release_tree(tmp_path / "repo")
    fixture_package("gate-check", stages_root(root))
    _write_workflow(root, _workflow_document())
    _register(root)
    return root


def _package_file(root: Path, relative: str) -> Path:
    return stages_root(root) / "acme" / "gate-check" / relative


# ---- the register and the reference grammar ------------------------------------------------


def test_the_register_holds_stage_releases_and_history_is_append_only() -> None:
    digest = "a" * 64
    earlier = parse_register(
        f"stages:\n  acme/gate-check@1: {digest}\n  acme/docs@1: {digest}\n", "base"
    )
    assert {str(r) for r in earlier.of(ReleaseKind.STAGE)} == {"acme/gate-check@1", "acme/docs@1"}
    assert earlier.latest(ReleaseKind.STAGE, "acme/docs") == Release("acme/docs", 1)

    grown = parse_register(
        f"stages:\n  acme/gate-check@1: {digest}\n  acme/docs@1: {digest}\n"
        f"  acme/gate-check@2: {'b' * 64}\n",
        "now",
    )
    assert rewritten_releases(earlier, grown) == []

    rehashed = parse_register(
        f"stages:\n  acme/gate-check@1: {'c' * 64}\n  acme/docs@1: {digest}\n", "x"
    )
    removed = parse_register(f"stages:\n  acme/gate-check@1: {digest}\n", "x")
    assert "re-hashed" in rewritten_releases(earlier, rehashed)[0]
    assert "removed" in rewritten_releases(earlier, removed)[0]


@pytest.mark.parametrize(
    "text",
    [
        "gate-check",
        "gate-check@1",
        "acme/gate-check",
        "acme/gate-check@latest",
        "acme/gate-check@main",
        "acme/gate-check@0",
        "a/b/c@1",
        "../x@1",
        "/abs@1",
        "Gate@1",
        "acme/gate-check@1.0",
    ],
)
def test_mutable_or_unnamespaced_stage_references_never_parse(text: str) -> None:
    with pytest.raises(ReleaseError):
        Release.parse_stage(text)


def test_only_stage_references_require_a_namespace() -> None:
    assert Release.parse("one-shot@1") == Release("one-shot", 1)
    assert Release.parse_workflow("raidar/one-shot@1") == Release("raidar/one-shot", 1)
    assert Release.parse_stage("acme/gate-check@1") == Release("acme/gate-check", 1)


@pytest.mark.parametrize("use", ["gate-check@1", "acme/gate-check@latest"])
def test_a_workflow_refuses_a_mutable_or_unnamespaced_package_reference(
    tree: Path, use: str
) -> None:
    document = _workflow_document()
    document["stages"] = [{"use": use}]
    _write_workflow(tree, document)

    with pytest.raises(ReleaseError, match="invalid workflow definition"):
        released_workflow("acme/gated@1", repo_root=tree, draft=True)


def test_a_register_with_an_unnamespaced_stage_is_unreadable() -> None:
    with pytest.raises(ReleaseError, match="namespace is required"):
        parse_register(f"stages:\n  gate-check@1: {'a' * 64}\n", "base")


def test_a_bare_stage_reference_is_refused_even_when_a_flat_folder_exists(tree: Path) -> None:
    flat = stages_root(tree) / "gate-check"
    shutil.copytree(_package_file(tree, ""), flat)

    with pytest.raises(ReleaseError, match="namespace is required"):
        released_stage("gate-check@1", repo_root=tree, draft=True)
    with pytest.raises(StagePackageError, match="expected stages/<namespace>/<name>"):
        load_stage_package(flat)


def test_a_package_deeper_than_namespace_and_name_is_refused(tmp_path: Path) -> None:
    folder = write_package(tmp_path / "lab" / "stages" / "acme" / "group", {"name": "docs"})

    with pytest.raises(StagePackageError, match="expected stages/<namespace>/<name>"):
        load_stage_package(folder)
    assert stage_names(tmp_path) == []


def test_only_the_latest_stage_release_resolves(tree: Path) -> None:
    register = yaml.safe_load(releases_file(tree).read_text())
    register["stages"]["acme/gate-check@2"] = register["stages"]["acme/gate-check@1"]
    releases_file(tree).write_text(yaml.safe_dump(register))

    with pytest.raises(ReleaseError, match="not the stages release the tree holds"):
        released_stage("acme/gate-check@1", repo_root=tree)
    assert released_stage("acme/gate-check@2", repo_root=tree).ref == "acme/gate-check@2"


def test_a_namespaced_package_lives_below_its_namespace(tmp_path: Path) -> None:
    folder = write_package(tmp_path / "lab" / "stages" / "acme", {"name": "docs"})
    package = load_stage_package(folder)
    register = f"stages:\n  acme/docs@1: {package.digest}\n"
    (tmp_path / "lab").mkdir(exist_ok=True)
    (tmp_path / "lab" / "releases.yaml").write_text(register)

    assert stage_names(tmp_path) == ["acme/docs"]
    assert released_stage("acme/docs@1", repo_root=tmp_path).digest == package.digest


def test_release_issues_name_an_unreleased_or_edited_package(tree: Path) -> None:
    assert release_issues(tree) == []
    _package_file(tree, "prompt.md").write_text("Edited %%%MARKER%%%.\n")

    issues = release_issues(tree)
    assert any(
        i.startswith(
            "stages/acme/gate-check changed since acme/gate-check@1: register acme/gate-check@2:"
        )
        for i in issues
    )
    assert any("workflows/acme/gated changed since acme/gated@1" in i for i in issues)

    shutil.rmtree(_package_file(tree, ""))
    assert any("registered but not defined" in i for i in release_issues(tree))


# ---- stage-tree/1 --------------------------------------------------------------------------


def _digest(root: Path) -> str:
    return stage_tree_digest(read_stage_tree(root))


def test_stage_tree_digest_covers_bytes_and_every_executable_bit(tmp_path: Path) -> None:
    root = fixture_package("gate-check", tmp_path)
    base = _digest(root)

    (root / "prompt.md").write_text("changed\n")
    assert _digest(root) != base
    shutil.rmtree(root)

    root = fixture_package("gate-check", tmp_path)
    assert _digest(root) == base  # nothing but the bytes counted
    target = root / "tool" / "src" / "gate_check" / "__main__.py"
    mode = target.stat().st_mode
    for bit in (stat.S_IXUSR, stat.S_IXGRP, stat.S_IXOTH):
        os.chmod(target, mode | bit)
        assert _digest(root) != base, f"execute bit {oct(bit)} must move the digest"
        os.chmod(target, mode)
        assert _digest(root) == base


def test_stage_tree_digest_leaves_fixtures_and_junk_out(tmp_path: Path) -> None:
    root = fixture_package("gate-check", tmp_path)
    base = _digest(root)
    (root / "fixtures" / "pass" / "state" / "extra.py").write_text("x = 1\n")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "a.pyc").write_bytes(b"x")
    (root / ".DS_Store").write_bytes(b"x")

    assert _digest(root) == base


@pytest.mark.parametrize("where", ["tool/link", "fixtures/pass/link"])
def test_stage_tree_refuses_symlinks_everywhere(tmp_path: Path, where: str) -> None:
    root = fixture_package("gate-check", tmp_path)
    (root / where).symlink_to("/etc/hostname")

    with pytest.raises(StagePackageError, match="symlinks are refused"):
        read_stage_tree(root)


# ---- AT4: identity moves, stale locks are refused ------------------------------------------


def _identity(root: Path) -> str:
    return released_workflow("acme/gated@1", repo_root=root, draft=True).digest


def _mutate_prompt(root: Path) -> None:
    _package_file(root, "prompt.md").write_text("Another sentence about %%%MARKER%%%.\n")


def _mutate_tool(root: Path) -> None:
    path = _package_file(root, "tool/src/gate_check/__main__.py")
    path.write_text(path.read_text() + "\n# behaviour change\n")


def _mutate_param(root: Path) -> None:
    _write_workflow(root, _workflow_document({"marker": "TODO"}))


@pytest.mark.parametrize("mutate", [_mutate_prompt, _mutate_tool, _mutate_param])
def test_each_prompt_tool_or_param_change_alone_moves_identity_and_stales_the_release(
    tree: Path, mutate: Callable[[Path], None]
) -> None:
    before = _identity(tree)
    assert released_workflow("acme/gated@1", repo_root=tree).digest == before

    mutate(tree)

    assert _identity(tree) != before
    with pytest.raises(ReleaseError, match=r"(ST4: acme/gate-check|gated) changed since"):
        released_workflow("acme/gated@1", repo_root=tree)


def test_the_fold_carries_release_digest_and_effective_params(tree: Path) -> None:
    workflow = released_workflow("acme/gated@1", repo_root=tree)
    (stage,) = workflow.stages

    assert stage.entry == {
        "use": "acme/gate-check@1",
        "sha256": load_stage_package(_package_file(tree, "")).digest,
        "params": {"marker": "FIXME"},
    }
    other = _workflow_document({"marker": "FIXME"})
    _write_workflow(tree, other)
    assert _identity(tree) == workflow.digest  # an explicit default is the same effective params


def test_an_undeclared_or_retyped_param_is_refused(tree: Path) -> None:
    for params in ({"nope": "x"}, {"marker": 3}):
        _write_workflow(tree, _workflow_document(params))
        with pytest.raises(ReleaseError, match="ST1"):
            released_workflow("acme/gated@1", repo_root=tree, draft=True)


# ---- ST1 .. ST7 ----------------------------------------------------------------------------


def _resolved(root: Path, name: str, release: str | None = None) -> ResolvedStage:
    package = load_stage_package(stages_root(root) / name)
    return ResolvedStage(Release.parse(release or f"{name}@1"), package, package.effective_params())


def _own(tmp_path: Path, manifest: dict[str, Any], **kw: Any) -> ResolvedStage:
    folder = write_package(tmp_path / "lab" / "stages" / "acme", manifest, **kw)
    package = load_stage_package(folder)
    return ResolvedStage(Release.parse_stage(f"acme/{package.name}@1"), package, {})


def test_st1_an_unsupported_contract_or_invalid_package_is_refused(tree: Path) -> None:
    manifest = _package_file(tree, "stage.yaml")
    manifest.write_text(manifest.read_text().replace("stage/1", "stage/2"))

    with pytest.raises(ReleaseError, match=r"^ST1:"):
        released_stage("acme/gate-check@1", repo_root=tree, draft=True)


def test_st1_a_prompt_naming_an_undefined_token_is_refused(tmp_path: Path) -> None:
    stage = _own(tmp_path, {"name": "tokens"}, prompt="Use %%%NOT_DEFINED%%%.\n")

    assert any(
        i.startswith("ST1:") and "NOT_DEFINED" in i
        for i in package_issues("%%%TOKENS%%% %%%TASK%%%", [stage])
    )


def test_st2_an_unsupported_primitive_is_refused(tmp_path: Path) -> None:
    stage = _own(tmp_path, {"name": "future", "requires": {"gate": "gate/9"}})

    issues = package_issues("%%%FUTURE%%% %%%TASK%%%", [stage])
    assert any(i.startswith("ST2:") and "gate/9" in i for i in issues)


def test_st3_an_input_without_an_earlier_producer_is_refused(tmp_path: Path) -> None:
    producer = _own(
        tmp_path,
        {
            "name": "maker",
            "requires": {"outputs": "outputs/1"},
            "outputs": {
                "criteria": {"path": "/app/.raidar/maker/c.json", "contract": "criteria/1"}
            },
        },
    )
    consumer = _own(
        tmp_path,
        {"name": "taker", "inputs": {"crit": {"from": "maker.criteria", "contract": "criteria/1"}}},
    )
    build = "%%%MAKER%%% %%%TAKER%%% %%%TASK%%%"

    assert package_issues(build, [producer, consumer]) == []
    forward = package_issues(build, [consumer, producer])
    assert any(i.startswith("ST3:") and "maker.criteria" in i for i in forward)
    assert any(i.startswith("ST3:") for i in package_issues(build, [consumer]))


def test_st4_a_package_that_differs_from_the_register_is_refused(tree: Path) -> None:
    _mutate_prompt(tree)

    pattern = r"ST4: acme/gate-check changed since acme/gate-check@1"
    with pytest.raises(ReleaseError, match=pattern):
        released_stage("acme/gate-check@1", repo_root=tree)
    drafted = released_stage("acme/gate-check@1", repo_root=tree, draft=True)
    assert drafted.ref == "acme/gate-check@1"


def test_st5_a_missing_or_sdist_or_stale_lock_is_refused(tree: Path, tmp_path: Path) -> None:
    stage = _resolved(tree, "acme/gate-check")
    build = "%%%GATE_CHECK%%% %%%TASK%%%"
    assert package_issues(build, [stage]) == []

    assert any(
        i.startswith("ST5:") and "stale" in i
        for i in package_issues(build, [stage], lock_check=lambda tool: "needs update")
    )

    bare = _own(
        tmp_path,
        {"name": "nolock", "tool": {"project": "tool"}},
        files={"tool/pyproject.toml": "[project]\nname = 'x'\nversion = '1'\ndependencies = []\n"},
    )
    assert any(
        i.startswith("ST5:") and "uv.lock" in i
        for i in package_issues("%%%NOLOCK%%% %%%TASK%%%", [bare])
    )

    lock = (_package_file(tree, "tool/uv.lock")).read_text()
    sdist = _own(
        tmp_path,
        {"name": "sdist", "tool": {"project": "tool"}},
        files={
            "tool/pyproject.toml": "[project]\nname = 'x'\nversion = '1'\ndependencies = []\n",
            "tool/uv.lock": lock + '\n[[package]]\nname = "x"\nversion = "1"\n'
            'source = { registry = "https://pypi.org/simple" }\n'
            'sdist = { url = "https://files.pythonhosted.org/x.tar.gz", hash = "sha256:'
            + "0" * 64
            + '" }\n',
        },
    )
    assert any(
        i.startswith("ST5:") and "sdist" in i
        for i in package_issues("%%%SDIST%%% %%%TASK%%%", [sdist])
    )


@pytest.mark.parametrize(
    "build",
    [
        "%%%TASK%%%",
        "%%%GATE_CHECK%%% %%%GATE_CHECK%%% %%%TASK%%%",
        "%%%GATE_CHECK%%% %%%STRANGER%%% %%%TASK%%%",
        "%%%GATE_CHECK%%%",
    ],
)
def test_st6_a_missing_repeated_or_unknown_frame_slot_is_refused(tree: Path, build: str) -> None:
    issues = package_issues(build, [_resolved(tree, "acme/gate-check")])

    assert any(i.startswith("ST6:") for i in issues), issues


@pytest.mark.parametrize("kind", ["command", "skill", "output", "slot"])
def test_st7_two_packages_claiming_the_same_name_are_refused(tmp_path: Path, kind: str) -> None:
    def manifest(name: str) -> dict[str, Any]:
        found: dict[str, Any] = {"name": name}
        if kind == "command":
            found["tool"] = {"project": "tool", "commands": {"shared": ["python"]}}
        if kind == "skill":
            found["skills"] = ["shared"]
        if kind == "output":
            found.update(requires={"o": "outputs/1"}, outputs={name: {"path": "/app/.raidar/x"}})
        return found

    def files(name: str) -> dict[str, str]:
        if kind == "command":
            return {
                "tool/pyproject.toml": "[project]\nname = 'x'\nversion = '1'\ndependencies = []\n"
            }
        return {"skills/shared/SKILL.md": "skill\n"} if kind == "skill" else {}

    parents = [tmp_path / "one", tmp_path / "two"]
    stages = []
    for parent, name in zip(parents, ("alpha", "beta"), strict=True):
        if kind == "slot":
            name = "same"
        folder = write_package(
            parent / "lab" / "stages" / ("a" if parent == parents[0] else "b"),
            manifest(name),
            files=files(name),
        )
        package = load_stage_package(folder)
        ns = "a" if parent == parents[0] else "b"
        stages.append(ResolvedStage(Release.parse(f"{ns}/{package.name}@1"), package, {}))
    slots = " ".join(f"%%%{n.upper()}%%%" for n in dict.fromkeys(s.leaf for s in stages))

    issues = package_issues(f"{slots} %%%TASK%%%", stages, lock_check=lambda tool: None)

    assert any(i.startswith("ST7:") and "claimed by a/" in i for i in issues), issues


def test_st6_a_package_named_task_cannot_take_the_reserved_slot(tmp_path: Path) -> None:
    stage = _own(tmp_path, {"name": "task"})

    issues = package_issues("%%%TASK%%%", [stage], lock_check=lambda tool: None)

    assert any(i.startswith("ST6:") and "reserved" in i for i in issues), issues


def test_st7_two_packages_returning_the_same_token_are_refused(tmp_path: Path) -> None:
    stages = [
        _own(tmp_path / name, {"name": name, "tokens": ["SHARED_TOKEN"]}) for name in ("one", "two")
    ]
    build = "%%%ONE%%% %%%TWO%%% %%%TASK%%%"

    issues = package_issues(build, stages, lock_check=lambda tool: None)

    assert any(i.startswith("ST7:") and "SHARED_TOKEN" in i for i in issues), issues

    distinct = [
        _own(tmp_path / "d1", {"name": "one", "tokens": ["FIRST"]}),
        _own(tmp_path / "d2", {"name": "two", "tokens": ["SECOND"]}),
    ]
    assert package_issues(build, distinct, lock_check=lambda tool: None) == []
