import subprocess

import click
import pytest

from raidar.application import repo_state


def test_repo_paths_and_name_status_parse_git_output_and_errors(monkeypatch):
    monkeypatch.setattr(
        repo_state.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["git"], 0, stdout=" a.py \n\nb.py\n", stderr=""
        ),
    )
    assert repo_state.repo_paths_from_git_cmd(["git"]) == ["a.py", "b.py"]

    monkeypatch.setattr(
        repo_state.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["git"], 0, stdout="M\tfile.py\nbad-line\nR100\told.py\tnew.py\n", stderr=""
        ),
    )
    assert repo_state.repo_name_status_from_git_cmd(["git"]) == [
        ("M", "file.py"),
        ("R100", "new.py"),
    ]

    monkeypatch.setattr(
        repo_state.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(["git"], 1, stdout="", stderr="bad"),
    )
    with pytest.raises(click.ClickException, match="bad"):
        repo_state.repo_paths_from_git_cmd(["git"])
    with pytest.raises(click.ClickException, match="bad"):
        repo_state.repo_name_status_from_git_cmd(["git"])


def test_changed_paths_entries_generated_artifacts_and_unstaged_detection(monkeypatch, tmp_path):
    path_calls = []

    def fake_paths(args):
        path_calls.append(args)
        if "--cached" in args:
            return ["a.py", "experiments/run.json"]
        if "--others" in args:
            return ["untracked.py"]
        return ["a.py", "b.py"]

    status_calls = []

    def fake_status(args):
        status_calls.append(args)
        if "--cached" in args:
            return [("M", "a.py"), ("D", "experiments/deleted.json")]
        return [("M", "a.py"), ("M", "experiments/run.json")]

    monkeypatch.setattr(repo_state, "repo_paths_from_git_cmd", fake_paths)
    monkeypatch.setattr(repo_state, "repo_name_status_from_git_cmd", fake_status)

    assert repo_state.changed_repo_paths(tmp_path) == [
        "a.py",
        "b.py",
        "experiments/run.json",
        "untracked.py",
    ]
    assert repo_state.generated_artifact_paths(["a.py", "experiments/run.json"]) == [
        "experiments/run.json"
    ]
    assert repo_state.changed_repo_entries(tmp_path) == [
        ("M", "a.py"),
        ("D", "experiments/deleted.json"),
        ("M", "experiments/run.json"),
        ("??", "untracked.py"),
    ]

    with pytest.raises(click.ClickException, match="Generated Harbor artifacts"):
        repo_state.assert_no_generated_artifact_changes(tmp_path)

    monkeypatch.setattr(repo_state, "changed_repo_entries", lambda _root: [("D", "experiments/x")])
    repo_state.assert_no_generated_artifact_changes(tmp_path)

    monkeypatch.setattr(
        repo_state.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(["git"], 1, stdout="", stderr=""),
    )
    assert repo_state.has_unstaged_changes(tmp_path) is True
    monkeypatch.setattr(
        repo_state.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(["git"], 0, stdout="", stderr=""),
    )
    assert repo_state.has_unstaged_changes(tmp_path) is False
