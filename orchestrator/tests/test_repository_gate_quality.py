"""Real-process canaries for the commit hook entry and path diagnostics."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Any, cast

import pytest
import yaml

pytestmark = pytest.mark.quality

ROOT = Path(__file__).resolve().parents[2]


def _hook_entry_prefix() -> list[str]:
    """The repository-gate hook entry without its trailing `make gate-commit`."""
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hooks = [hook for repo in config["repos"] for hook in repo["hooks"]]
    entry = shlex.split(next(hook["entry"] for hook in hooks if hook["id"] == "repository-gate"))
    assert entry[-2:] == ["make", "gate-commit"]
    return entry[:-2]


def test_hook_entry_keeps_linked_worktree_repository_non_bare(tmp_path: Path) -> None:
    environment = os.environ.copy()
    for variable in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_PREFIX",
    ):
        environment.pop(variable, None)

    def git(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    repository = tmp_path / "main"
    repository.mkdir()
    assert git("init", "-q", cwd=repository).returncode == 0
    for key, value in (
        ("user.name", "Repository Gate Canary"),
        ("user.email", "gate@example.invalid"),
        ("commit.gpgsign", "false"),
        ("core.hooksPath", str(repository / ".git/hooks")),
    ):
        assert git("config", key, value, cwd=repository).returncode == 0
    initial = git("commit", "--allow-empty", "--no-verify", "-m", "Initial", cwd=repository)
    assert initial.returncode == 0, initial.stderr
    worktree = tmp_path / "linked"
    added = git("worktree", "add", "-b", "linked", str(worktree), cwd=repository)
    assert added.returncode == 0, added.stderr

    scratch_init = (
        "import os, subprocess, tempfile\n"
        "with tempfile.TemporaryDirectory() as scratch:\n"
        "    os.chdir(scratch)\n"
        "    subprocess.run(['git', 'init', '-q', '--bare'], check=True, timeout=10)\n"
    )
    hook = repository / ".git/hooks/pre-commit"
    hook.write_text(
        "#!/bin/sh\nexec "
        + shlex.join([*_hook_entry_prefix(), sys.executable, "-c", scratch_init])
        + "\n"
    )
    hook.chmod(0o755)
    (worktree / "tracked.txt").write_text("Worktree commit exercises the gate hook.\n")
    assert git("add", "tracked.txt", cwd=worktree).returncode == 0

    committed = git("commit", "-m", "Run repository gate", cwd=worktree)
    bare = git(
        "--git-dir", str(repository / ".git"), "config", "--get", "core.bare", cwd=repository
    )

    assert bare.returncode == 0, bare.stderr
    assert bare.stdout.strip() == "false", committed.stderr
    assert committed.returncode == 0, committed.stderr


def test_path_check_accepts_a_clean_owned_path() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/checks/check-repository-paths.py"), "README.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_path_check_refuses_a_misplaced_file_with_one_actionable_line() -> None:
    # This canary must be at the root to exercise real folder-placement policy.
    relative = "gate-canary.txt"
    candidate = ROOT / relative
    # A random basename can itself violate the independent version-name rule.
    with candidate.open("x", encoding="utf-8") as stream:
        stream.write("misplaced file\n")
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/checks/check-repository-paths.py"), relative],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    finally:
        candidate.unlink()

    assert result.returncode == 1, result.stderr
    assert result.stdout == ""
    lines = result.stderr.splitlines()
    assert len(lines) == 1
    assert re.fullmatch(
        rf"{re.escape(relative)}: STRUCT\.folder-placement: [^\r\n;]+; [^\r\n]+", lines[0]
    )


@pytest.mark.parametrize("selection", [[], ["unowned"], ["unowned/probe.py"]])
def test_python_cli_rejects_unknown_roots(tmp_path: Path, selection: list[str]) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=10)
    candidate = tmp_path / "unowned/probe.py"
    candidate.parent.mkdir()
    candidate.write_text("VALUE = 1\n")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/checks/check-python-quality.py"),
            "--repo-root",
            str(tmp_path),
            *selection,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert re.fullmatch(r"unowned/probe.py:\d+: PYQ100 [^\r\n;]+; [^\r\n]+\n", result.stderr)


def _inventory_repository(tmp_path: Path) -> tuple[Path, list[str]]:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True, timeout=10)
    (repository / ".gitignore").write_text("ignored/\n")
    (repository / "CODEOWNERS").write_text("* @owner\n")
    outside = tmp_path / "outside.py"
    outside.write_text("def broken(:\n")
    ignored = repository / "orchestrator/src/raidar/scoring/ignored"
    ignored.mkdir(parents=True)
    (ignored / "link.py").symlink_to(outside)
    (ignored / "__init__.py").write_text("def broken(:\n")
    paths = [
        f"orchestrator/src/raidar/scoring/{name}/__init__.py" for name in ("tracked", "untracked")
    ]
    for relative in paths:
        path = repository / relative
        path.parent.mkdir(parents=True)
        path.write_text("import os\nVALUE = os.environ\n")
    subprocess.run(
        ["git", "-C", str(repository), "add", "--", paths[0]],
        check=True,
        timeout=10,
    )
    return repository, paths


def _add_inventory_policy_canaries(repository: Path, paths: list[str]) -> dict[str, str]:
    outside = repository.parent / "outside.py"
    # Add policy violations only after the original Python-inventory canaries.
    # Each pair has one tracked and one nonignored untracked member.
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=Inventory",
            "-c",
            "user.email=inventory@example.invalid",
            "commit",
            "-qm",
            "baseline",
        ],
        check=True,
        timeout=10,
    )
    fixtures: dict[str, str] = {"lab/releases.yaml": "stages: {}\n"}
    for relative in (
        "Makefile",
        "README.md",
        "orchestrator/pyproject.toml",
        "orchestrator/uv.lock",
        "contract/pyproject.toml",
        "package.json",
        "package-lock.json",
    ):
        fixtures[relative] = (ROOT / relative).read_text()
    for name in ("tracked", "untracked", "ignored"):
        fixtures.update(
            {
                f"orchestrator/tests/{name}/test_probe.py": (
                    "import pytest\nimport subprocess\n"
                    "pytestmark = pytest.mark.quality\nsubprocess.run(['probe'])\n"
                ),
                f".agents/skills/bad_{name}/SKILL.md": "missing frontmatter\n",
                f"lab/problems/registry/{name}/dependencies.json": '{"resolver":"uv@wrong"}',
                f"lab/stage-checks/{name}.json": "{}",
                f"{name}/probe.txt": "",
                f"{name}/installer.sh": (
                    "uv sync --project orchestrator --frozen "
                    "--all-extras --extra inventory-missing\n"
                ),
            }
        )
    (repository / ".gitignore").write_text(
        "ignored/\n.agents/skills/bad_ignored/\nlab/stage-checks/ignored.json\n"
    )
    for relative, source in fixtures.items():
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source)
    # Escaping symlinks must not cause any scanner to read the outside tree.
    for relative in (
        "orchestrator/tests/ignored/link.py",
        ".agents/skills/bad_ignored/link.py",
        "lab/problems/registry/ignored/link.json",
    ):
        (repository / relative).symlink_to(outside)
    tracked_fixtures = [path for path in fixtures if "tracked" in path and "untracked" not in path]
    subprocess.run(
        ["git", "-C", str(repository), "add", "--", *tracked_fixtures],
        check=True,
        timeout=10,
    )
    for relative in paths:
        (repository / relative).write_text("def BadName():\n    pass  # noqa: F401\n")
    policy = repository / "scripts/checks/repository-owners.json"
    policy.parent.mkdir(parents=True)
    policy.write_text('{"seam_owners": {}}\n')
    return fixtures


def _observe_inventory_checkers(repository: Path) -> dict[str, Any]:
    program = dedent(
        """
        import contextlib
        import importlib.util
        import io
        import json
        import subprocess
        import sys
        from pathlib import Path

        checks, root = map(Path, sys.argv[1:])
        sys.path.insert(0, str(checks))
        from git_inventory import repo_paths

        def load(filename):
            spec = importlib.util.spec_from_file_location(filename, checks / filename)
            module = importlib.util.module_from_spec(spec)
            sys.modules[filename] = module
            spec.loader.exec_module(module)
            module.ROOT = root
            return module

        def capture(function, *args, **kwargs):
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = function(*args, **kwargs)
            return [status, stdout.getvalue(), stderr.getvalue()]

        result = {}
        fanout = load("check-python-fanout.py")
        result["fanout"] = [
            item.relpath for item in fanout.collect_python_files(root)
        ]
        structure = load("check-structure.py")
        result["structure"] = capture(structure.main, ["--repo-root", str(root)])
        paths = load("check-repository-paths.py")
        result["paths"] = capture(paths.main, [], root=root)
        pins = load("check_toolchain_pins.py")
        result["pins"] = pins._registry_resolver_violations(root, "1.0")
        hygiene = load("check-test-hygiene.py")
        sys.argv = ["check-test-hygiene", "--repo-root", str(root)]
        result["hygiene"] = capture(hygiene.main)
        quality = load("check-python-quality.py")
        result["skills"] = [
            [item.relpath, item.code]
            for item in quality._skill_directory_diagnostics(root, [])
        ]
        suppressions = load("check-ruff-suppressions.py")
        sys.argv = ["check-ruff-suppressions", "--base-ref", "HEAD"]
        result["suppressions"] = capture(suppressions.main)

        seams = load("run-affected-seams.py")
        real_run = subprocess.run
        selected = []
        def run(command, **kwargs):
            if command[1:3] == ["-m", "pytest"]:
                selected.extend(command[6:])
                return subprocess.CompletedProcess(command, 0)
            return real_run(command, **kwargs)
        subprocess.run = run
        try:
            result["seams"] = capture(seams.main)
        finally:
            subprocess.run = real_run
        result["selected"] = selected

        parity = load("check-toolchain-parity.py")
        sys.argv = ["check-toolchain-parity", "static"]
        result["parity"] = capture(parity.main)
        result["inventory"] = repo_paths(root)
        print(json.dumps(result))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", program, str(ROOT / "scripts/checks"), str(repository)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return cast("dict[str, Any]", json.loads(result.stdout))


def test_checker_inventory_ignores_external_symlinks_and_checks_git_files(tmp_path: Path) -> None:
    repository, paths = _inventory_repository(tmp_path)
    ignored = repository / "orchestrator/src/raidar/scoring/ignored"
    ignored_selection = [ignored.relative_to(repository).as_posix()]
    for selection in ([], ["."], ["orchestrator/src"], paths, ignored_selection):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/checks/check-python-quality.py"),
                "--repo-root",
                str(repository),
                "--check",
                "structure",
                *selection,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        count = 0 if selection == ignored_selection else 2
        assert result.stdout == f"[python-quality] OK: {count} maintained Python files checked\n"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/checks/check-analyzer-scope.py"),
            "--repo-root",
            str(repository),
            "--format",
            "findings-json",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert {finding["path"] for finding in json.loads(result.stdout)} == set(paths)
    program = (
        "import runpy,sys; from pathlib import Path; "
        f"sys.path.insert(0, {str(ROOT / 'scripts/checks')!r}); "
        f"checker=runpy.run_path({str(ROOT / 'scripts/checks/check-mypy-coverage.py')!r}); "
        f"print(checker['inventory'](Path({str(repository)!r})))"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == repr(
        ["orchestrator/src", *[str(Path(path).parent) for path in paths]]
    )
    fixtures = _add_inventory_policy_canaries(repository, paths)
    observed = _observe_inventory_checkers(repository)
    assert observed["fanout"] == paths
    assert observed["structure"][0] == 1
    for name in ("tracked", "untracked"):
        assert f"unexpected top-level directory: {name}/" in "".join(observed["structure"][1:])
    assert observed["paths"][0] == 1
    for relative in paths:
        assert f"{relative}: NAME.conventions: noncanonical symbol BadName;" in observed["paths"][2]
        assert (
            f"{relative}:2: RUFF.IGNORES added suppression; "
            "remove the new suppression and fix the finding"
        ) in observed["suppressions"][2]
    assert observed["suppressions"][0] == 1
    assert observed["pins"] == [
        f"lab/problems/registry/{name}/dependencies.json: "
        "resolver 'uv@wrong' differs from declared uv@1.0"
        for name in ("tracked", "untracked")
    ]
    assert observed["hygiene"][0] == 1
    assert observed["hygiene"][2] == ""
    assert {tuple(line.split(": ", 2)[:2]) for line in observed["hygiene"][1].splitlines()} == {
        (f"orchestrator/tests/{name}/test_probe.py", "TEST.subprocess-timeouts")
        for name in ("tracked", "untracked")
    } | {
        (f".agents/skills/bad_{name}/SKILL.md", "SKILL.metadata")
        for name in ("tracked", "untracked")
    } | {(f"lab/stage-checks/{name}.json", "SEAM.evidence") for name in ("tracked", "untracked")}
    assert observed["skills"] == [
        [f".agents/skills/bad_{name}", "PYQ106"] for name in ("tracked", "untracked")
    ]
    assert observed["seams"] == [0, "TEST.tiers: running 2 affected T2 files\n", ""]
    assert observed["selected"] == [
        str(repository / f"orchestrator/tests/{name}/test_probe.py")
        for name in ("tracked", "untracked")
    ]
    assert observed["parity"][0] == 1
    assert observed["parity"][2] == ""
    assert {tuple(line.split(": ", 2)[:2]) for line in observed["parity"][1].splitlines()} == {
        (f"{name}/installer.sh:1", "PARITY.dependency-inventory")
        for name in ("tracked", "untracked")
    }
    assert observed["inventory"] == sorted(
        [
            ".gitignore",
            "CODEOWNERS",
            *paths,
            *[path for path in fixtures if "ignored" not in path],
            "scripts/checks/repository-owners.json",
        ]
    )
    for checker in (
        "structure",
        "paths",
        "pins",
        "hygiene",
        "skills",
        "suppressions",
        "selected",
        "parity",
    ):
        assert "ignored" not in json.dumps(observed[checker]), checker


@pytest.mark.parametrize("selection", [[], [".agents/skills"], [".agents/skills/bad_skill"]])
@pytest.mark.parametrize("report_format", ["text", "findings-json"])
def test_skill_directory_policy_scans_skills_without_python(
    tmp_path: Path, selection: list[str], report_format: str
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=10)

    def run(paths: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/checks/check-python-quality.py"),
                "--repo-root",
                str(tmp_path),
                "--format",
                report_format,
                *paths,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    (tmp_path / ".agents/skills/good-skill").mkdir(parents=True)
    (tmp_path / ".agents/skills/good-skill/support.txt").write_text("skill support\n")
    assert run([]).returncode == 0
    (tmp_path / ".agents/skills/bad_skill").mkdir()
    # Empty directories are not repository content; a non-Python file makes this one visible.
    assert run(selection).returncode == 0
    (tmp_path / ".agents/skills/bad_skill/support.txt").write_text("skill support\n")
    result = run(selection)
    assert result.returncode == 1
    rendered = result.stderr if report_format == "text" else result.stdout
    assert ".agents/skills/bad_skill" in rendered
    assert "PYQ106" in rendered
    if report_format == "text":
        assert len(result.stderr.splitlines()) == 1
        assert "; rename the skill directory" in result.stderr
    assert run([".agents/skills/good-skill"]).returncode == 0
