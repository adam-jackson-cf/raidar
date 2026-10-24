"""Execution targets: the targets file, selection precedence, trust and activation."""

from __future__ import annotations

import os
import socket
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.cli.target_options import job_target
from raidar.engine.target.config import (
    TARGETS_TEMPLATE,
    TargetError,
    activate,
    load_targets,
    parse_targets,
    require_trusted,
    resolve_selection,
    resolve_target,
)
from raidar.engine.trial.environment import run_scope

FILE = """
default = "mbp"

[targets.local]
docker_context = "orbstack"
data_check = ["orbstack-data-status"]
data_check_expect = "external_available=True"
max_concurrency = 3

[targets.mbp]
max_concurrency = 2
grader = "local"

[targets.mbp.transport]
kind = "ssh-socket"
host = "me@host"
identity = "~/.ssh/key"
remote_socket = "/run/docker.sock"

[targets.lab]
docker_context = "lab-ctx"
trusted = false
"""


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("RAIDAR_TARGET", "DOCKER_HOST", "DOCKER_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RAIDAR_TARGETS_FILE", str(tmp_path / "targets.toml"))


def write_targets(tmp_path: Path, text: str = FILE) -> Path:
    path = tmp_path / "targets.toml"
    path.write_text(text)
    return path


def test_a_file_parses_into_targets_with_derived_context_and_trust(tmp_path: Path) -> None:
    book = load_targets(write_targets(tmp_path))

    local, mbp, lab = book.get("local"), book.get("mbp"), book.get("lab")
    assert (local.docker_context, local.trusted, local.transport) == ("orbstack", True, None)
    assert local.data_check == ("orbstack-data-status",)
    assert local.data_check_expect == "external_available=True"
    assert (mbp.docker_context, mbp.trusted) == ("raidar-mbp", False)
    assert mbp.transport is not None
    assert mbp.transport.host == "me@host"
    assert lab.trusted is False
    assert (local.grader, mbp.grader) == ("local", "local")
    assert book.default == "mbp"


def test_trusted_can_be_set_explicitly_on_a_transport_target(tmp_path: Path) -> None:
    text = FILE.replace("max_concurrency = 2\n", "max_concurrency = 2\ntrusted = true\n")

    assert load_targets(write_targets(tmp_path, text)).get("mbp").trusted is True


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[targets.a]\ndocker_context = "x"\nbogus = 1\n', "targets.a.bogus"),
        ('[targets.a]\nkind = "harbor"\ndocker_context = "x"\n', "targets.a.kind"),
        ("[targets.a]\n", "set docker_context or a transport"),
        ('[targets.a]\ndocker_context = "x"\nmax_concurrency = 0\n', "max_concurrency"),
        ('[targets.a]\ndocker_context = "x"\ngrader = "nope"\n', "'nope' is not a defined target"),
        ('default = "nope"\n[targets.a]\ndocker_context = "x"\n', "default: 'nope'"),
        ('[targets."bad name"]\ndocker_context = "x"\n', "names use letters"),
        ('[targets."a\\n"]\ndocker_context = "x"\n', "names use letters"),
        ('[targets.a]\ndocker_context = ""\n', "targets.a.docker_context"),
        ('[targets.a]\ndocker_context = "x"\ndata_check_expect = "ok"\n', "without a data_check"),
        (
            '[targets.a.transport]\nkind = "ssh-socket"\nhost = "-oProxyCommand=x"\n'
            'remote_socket = "/s"\n',
            "must not start with '-'",
        ),
        ("[targets.a\n", "invalid targets file"),
        (
            '[targets.a]\ndocker_context = "x"\n[targets.a.transport]\nkind = "ssh-socket"\n'
            'host = "h"\nremote_socket = "/s"\n',
            "docker_context cannot be set with a transport",
        ),
        (
            '[targets.a.transport]\nkind = "ssh-socket"\nhost = "h"\nremote_socket = "/s"\n'
            "port = 2\n",
            "targets.a.transport.port",
        ),
    ],
)
def test_invalid_files_are_refused_with_the_field_named(
    tmp_path: Path, text: str, message: str
) -> None:
    with pytest.raises(TargetError, match=message):
        parse_targets(text, tmp_path / "targets.toml")


def test_selection_precedence_is_flag_then_env_then_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_targets(tmp_path)
    monkeypatch.setenv("DOCKER_CONTEXT", "ignored-ctx")

    assert resolve_target().name == "mbp"  # the file's default
    monkeypatch.setenv("RAIDAR_TARGET", "lab")
    assert resolve_target().name == "lab"  # env beats default
    assert resolve_target("local").name == "local"  # flag beats env


@pytest.mark.parametrize("text", [None, '[targets.a]\ndocker_context = "x"\n'])
def test_no_selection_is_refused_and_the_docker_context_is_never_consulted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str | None
) -> None:
    if text is not None:
        write_targets(tmp_path, text)
    monkeypatch.setenv("DOCKER_CONTEXT", "ambient-ctx")

    with pytest.raises(TargetError, match="no target selected"):
        resolve_target()
    with pytest.raises(TargetError, match="no target selected"):
        resolve_selection()


def test_an_unknown_target_lists_the_defined_names(tmp_path: Path) -> None:
    write_targets(tmp_path)

    with pytest.raises(TargetError, match="unknown target 'nope'.*lab, local, mbp"):
        resolve_target("nope")


def test_the_grader_target_is_resolved_beside_the_trial_target(tmp_path: Path) -> None:
    write_targets(tmp_path)

    selection = resolve_selection("mbp")

    assert (selection.trial.name, selection.grader.name) == ("mbp", "local")
    assert resolve_selection("lab").grader.name == "lab"


def test_an_untrusted_target_is_refused_naming_the_setting(tmp_path: Path) -> None:
    write_targets(tmp_path)

    require_trusted(resolve_target("local"), "grading")
    with pytest.raises(TargetError, match=r"targets\.mbp\.trusted"):
        require_trusted(resolve_target("mbp"), "agent credentials")


def test_activate_points_docker_at_the_target_and_drops_docker_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_targets(tmp_path)
    monkeypatch.setenv("DOCKER_HOST", "tcp://elsewhere")
    monkeypatch.setenv("DOCKER_CONTEXT", "before")

    activate(resolve_target("mbp"))

    assert os.environ["DOCKER_CONTEXT"] == "raidar-mbp"
    assert "DOCKER_HOST" not in os.environ


def test_the_init_template_is_a_valid_file_and_is_never_overwritten(
    tmp_path: Path,
) -> None:
    assert parse_targets(TARGETS_TEMPLATE, tmp_path / "t.toml").default == "local"
    runner = CliRunner()

    first = runner.invoke(main, ["target", "init"])
    second = runner.invoke(main, ["target", "init"])

    assert first.exit_code == 0, first.output
    assert (tmp_path / "targets.toml").read_text() == TARGETS_TEMPLATE
    assert second.exit_code != 0
    assert "already exists" in second.output


def test_target_list_shows_the_selected_target_without_touching_docker(tmp_path: Path) -> None:
    write_targets(tmp_path)

    result = CliRunner().invoke(main, ["target", "list"])

    assert result.exit_code == 0, result.output
    assert '"selected": "mbp"' in result.output
    assert '"docker_context": "raidar-mbp"' in result.output


def test_run_scope_depends_on_the_orchestrator_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trials = tmp_path / "trials"
    monkeypatch.setattr(socket, "gethostname", lambda: "host-a")
    on_a = run_scope(trials)
    monkeypatch.setattr(socket, "gethostname", lambda: "host-b")

    assert run_scope(trials) != on_a
    assert run_scope(trials) == run_scope(trials)


@pytest.mark.parametrize(
    "argv",
    [
        ["assess", "--regrade", "{dir}", "--target", "lab"],
        ["problem", "validate", "scb/forge@38d627ecf668", "--target", "lab"],
    ],
)
def test_hidden_test_commands_refuse_an_untrusted_target_before_touching_docker(
    tmp_path: Path, argv: list[str]
) -> None:
    write_targets(tmp_path)
    job = tmp_path / "job"
    job.mkdir()

    result = CliRunner().invoke(main, [part.format(dir=job) for part in argv])

    assert result.exit_code != 0
    assert "targets.lab.trusted" in result.output


def test_a_run_needs_trusted_trial_and_grader_targets(tmp_path: Path) -> None:
    write_targets(tmp_path)

    with pytest.raises(click.ClickException, match=r"targets\.mbp\.trusted"):
        job_target("mbp")  # transport targets are untrusted unless the file says so
    text = FILE.replace("max_concurrency = 2\n", "max_concurrency = 2\ntrusted = true\n")
    write_targets(tmp_path, text.replace('grader = "local"', 'grader = "lab"'))
    with pytest.raises(click.ClickException, match=r"targets\.lab\.trusted"):
        job_target("mbp")  # a trusted trial target with an untrusted grader target
