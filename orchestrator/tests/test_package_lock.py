"""The lock's package identity: arm packages and primitive code, proof, A4 and trial integrity."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from raidar_contract.canonical import digest_json
from raidar_contract.references import Release

from raidar.contract import source_imports
from raidar.contract.paths import releases_file, stage_checks_root, stages_root, workflows_root
from raidar.contract.source_imports import import_closure
from raidar.design.experiment.agent import ResolvedAgent
from raidar.design.experiment.arm import (
    ArmResolutionError,
    ResolvedArm,
    workflow_evidence,
)
from raidar.design.experiment.schema import AgentEntry
from raidar.subject.agent.definition import ReleasedAgent
from raidar.subject.agent.schema import AgentName
from raidar.subject.draft import draft_resolution
from raidar.subject.package.registry import load_stage_package
from raidar.subject.releases import released_agent, released_workflow
from raidar.subject.workflow import identity, package_checks
from raidar.subject.workflow.identity import (
    RUNNER_ID,
    package_primitives,
    primitive_code,
)
from raidar.subject.workflow.package_checks import ResolvedStage
from raidar.subject.workflow.package_runtime import (
    PackageEvidence,
    check_record,
    check_record_mismatch,
    evidence_issues,
    installed_mismatch,
    installed_trees,
    load_evidence,
)
from tests.package_support import STAGE_FIXTURES, fixture_package, note_state_package
from tests.release_support import release_tree

AGENT = "codex"
REAL_DIGEST = identity._digest.__wrapped__  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _fake_source_digest(monkeypatch: pytest.MonkeyPatch, stage_check_store: Path) -> None:
    """Primitive code digests the real source tree (slow tier); fast tests fold the inputs."""
    monkeypatch.setattr(
        identity,
        "_digest",
        lambda roots, adapter: digest_json({"roots": roots, "adapter": str(adapter)}),
    )


@pytest.fixture(scope="module")
def tree_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The registered gated-workflow tree, built once; tests copy its few small files."""
    base = tmp_path_factory.mktemp("lock-template")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("RAIDAR_VAR", str(base / "var"))
        patch.setattr(package_checks, "uv_lock_stale", lambda tool: None)
        root = release_tree(base / "repo")
        fixture_package("gate-check", stages_root(root))
        folder = workflows_root(root) / "acme" / "gated"
        folder.mkdir(parents=True)
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
            "acme/gate-check@1": load_stage_package(
                stages_root(root) / "acme" / "gate-check"
            ).digest
        }
        register["workflows"]["acme/gated@1"] = released_workflow(
            "acme/gated@1", repo_root=root, draft=True
        ).digest
        releases_file(root).write_text(yaml.safe_dump(register))
    return root


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tree_template: Path) -> Path:
    monkeypatch.setenv("RAIDAR_VAR", str(tmp_path / "var"))
    monkeypatch.setattr(package_checks, "uv_lock_stale", lambda tool: None)
    return shutil.copytree(tree_template, tmp_path / "repo")


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
    workflow = released_workflow("acme/gated@1", repo_root=tree)
    (stage,) = workflow.stages
    _write_proof(stage.package.digest)
    evidence = {stage.release.name: load_evidence(stage)}

    identity = _arm(tree, "acme/gated@1", evidence).identity()

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
    (stage,) = released_workflow("acme/gated@1", repo_root=tree).stages
    _write_proof(stage.package.digest, tree_digest="a" * 64)
    first = _arm(tree, "acme/gated@1", {"acme/gate-check": load_evidence(stage)}).identity()[
        "digest"
    ]
    _write_proof(stage.package.digest, tree_digest="b" * 64)
    second = _arm(tree, "acme/gated@1", {"acme/gate-check": load_evidence(stage)}).identity()[
        "digest"
    ]

    assert first != second


def test_an_arm_without_packages_is_identified_by_the_runtime_primitives_alone(tree: Path) -> None:
    identity = _arm(tree, "raidar/one-shot@1").identity()

    assert identity["packages"] == {}
    assert set(identity["primitive_code"]) == {  # type: ignore[arg-type]
        "frame/1",
        "install/1",
        "runner/1",
    }


def test_primitive_code_is_deterministic_adapter_bound_and_core_is_adapter_free() -> None:
    package = load_stage_package(STAGE_FIXTURES / "gate-check")
    contracts = {"gate/1"}

    codex = primitive_code(contracts, AgentName.CODEX)
    claude = primitive_code(contracts, AgentName.CLAUDE_CODE)

    assert codex == primitive_code(contracts, AgentName.CODEX)
    assert all(codex[key] != claude[key] for key in codex)
    core = package_primitives([package])
    assert set(core) == {"frame/1", "install/1", "gate/1"}
    assert not set(core.values()) & set(codex.values())


def test_a_primitive_digest_follows_source_syntax_and_the_adapter_not_comments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "primitive.py"
    monkeypatch.setattr(identity, "_inventory", lambda roots, adapter: {"p.py": source})
    digest = REAL_DIGEST

    source.write_text("x = 1\n")
    base = digest(("m",), None)
    source.write_text("# a comment\nx = 1\n")
    assert digest(("m",), None) == base
    source.write_text("x = 2\n")
    assert digest(("m",), None) != base


def test_the_inventory_holds_the_closure_files_adapter_entries_and_payload_scripts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source_imports, "import_closure", lambda module: frozenset({module}))

    bare = identity.primitive_inventory("gate/1", None)
    codex = identity.primitive_inventory("gate/1", AgentName.CODEX)

    assert "subject/primitive/gate.py" in bare
    assert "subject/agent/adapters/codex_cli.py" not in bare
    assert "subject/agent/adapters/codex_cli.py" in codex
    assert any(name.startswith("subject/primitive/payload/") for name in bare)
    assert identity.primitive_inventory(RUNNER_ID, None) != bare
    with pytest.raises(ValueError, match="unsupported primitive contract"):
        identity.primitive_inventory("nope/9", None)
    assert identity._inventory_key(identity.CONTRACT_DIR / "agent.py") == "raidar_contract/agent.py"
    assert source_imports.source_path("os") is None
    assert source_imports.source_path("raidar_contract") == identity.CONTRACT_DIR / "__init__.py"


# ---- proof the lock needs --------------------------------------------------------------------


def test_a_normal_arm_needs_tracked_stage_check_proof_without_a_var_fallback(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage_check_store: Path
) -> None:
    (stage,) = released_workflow("acme/gated@1", repo_root=tree).stages
    relocated = tmp_path / "relocated-var"
    monkeypatch.setenv("RAIDAR_VAR", str(relocated))
    assert stage_checks_root(tree) == tree / "lab" / "stage-checks"
    assert stage_checks_root() == stage_check_store
    legacy = relocated / "stage-checks"
    legacy.mkdir(parents=True)
    (legacy / f"{stage.package.digest}.installed.json").write_text(
        json.dumps({"arm64": {"tree_digest": "legacy", "wheels": []}})
    )
    (legacy / f"{stage.package.digest}.json").write_text('{"legacy": true}')
    assert check_record(stage.package.digest) is None

    missing = evidence_issues(stage, load_evidence(stage))
    assert missing
    assert "make stage-check NAME=acme/gate-check" in missing[0]

    _write_proof(stage.package.digest)
    assert evidence_issues(stage, load_evidence(stage)) == []
    assert installed_trees(stage.package.digest) == {"arm64": "t" * 64}

    (stage_check_store / f"{stage.package.digest}.installed.json").unlink()
    assert evidence_issues(stage, load_evidence(stage))
    assert installed_trees(stage.package.digest) == {}


def test_a_stop_needs_its_check_record_and_the_lock_binds_it(tmp_path: Path) -> None:
    from raidar_contract.references import Release

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
    workflow = released_workflow("acme/gated@1", repo_root=tree)

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


def test_the_import_closure_follows_nested_and_relative_imports_and_parent_packages() -> None:
    closure = import_closure("raidar.subject.primitive.gate")

    assert "raidar.subject.primitive.calls" in closure
    assert "raidar.subject.primitive" in closure  # the parent package's __init__
    assert "raidar" in closure


def test_a_package_with_a_stop_owes_a_stage_check_record_even_with_an_installed_tree() -> None:
    package = note_state_package()
    stage = ResolvedStage(
        Release.parse_stage("acme/note-state@1"), package, package.effective_params()
    )

    issues = evidence_issues(stage, PackageEvidence({"arm64": "t" * 64}, None))

    assert len(issues) == 1
    assert "no stage-check record" in issues[0]
    assert "make stage-check NAME=acme/note-state" in issues[0]


def _uv(monkeypatch: pytest.MonkeyPatch, outcome: object) -> None:
    def run(*args: object, **kwargs: object) -> object:
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(package_checks.subprocess, "run", run)


def _done(code: int, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["uv"], code, out, err)


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (_done(0), None),
        (_done(2, err="warn\nthe lock needs to be updated\n"), "the lock needs to be updated"),
        (_done(1, out="from stdout\n"), "from stdout"),
        (_done(3), "uv lock --check exited 3"),
        (OSError("no uv"), "uv lock --check could not run: no uv"),
    ],
    ids=["current", "stderr", "stdout", "silent", "missing-uv"],
)
def test_uv_lock_stale_reports_why_the_lock_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outcome: object, expected: str | None
) -> None:
    _uv(monkeypatch, outcome)

    assert package_checks.uv_lock_stale(tmp_path) == expected


def test_a_uv_that_times_out_is_a_stale_lock_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _uv(monkeypatch, subprocess.TimeoutExpired(["uv"], 120))

    reason = package_checks.uv_lock_stale(tmp_path)

    assert reason is not None
    assert reason.startswith("uv lock --check could not run")
