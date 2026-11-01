"""Fast generated-artifact refusals over injected Git outcomes."""

import subprocess
from pathlib import Path

import click
import pytest

from raidar.cli import repo_state


def test_git_output_parses_changes_and_renames_but_ignores_malformed_rows(monkeypatch):
    output = " a.py \n\nb.py\n"

    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout=output, stderr="")

    monkeypatch.setattr(repo_state.subprocess, "run", run)
    assert repo_state.repo_paths_from_git_cmd(["git"]) == ["a.py", "b.py"]
    output = "\nM\tfile.py\nbad-line\nR100\told.py\tnew.py\n"
    assert repo_state.repo_name_status_from_git_cmd(["git"]) == [
        ("M", "file.py"),
        ("R100", "new.py"),
    ]


@pytest.mark.parametrize("stderr", ["bad", ""])
def test_git_failures_refuse_to_treat_unknown_state_as_clean(monkeypatch, stderr: str):
    monkeypatch.setattr(
        repo_state.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 1, stdout="", stderr=stderr),
    )
    for read in (repo_state.repo_paths_from_git_cmd, repo_state.repo_name_status_from_git_cmd):
        with pytest.raises(click.ClickException, match=stderr or "Command failed"):
            read(["git"])
    assert repo_state.has_unstaged_changes(Path("repo")) is True


def test_git_timeouts_remain_errors_not_empty_change_lists(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)

    monkeypatch.setattr(repo_state.subprocess, "run", timeout)
    for read in (repo_state.repo_paths_from_git_cmd, repo_state.repo_name_status_from_git_cmd):
        with pytest.raises(subprocess.TimeoutExpired):
            read(["git"])


def test_changes_are_deduplicated_and_only_generated_additions_are_refused(monkeypatch):
    def paths(args):
        return ["untracked.py", "var/stores/s/log.jsonl"]

    def statuses(args):
        if "--cached" in args:
            return [("M", "a.py"), ("D", "var/jobs/e/j/deleted.json")]
        return [("M", "a.py"), ("M", "var/jobs/e/j/job.json")]

    monkeypatch.setattr(repo_state, "repo_paths_from_git_cmd", paths)
    monkeypatch.setattr(repo_state, "repo_name_status_from_git_cmd", statuses)
    entries = repo_state.changed_repo_entries(Path("repo"))
    assert entries == [
        ("M", "a.py"),
        ("D", "var/jobs/e/j/deleted.json"),
        ("M", "var/jobs/e/j/job.json"),
        ("??", "untracked.py"),
        ("??", "var/stores/s/log.jsonl"),
    ]
    with pytest.raises(click.ClickException, match="Generated Harbor artifacts") as caught:
        repo_state.assert_no_generated_artifact_changes(Path("repo"))
    assert "var/jobs/e/j/job.json" in caught.value.message
    assert "var/stores/s/log.jsonl" in caught.value.message
    assert "deleted.json" not in caught.value.message
    for clean in ([], [("D", "var/jobs/x")], [("M", "src/source.py")]):
        monkeypatch.setattr(repo_state, "changed_repo_entries", lambda _root, rows=clean: rows)
        repo_state.assert_no_generated_artifact_changes(Path("repo"))
    monkeypatch.setattr(
        repo_state.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, stdout="", stderr=""),
    )
    assert repo_state.has_unstaged_changes(Path("repo")) is False


@pytest.mark.parametrize(
    "path", ["var/jobs/j/result.json", "var/stores/s/log.jsonl", "var/analysis/a/report.md"]
)
def test_generated_output_is_distinct_from_source_and_similarly_named_paths(path: str):
    assert repo_state.generated_artifact_paths(
        ["src/x.py", "variable/x", "lab/experiment.yaml", path]
    ) == [path]
