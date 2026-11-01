from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RAIDAR = "orchestrator/src/raidar"

pytestmark = pytest.mark.quality


def _load_structure_module():
    script_path = REPO_ROOT / "scripts" / "checks" / "check-structure.py"
    spec = importlib.util.spec_from_file_location("check_structure", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


structure = _load_structure_module()


@pytest.mark.parametrize(
    "path",
    [
        "orchestrator/src/raidar/scoring/framework/scorer_v10/scorer.py",
        "orchestrator/tests/test_code_quality_v3.py",
        "lab/experiments/smoke/python__forge__mechanics-v1/experiment.yaml",
        "orchestrator/src/raidar/subject/workflow/runner_v2.py",
        "scripts/v2/run.sh",
        "orchestrator/src/raidar/scoring/framework/v3/x.py",
    ],
)
def test_version_suffixes_and_segments_are_refused(path: str) -> None:
    assert structure.version_violations([path]) == [f"version in path name: {path}"]


@pytest.mark.parametrize(
    "path",
    [
        "orchestrator/src/raidar/scoring/code_quality/scorer.py",
        "orchestrator/src/raidar/grading/vector.py",
        "lab/experiments/smoke/python__forge__mechanics/experiment.yaml",
        "docs/reboot/mechanics-v1-design.md",
    ],
)
def test_unversioned_names_pass(path: str) -> None:
    assert structure.version_violations([path]) == []


def test_a_version_segment_is_refused_even_with_data_below_it() -> None:
    path = "orchestrator/src/raidar/scoring/framework/v2/data.json"
    assert structure.version_violations([path]) == [f"version in path name: {path}"]


def test_a_version_folder_in_lab_data_is_refused() -> None:
    path = "lab/workflows/raidar/one-shot/v2"
    assert structure.version_violations([path]) == [f"version in path name: {path}"]


def test_root_allowlist_names_unexpected_directories_and_files() -> None:
    paths = [
        "Makefile",
        "README.md",
        "codereview.md",
        "lab/experiments/a.yaml",
        "orchestrator/pyproject.toml",
        "experiments/a.yaml",
        "jobs/x/job.json",
        "notes.txt",
    ]

    assert structure.root_violations(paths) == [
        "unexpected top-level directory: experiments/",
        "unexpected top-level directory: jobs/",
        "unexpected root file: notes.txt",
    ]


def test_raidar_packages_must_be_on_the_allowlist() -> None:
    paths = [
        f"{RAIDAR}/__init__.py",
        f"{RAIDAR}/scoring/__init__.py",
        f"{RAIDAR}/commands/run.py",
        f"{RAIDAR}/harness/definitions.py",
        f"{RAIDAR}/scorers/correctness/scorer.py",
    ]

    assert structure.package_violations(paths) == [
        "unexpected raidar package: commands/",
        "unexpected raidar package: harness/",
        "unexpected raidar package: scorers/",
    ]


def test_every_group_allows_only_its_own_subpackages() -> None:
    allowed = [
        f"{RAIDAR}/design/experiment/lock.py",
        f"{RAIDAR}/design/problem/store.py",
        f"{RAIDAR}/subject/workflow/inner.py",
        f"{RAIDAR}/engine/trial/driver.py",
        f"{RAIDAR}/engine/job/run.py",
        f"{RAIDAR}/engine/health/audit.py",
        f"{RAIDAR}/scoring/judge/audit.py",
        f"{RAIDAR}/evaluation/review/projection/spans.py",
        f"{RAIDAR}/grading/runtime/plugin.py",
        f"{RAIDAR}/engine/__init__.py",
    ]
    refused = [
        f"{RAIDAR}/engine/execution/run.py",
        f"{RAIDAR}/scoring/sdk/result.py",
        f"{RAIDAR}/contract/paths/more.py",
        f"{RAIDAR}/cli/sub/command.py",
        f"{RAIDAR}/evaluation/jobs/run.py",
    ]

    assert structure.package_violations(allowed) == []
    assert structure.package_violations(refused) == [
        "unexpected cli subpackage: cli/sub/",
        "unexpected contract subpackage: contract/paths/",
        "unexpected engine subpackage: engine/execution/",
        "unexpected evaluation subpackage: evaluation/jobs/",
        "unexpected scoring subpackage: scoring/sdk/",
    ]


def test_package_depth_is_capped_at_three_below_raidar() -> None:
    allowed = f"{RAIDAR}/scoring/code_quality/snapshot_tool/module.py"
    too_deep = f"{RAIDAR}/scoring/code_quality/snapshot_tool/graph/nodes.py"

    assert structure.package_violations([allowed]) == []
    assert structure.package_violations([too_deep]) == [
        "package nests deeper than 3 below raidar/: "
        f"{RAIDAR}/scoring/code_quality/snapshot_tool/graph"
    ]


def test_no_data_tree_is_exempt_from_the_depth_cap() -> None:
    deep = f"{RAIDAR}/subject/workflow/stages/rules/ruff/file.toml"

    assert structure.package_violations([deep]) == [
        f"package nests deeper than 3 below raidar/: {RAIDAR}/subject/workflow/stages/rules/ruff",
    ]


def test_lab_holds_only_the_allowlisted_data_trees_and_the_release_register() -> None:
    allowed = [
        "lab/releases.yaml",
        "lab/experiments/smoke/x/experiment.yaml",
        "lab/problems/sources.yaml",
        "lab/ledger/faults/FCR-001.md",
        "lab/agents/codex.yaml",
        "lab/workflows/raidar/one-shot/workflow.yaml",
        "lab/environments/python/pack.yaml",
        "lab/judge/policies/code-quality.rubric-smells.yaml",
    ]
    refused = ["lab/components/rules/AGENTS.md", "lab/notes.md", "lab/releases.yaml/x.yaml"]

    assert structure.lab_violations(allowed) == []
    assert structure.lab_violations(refused) == [
        "unexpected lab entry: lab/components",
        "unexpected lab entry: lab/notes.md",
        "unexpected lab entry: lab/releases.yaml",
    ]


def test_a_workflow_lives_at_namespace_name_and_holds_its_definition() -> None:
    allowed = [
        "lab/workflows/raidar/one-shot/workflow.yaml",
        "lab/workflows/raidar/define-build-verify/skills/a/SKILL.md",
        "lab/workflows/raidar/define-build-verify/workflow.yaml",
    ]
    refused = [
        "lab/workflows/one-shot/workflow.yaml",
        "lab/workflows/raidar/stray.md",
        "lab/workflows/acme/empty/notes.md",
    ]

    assert structure.workflow_violations(allowed) == []
    assert structure.workflow_violations(refused) == [
        "workflow folder without workflow.yaml: lab/workflows/acme/empty",
        "workflow outside lab/workflows/<ns>/<name>/: lab/workflows/one-shot/workflow.yaml",
        "workflow outside lab/workflows/<ns>/<name>/: lab/workflows/raidar/stray.md",
    ]


def test_repo_paths_list_tracked_and_unignored_files_but_not_ignored_ones(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text("/var/\n", encoding="utf-8")
    (tmp_path / "var").mkdir()
    (tmp_path / "var" / "jobs.json").write_text("{}", encoding="utf-8")
    (tmp_path / "stray").mkdir()
    (tmp_path / "stray" / "a.txt").write_text("", encoding="utf-8")

    assert structure.repo_paths(tmp_path) == [".gitignore", "stray/a.txt"]
    assert structure.violations(structure.repo_paths(tmp_path)) == [
        "unexpected top-level directory: stray/"
    ]


def test_main_exits_zero_on_a_clean_layout_and_one_on_a_violation(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "lab" / "judge").mkdir(parents=True)
    (tmp_path / "lab" / "judge" / "a.yaml").write_text("", encoding="utf-8")

    assert structure.main(["--repo-root", str(tmp_path)]) == 0

    (tmp_path / "lab" / "judge" / "thing_v2.yaml").write_text("", encoding="utf-8")

    assert structure.main(["--repo-root", str(tmp_path)]) == 1


def test_this_repository_has_no_legacy_top_level_directories() -> None:
    top_level = {path.split("/", 1)[0] for path in structure.repo_paths(REPO_ROOT) if "/" in path}

    assert top_level <= structure.ROOT_DIRECTORIES


def _stage_package(tmp_path: Path) -> Path:
    package = tmp_path / "lab" / "stages" / "team" / "note-state"
    shutil.copytree(Path(__file__).parent / "fixtures" / "stages" / "acme" / "note-state", package)
    return package


def _stage_paths(root: Path) -> list[str]:
    return [path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()]


def test_stage_structure_accepts_namespaced_packages_with_authored_tools(tmp_path: Path) -> None:
    _stage_package(tmp_path)
    assert structure.stage_violations(_stage_paths(tmp_path), tmp_path) == []


def test_stage_structure_refuses_files_outside_a_declared_package(tmp_path: Path) -> None:
    assert structure.stage_violations(["lab/stages/loose.py"], tmp_path) == [
        "stage file outside a declared package: lab/stages/loose.py"
    ]


@pytest.mark.parametrize("layout", ["flat", "too-deep"], ids=["no-namespace", "three-levels"])
def test_stage_structure_refuses_packages_that_are_not_exactly_two_levels(
    tmp_path: Path, layout: str
) -> None:
    source = Path(__file__).parent / "fixtures" / "stages" / "acme" / "note-state"
    relative = "note-state" if layout == "flat" else "team/inner/note-state"
    shutil.copytree(source, tmp_path / "lab" / "stages" / relative)

    issues = structure.stage_violations(_stage_paths(tmp_path), tmp_path)

    assert issues
    assert all(issue.startswith("stage file outside a declared package:") for issue in issues)


@pytest.mark.parametrize("mutation", ["extra", "missing_prompt", "symlink"])
def test_stage_structure_rejects_malformed_package_layouts(tmp_path: Path, mutation: str) -> None:
    package = _stage_package(tmp_path)
    if mutation == "extra":
        (package / "extra.py").write_text("pass\n")
    elif mutation == "missing_prompt":
        (package / "prompt.md").unlink()
    else:
        prompt = package / "prompt.md"
        prompt.unlink()
        prompt.symlink_to(package / "instructions.md")
    issues = structure.stage_violations(_stage_paths(tmp_path), tmp_path)
    assert len(issues) == 1
    assert "invalid stage package lab/stages/team/note-state:" in issues[0]
