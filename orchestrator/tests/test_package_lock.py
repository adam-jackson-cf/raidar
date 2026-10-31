"""The lock's package identity: arm packages and primitive code, proof, A4 and trial integrity."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from raidar_contract.agent import AgentName
from raidar_contract.package.registry import load_stage_package

from raidar.contract.paths import releases_file, stage_checks_root, stages_root, workflows_root
from raidar.design.experiment.agent import ResolvedAgent
from raidar.design.experiment.arm import (
    ArmResolutionError,
    ResolvedArm,
    workflow_evidence,
)
from raidar.design.experiment.schema import AgentEntry
from raidar.subject.draft import draft_resolution
from raidar.subject.primitive.registry import PRIMITIVE_MODULES
from raidar.subject.releases import ReleasedAgent, released_agent, released_workflow
from raidar.subject.workflow import package_checks
from raidar.subject.workflow.identity import (
    ADAPTER_ENTRIES,
    RAIDAR_DIR,
    RUNNER_ID,
    import_closure,
    package_primitives,
    primitive_code,
    primitive_inventory,
)
from raidar.subject.workflow.package_runtime import (
    PackageEvidence,
    check_record,
    check_record_mismatch,
    evidence_issues,
    installed_mismatch,
    installed_trees,
    load_evidence,
)
from tests.package_support import fixture_package
from tests.release_support import release_tree

AGENT = "codex"


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("RAIDAR_VAR", str(tmp_path / "var"))
    monkeypatch.setattr(package_checks, "uv_lock_stale", lambda tool: None)
    root = release_tree(tmp_path / "repo")
    fixture_package("gate-check", stages_root(root))
    folder = workflows_root(root) / "gated"
    folder.mkdir()
    (folder / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "schema": 2,
                "name": "gated",
                "build": {"prompt": "%%%GATE_CHECK%%% %%%TASK%%%"},
                "stages": [{"use": "acme/gate-check@1"}],
            }
        )
    )
    register = yaml.safe_load(releases_file(root).read_text())
    register["stages"] = {
        "acme/gate-check@1": load_stage_package(stages_root(root) / "acme" / "gate-check").digest
    }
    register["workflows"]["gated@1"] = released_workflow(
        "gated@1", repo_root=root, draft=True
    ).digest
    releases_file(root).write_text(yaml.safe_dump(register))
    return root


def _arm(
    root: Path, workflow: str, evidence: dict[str, PackageEvidence] | None = None
) -> ResolvedArm:
    released: ReleasedAgent = released_agent("codex@1", repo_root=root)
    models = released.definition.models
    model = next(iter(models))
    reasoning = next(iter(models[model].reasoning), None)
    agent = ResolvedAgent(
        AgentEntry(id="a", release="codex@1", model=model, reasoning=reasoning), released
    )
    return ResolvedArm("a", agent, released_workflow(workflow, repo_root=root), evidence or {})


def _write_proof(digest: str, *, arch: str = "arm64", tree_digest: str = "t" * 64) -> None:
    root = stage_checks_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{digest}.installed.json").write_text(
        json.dumps({arch: {"tree_digest": tree_digest, "wheels": []}})
    )


# ---- the arm's identity ----------------------------------------------------------------------


def test_a_package_arm_records_packages_and_primitive_code_never_stage_code(tree: Path) -> None:
    workflow = released_workflow("gated@1", repo_root=tree)
    (stage,) = workflow.stages
    _write_proof(stage.package.digest)
    evidence = {stage.release.name: load_evidence(stage)}

    identity = _arm(tree, "gated@1", evidence).identity()

    assert "stage_code" not in identity
    assert identity["workflow_sha256"] == workflow.digest
    assert identity["packages"] == {
        "acme/gate-check": {
            "release": "acme/gate-check@1",
            "sha256": stage.package.digest,
            "installed": {"arm64": "t" * 64},
        }
    }
    codes = identity["primitive_code"]
    assert isinstance(codes, dict)
    assert set(codes) == {"frame/1", "install/1", "gate/1", RUNNER_ID}


def test_the_proof_is_part_of_the_arm_digest(tree: Path) -> None:
    (stage,) = released_workflow("gated@1", repo_root=tree).stages
    _write_proof(stage.package.digest, tree_digest="a" * 64)
    first = _arm(tree, "gated@1", {"acme/gate-check": load_evidence(stage)}).identity()["digest"]
    _write_proof(stage.package.digest, tree_digest="b" * 64)
    second = _arm(tree, "gated@1", {"acme/gate-check": load_evidence(stage)}).identity()["digest"]

    assert first != second


def test_a_spike_arm_has_no_packages_and_is_identified_by_stage_type(tree: Path) -> None:
    identity = _arm(tree, "one-shot@1").identity()

    assert identity["packages"] == {}
    assert set(identity["primitive_code"]) == {"build"}  # type: ignore[arg-type]


def test_primitive_code_is_deterministic_adapter_bound_and_core_is_adapter_free(tree: Path) -> None:
    (stage,) = released_workflow("gated@1", repo_root=tree).stages
    contracts = {"gate/1"}

    codex = primitive_code(contracts, AgentName.CODEX)
    claude = primitive_code(contracts, AgentName.CLAUDE_CODE)

    assert codex == primitive_code(contracts, AgentName.CODEX)
    assert all(codex[key] != claude[key] for key in codex)
    core = package_primitives([stage.package])
    assert set(core) == {"frame/1", "install/1", "gate/1"}
    assert not set(core.values()) & set(codex.values())


# ---- proof the lock needs --------------------------------------------------------------------


def test_a_normal_arm_needs_the_stage_check_proof_a_draft_may_lack_it(tree: Path) -> None:
    (stage,) = released_workflow("gated@1", repo_root=tree).stages

    missing = evidence_issues(stage, load_evidence(stage))
    assert missing
    assert "make stage-check NAME=acme/gate-check" in missing[0]

    _write_proof(stage.package.digest)
    assert evidence_issues(stage, load_evidence(stage)) == []
    assert installed_trees(stage.package.digest) == {"arm64": "t" * 64}


def test_a_stop_needs_its_check_record_and_the_lock_binds_it(tmp_path: Path) -> None:
    from raidar_contract.releases import Release

    from raidar.subject.workflow.package_checks import ResolvedStage

    package = load_stage_package(fixture_package("note-state", tmp_path))
    stage = ResolvedStage(
        Release.parse_stage("acme/note-state@1"), package, package.effective_params()
    )
    root = tmp_path / "proof"
    root.mkdir()
    assert load_evidence(stage, root).check is None
    (root / f"{package.digest}.json").write_text(json.dumps({"a": 1}))

    bound = load_evidence(stage, root)
    assert bound.check is not None
    assert bound.entry()["check"] == bound.check
    assert check_record(package.digest, root) == {"a": 1}
    assert check_record_mismatch(bound.entry(), {"a": 1}) is None
    assert "differs" in str(check_record_mismatch(bound.entry(), {"a": 2}))
    assert "missing" in str(check_record_mismatch(bound.entry(), None))


def test_a_normal_arm_without_proof_is_refused_and_a_draft_records_it_absent(tree: Path) -> None:
    workflow = released_workflow("gated@1", repo_root=tree)

    with pytest.raises(ArmResolutionError, match="make stage-check NAME=acme/gate-check"):
        workflow_evidence(workflow)
    with draft_resolution():
        assert workflow_evidence(workflow)["acme/gate-check"].installed == {}


# ---- trial integrity: installed tree and check record against the lock ------------------------


def test_the_installed_tree_must_equal_the_locked_digest_for_its_architecture() -> None:
    locked = {"installed": {"arm64": "a" * 64}}

    assert installed_mismatch(locked, "arm64", "a" * 64) is None
    assert "differs from the locked" in str(installed_mismatch(locked, "arm64", "b" * 64))
    assert "none for amd64" in str(installed_mismatch(locked, "amd64", "a" * 64))
    assert installed_mismatch({"installed": {}}, "arm64", "x") is None  # a draft lock


# ---- A4: each primitive's import closure lies inside its identity inventory --------------------


def _runtime_files(modules: list[str]) -> set[str]:
    """``raidar`` files really loaded by importing ``modules`` in a fresh interpreter."""
    code = (
        "import importlib, json, sys\n"
        f"[importlib.import_module(m) for m in {modules!r}]\n"
        "print(json.dumps(sorted(n for n in sys.modules "
        "if n == 'raidar' or n.startswith('raidar.'))))\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    files = set()
    for name in json.loads(done.stdout):
        base = RAIDAR_DIR.parent / name.replace(".", "/")
        path = (
            base.with_suffix(".py") if base.with_suffix(".py").is_file() else base / "__init__.py"
        )
        if path.is_file():
            files.add(path.relative_to(RAIDAR_DIR).as_posix())
    return files


@pytest.mark.parametrize("contract", sorted(PRIMITIVE_MODULES))
def test_each_primitives_runtime_imports_lie_inside_its_inventory(contract: str) -> None:
    loaded = _runtime_files([PRIMITIVE_MODULES[contract]])

    for adapter in AgentName:
        assert loaded <= set(primitive_inventory(contract, adapter)), (
            f"{contract} imports outside its identity inventory: "
            f"{sorted(loaded - set(primitive_inventory(contract, adapter)))}"
        )


def test_the_package_runner_and_each_native_adapter_are_inside_the_inventory() -> None:
    runner = _runtime_files(["raidar.subject.workflow.package", "raidar.subject.workflow.runner"])
    for adapter, entries in ADAPTER_ENTRIES.items():
        loaded = _runtime_files([*entries])
        inventory = set(primitive_inventory(RUNNER_ID, adapter)) | set(
            primitive_inventory("frame/1", adapter)
        )
        assert runner <= set(primitive_inventory(RUNNER_ID, adapter))
        assert loaded <= inventory, sorted(loaded - inventory)


def test_the_import_closure_follows_nested_and_relative_imports_and_parent_packages() -> None:
    closure = import_closure("raidar.subject.primitive.gate")

    assert "raidar.subject.primitive.calls" in closure
    assert "raidar.subject.primitive" in closure  # the parent package's __init__
    assert "raidar" in closure
