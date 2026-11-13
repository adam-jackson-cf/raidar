"""Fast decisions of the repository check and release scripts, built in memory.

Covers execution-boundary rules (V2/V5: new install paths and unrecorded container transports),
the layout rules of ``check-structure.py``, the import accounting of ``check-python-fanout.py``
and the commit/pyproject decisions of ``bump-version.py``. Real git, uv and make seams live in
the quality and slow files of the same scripts.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _load_script(relative: str, name: str):
    script = Path(__file__).resolve().parents[2] / relative
    spec = importlib.util.spec_from_file_location(name, script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rules = _load_script("scripts/checks/check-python-quality.py", "execution_boundary_rules")
structure = _load_script("scripts/checks/check-structure.py", "fast_check_structure")
fanout = _load_script("scripts/checks/check-python-fanout.py", "fast_check_python_fanout")
bump = _load_script("scripts/release/bump-version.py", "fast_bump_version")
NOTE_STATE = Path(__file__).parent / "fixtures" / "stages" / "acme" / "note-state"
RAIDAR = "orchestrator/src/raidar"
PREFIX = "orchestrator/src/raidar/"


@pytest.mark.parametrize(
    "source",
    [
        'command = "uv pip install example"',
        'command = "python -m pip install example"',
        'argv = ["uv", "sync", "--offline"]',
        'argv = ["pip", "install", "example"]',
    ],
)
def test_install_commands_are_refused_outside_owned_installers(source: str) -> None:
    found = rules.execution_boundary_diagnostics_for_source(
        source, PREFIX + "subject/workflow/new.py"
    )
    assert {item.code for item in found} == {"PYQ300"}


def test_install_ownership_does_not_ban_documentation_or_trusted_installer() -> None:
    prose = '"""Use uv pip install inside the container."""'
    assert (
        rules.execution_boundary_diagnostics_for_source(prose, PREFIX + "subject/workflow/new.py")
        == []
    )
    assert (
        rules.execution_boundary_diagnostics_for_source(
            'command = "uv pip install --offline example"', PREFIX + "subject/primitive/install.py"
        )
        == []
    )


@pytest.mark.parametrize("operation", ["exec", "upload_file"])
@pytest.mark.parametrize("module", ["subject/workflow/new.py", "subject/primitive/new.py"])
def test_container_transport_cannot_bypass_call_boundary(operation: str, module: str) -> None:
    source = f"async def run(environment):\n    await environment.{operation}('payload')\n"
    found = rules.execution_boundary_diagnostics_for_source(source, PREFIX + module)
    assert [(item.code, item.line) for item in found] == [("PYQ301", 2)]


def test_call_boundary_and_its_consumers_are_allowed() -> None:
    assert (
        rules.execution_boundary_diagnostics_for_source(
            "await environment.exec(command)", PREFIX + "subject/primitive/calls.py"
        )
        == []
    )
    assert (
        rules.execution_boundary_diagnostics_for_source(
            "await calls.upload_file(environment, source, target)",
            PREFIX + "subject/workflow/new.py",
        )
        == []
    )
    assert (
        rules.execution_boundary_diagnostics_for_source(
            "await calls.trusted_exec(environment, command)", PREFIX + "subject/workflow/new.py"
        )
        == []
    )


@pytest.mark.parametrize(
    "prefix",
    [
        "orchestrator/tests/fixtures/stages/acme/third-party-check/tool/src/",
        "lab/stages/third-party/docs-check/tool/src/",
        "lab/stages/helpers/common/tool/src/",
    ],
)
def test_stage_data_names_do_not_relax_python_module_names(prefix: str) -> None:
    assert rules.structure_diagnostics_for_relpath(prefix + "checker/main.py") == []
    found = rules.structure_diagnostics_for_relpath(prefix + "bad-module/main.py")
    assert [item.code for item in found] == ["PYQ105"]
    found = rules.structure_diagnostics_for_relpath(prefix + "helpers/main.py")
    assert [item.code for item in found] == ["PYQ103"]


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess\nclass Other:\n    def run_or_raise(self):\n"
        "        subprocess.run([])\n",
        "import subprocess\ndef run_or_raise():\n    def nested():\n        subprocess.run([])\n",
    ],
)
def test_process_approval_does_not_leak_to_same_named_methods_or_nested_functions(
    source: str,
) -> None:
    found = rules.class_interface_diagnostics_for_source(source, PREFIX + "cli/process.py")
    assert [item.code for item in found] == ["PYQ220"]


def test_process_import_aliases_are_resolved_in_their_own_function_scope() -> None:
    source = (
        "def run_or_raise():\n"
        "    import subprocess as process\n"
        "    process.run([])\n"
        "def unrelated():\n"
        "    import widget as process\n"
        "    process.run([])\n"
    )
    assert rules.class_interface_diagnostics_for_source(source, PREFIX + "cli/process.py") == []


def test_json_data_annotation_approval_is_exact_not_a_collaborator_escape() -> None:
    imports = "from typing import Any\nfrom collections.abc import Mapping\n"
    path = PREFIX + "scoring/framework/context.py"
    data = imports + "class AssessContext:\n    config: Mapping[str, Any]\n"
    assert rules.class_interface_diagnostics_for_source(data, path) == []
    for changed in (
        data.replace("config:", "collaborator:"),
        data.replace("Mapping[str, Any]", "list[Any]"),
    ):
        found = rules.class_interface_diagnostics_for_source(changed, path)
        assert [item.code for item in found] == ["PYQ231"]


def test_named_contract_aliases_do_not_hide_any_inside_quoted_nested_annotations() -> None:
    source = (
        "from typing import Any\n"
        "Payload = list['Any']\n"
        "class Boundary:\n"
        "    collaborator: 'Payload'\n"
        "    def bind(self, other: Payload):\n"
        "        local_json: dict[str, Any] = {}\n"
    )
    found = rules.class_interface_diagnostics_for_source(
        source, PREFIX + "subject/agent/protocol.py"
    )
    assert [(item.code, item.line) for item in found] == [("PYQ231", 4), ("PYQ231", 5)]


@pytest.mark.parametrize(
    "path",
    [
        "orchestrator/src/raidar/scoring/framework/scorer_v10/scorer.py",
        "orchestrator/tests/test_code_quality_v3.py",
        "lab/experiments/smoke/python__forge__mechanics-v1/experiment.yaml",
        "orchestrator/src/raidar/subject/workflow/runner_v2.py",
        "scripts/v2/run.sh",
        "orchestrator/src/raidar/scoring/framework/v3/x.py",
        "orchestrator/src/raidar/scoring/framework/v2/data.json",
        "lab/workflows/raidar/one-shot/v2",
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


def test_root_allowlist_names_unexpected_directories_and_files() -> None:
    paths = [
        "Makefile",
        "README.md",
        ".python-version",
        "docs/reboot/evidence/codereview-2026-10-24.md",
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


def _stage_package(tmp_path: Path) -> Path:
    package = tmp_path / "lab" / "stages" / "team" / "note-state"
    shutil.copytree(NOTE_STATE, package)
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
    relative = "note-state" if layout == "flat" else "team/inner/note-state"
    shutil.copytree(NOTE_STATE, tmp_path / "lab" / "stages" / relative)

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


def _python_file(relpath: str, module: str):
    return fanout.PythonFile(path=Path(relpath), relpath=relpath, module=module, is_package=False)


def test_counts_submodule_imports_without_hiding_grouped_imports() -> None:
    current = _python_file("orchestrator/src/raidar/sample/pipeline.py", "raidar.sample.pipeline")
    known_modules = {
        "raidar",
        "raidar.sample",
        "raidar.sample.artifacts",
        "raidar.sample.workspace",
        "raidar.sample.pipeline",
    }

    imports = fanout.imported_modules(
        "from raidar.sample import artifacts, workspace\n",
        current,
        root_package="raidar",
        known_modules=known_modules,
    )

    assert imports == {"raidar.sample.artifacts", "raidar.sample.workspace"}


def test_architectural_dependencies_exclude_stdlib_but_keep_third_party() -> None:
    current = _python_file("orchestrator/src/raidar/module.py", "raidar.module")

    imports = fanout.imported_modules(
        "import json\nfrom click.testing import CliRunner\n",
        current,
        root_package="raidar",
        known_modules={"raidar", "raidar.module"},
    )

    assert imports == {"click.testing", "json"}
    assert fanout.architectural_dependencies(imports) == {"click.testing"}


def test_source_nloc_ignores_blank_lines_and_comments() -> None:
    assert fanout.source_nloc("\n# comment\nx = 1\n\n    # indented comment\n") == 1


@pytest.mark.parametrize(
    "commit",
    [
        "feat!: change CLI contract",
        "feat(api)!: change CLI contract",
        "feat: change CLI contract\n\nBREAKING CHANGE: old flag removed",
    ],
)
def test_analyze_commits_treats_conventional_breaking_changes_as_major(commit: str) -> None:
    bump_type, categorized = bump.analyze_commits([commit])

    assert bump_type == "major"
    assert categorized == [{"type": "feat", "raw": commit.splitlines()[0]}]


def test_get_commits_since_last_bump_keeps_bodies_and_stops_at_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "feat: change CLI contract\n"
                "\n"
                "BREAKING CHANGE: old flag removed"
                "\x1e"
                "fix: repair cache"
                "\x1e"
                "chore(release): 0.11.1"
                "\x1e"
                "feat: older change"
                "\x1e"
            ),
        )

    monkeypatch.setattr(bump.subprocess, "run", fake_run)

    assert bump.get_commits_since_last_bump() == [
        "feat: change CLI contract\n\nBREAKING CHANGE: old flag removed",
        "fix: repair cache",
    ]


def test_update_pyproject_only_rewrites_project_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "\n".join(
            [
                "[project]",
                'name = "sample"',
                'version = "1.2.3"',
                "",
                "[tool.sample]",
                'version = "9.9.9"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bump, "PYPROJECT_PATH", pyproject)

    bump.update_pyproject("2.0.0")

    assert pyproject.read_text(encoding="utf-8") == (
        '[project]\nname = "sample"\nversion = "2.0.0"\n\n[tool.sample]\nversion = "9.9.9"\n'
    )


def test_failed_lock_verification_leaves_all_release_files_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    lockfile = tmp_path / "uv.lock"
    changelog = tmp_path / "CHANGELOG.md"
    originals = {
        pyproject: '[project]\nname = "sample"\nversion = "1.2.3"\n',
        lockfile: "old lock\n",
        changelog: "# Changelog\n",
    }
    for path, contents in originals.items():
        path.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(bump, "PYPROJECT_PATH", pyproject)
    monkeypatch.setattr(bump, "LOCKFILE_PATH", lockfile)
    monkeypatch.setattr(bump, "CHANGELOG_PATH", changelog)
    monkeypatch.setattr(bump, "get_commits_since_last_bump", lambda: ["feat: add"])
    monkeypatch.setattr(
        bump,
        "git_output",
        lambda *args: "2026-11-09" if args[0] == "show" else "matching-revision",
    )
    monkeypatch.setattr(sys, "argv", ["bump-version.py"])

    def resolve_then_fail_verification(command, **_kwargs):
        staging = Path(command[command.index("--project") + 1])
        if "--check" in command:
            raise subprocess.CalledProcessError(1, command)
        (staging / "uv.lock").write_text("resolved lock\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(bump.subprocess, "run", resolve_then_fail_verification)

    with pytest.raises(subprocess.CalledProcessError):
        bump.main()

    assert all(path.read_text(encoding="utf-8") == contents for path, contents in originals.items())
