"""Python pack agent image: lock-hash tagging, scorer-free audit, task binding (WS-I I1)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from harbor.agents.installed.base import BaseInstalledAgent
from harbor.agents.installed.claude_code import ClaudeCode
from harbor.agents.installed.codex import Codex
from harbor.environments.base import ExecResult
from harbor.environments.definition import should_use_prebuilt_docker_image
from pydantic import ValidationError

from raidar.cli import main
from raidar.packs.python import (
    HarnessPinError,
    ImageAuditError,
    ImageNotBuiltError,
    PackSpec,
    audit_image,
    bind_task_environment,
    build_image,
    compose_override,
    harbor_agent_kwargs,
    harbor_reported_version,
    harbor_version_command,
    image_lock_hash,
    image_tag,
    load_pack_spec,
    load_tool_lock,
)
from raidar.packs.python import image as image_module
from raidar.packs.python.image import LOCK_HASH_LABEL, build_args
from raidar.packs.python.models import PACK_SPEC_PATH


def _spec_with_context(spec: PackSpec, context: Path) -> PackSpec:
    image = spec.image.model_copy(update={"context": str(context)})
    return spec.model_copy(update={"image": image})


DEFAULT_PYTHON = "/usr/local/bin/python3"
DEBIAN_PYTHON = "/usr/bin/python3"
IMAGE_ID = "sha256:" + "ab" * 32


class FakeDocker:
    """Records docker argv; answers inspect/build/run/rm like the real CLI would."""

    def __init__(
        self,
        *,
        present: bool,
        audit: dict[str, object],
        label: str | None = None,
        build_fails: bool = False,
    ) -> None:
        self.present = present
        self.audit = audit
        self.label = label
        self.build_fails = build_fails
        self.calls: list[list[str]] = []

    def __call__(
        self, args: Sequence[str], *, check: bool, capture: bool = True
    ) -> subprocess.CompletedProcess[str]:
        argv = list(args)
        self.calls.append(argv)
        if argv[:2] == ["image", "inspect"]:
            if not self.present:
                return subprocess.CompletedProcess(argv, 1, "", "No such image")
            labels = {LOCK_HASH_LABEL: self.label} if self.label else {}
            document = [{"Id": IMAGE_ID, "Config": {"Labels": labels}}]
            return subprocess.CompletedProcess(argv, 0, json.dumps(document), "")
        if argv[:2] == ["image", "rm"]:
            self.present = False
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == "build":
            if self.build_fails:
                raise subprocess.CalledProcessError(1, ["docker", *argv])
            self.present = True
            prefix = f"{LOCK_HASH_LABEL}="
            self.label = next(a for a in argv if a.startswith(prefix)).removeprefix(prefix)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == "run":
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.audit), "")
        raise AssertionError(f"unexpected docker call {argv}")

    def verbs(self) -> list[str]:
        return [" ".join(call[:2]) if call[0] == "image" else call[0] for call in self.calls]


def _interpreter(version: str, **found: list[str]) -> dict[str, object]:
    return {"version": version, "modules": [], "distributions": [], **found}


# ``--version`` output as each pinned binary prints it (stderr merged, as Harbor execs).
_VERSION_OUTPUT = {"codex-cli": "codex-cli {version}\n", "claude-code": "{version} (Claude Code)\n"}
_TEMP_HOME_WARNING = (
    "WARNING: proceeding, even though we could not create PATH aliases: Refusing to "
    'create helper binaries under temporary dir "/tmp" '
    '(codex_home: AbsolutePathBuf("/tmp/agent_home/.codex"))\n'
)


def _check(output: str, exit_code: int = 0) -> dict[str, object]:
    """One harness's version check as the probe reports it."""
    return {"exit_code": exit_code, "output": output}


def _clean_audit(spec: PackSpec) -> dict[str, object]:
    return {
        "harness": {
            h: _check(_VERSION_OUTPUT[h].format(version=pin.version))
            for h, pin in spec.image.harnesses.items()
        },
        "default_python": "3.12.14",
        "interpreters": {
            DEFAULT_PYTHON: _interpreter("3.12.14"),
            DEBIAN_PYTHON: _interpreter("3.13.5"),
        },
        "executables": [],
        "browsers": [],
        "content": {"dpkg_manifest_sha256": "d" * 64, "npm_global_manifest_sha256": "n" * 64},
    }


def test_image_tag_is_stable_and_changes_with_any_image_input(tmp_path: Path) -> None:
    spec = load_pack_spec()
    assert image_lock_hash(spec) == image_lock_hash(load_pack_spec())
    assert image_tag(spec) == f"{spec.image.repository}:{image_lock_hash(spec)[:16]}"

    context = tmp_path / "image"
    shutil.copytree(spec.context_dir, context)
    copied = _spec_with_context(spec, context)
    assert image_lock_hash(copied) == image_lock_hash(spec)

    dockerfile = context / "Dockerfile"
    dockerfile.write_text(dockerfile.read_text() + "\nRUN true\n")
    assert image_lock_hash(copied) != image_lock_hash(spec)

    codex = spec.image.harnesses["codex-cli"]
    old_codex = codex.model_copy(update={"version": "0.0.1"})
    bumped_harnesses = {**spec.image.harnesses, "codex-cli": old_codex}
    bumped = spec.model_copy(
        update={"image": spec.image.model_copy(update={"harnesses": bumped_harnesses})}
    )
    assert image_lock_hash(bumped) != image_lock_hash(spec)

    rebound = spec.model_copy(
        update={
            "task_environment": spec.task_environment.model_copy(
                update={"compose_main_service": {"init": True, "stop_grace_period": "1s"}}
            )
        }
    )
    assert image_lock_hash(rebound) == image_lock_hash(spec), "task binding is not an image input"


def test_pack_spec_rejects_a_base_image_not_pinned_by_digest() -> None:
    raw = yaml.safe_load(PACK_SPEC_PATH.read_text())
    raw["image"]["base_image"] = "ghcr.io/astral-sh/uv:python3.12-trixie-slim"
    with pytest.raises(ValidationError, match="pinned by digest"):
        PackSpec.model_validate(raw)


@pytest.mark.parametrize(
    ("agent_home", "error"),
    [
        ("/tmp/agent_home", "temporary directory"),
        ("/tmp", "temporary directory"),
        ("/var/tmp/home", "temporary directory"),
        ("/home/../tmp/agent", "temporary directory"),
        ("home/agent", "absolute"),
        ("/tmp/../home/agent", None),
        ("/home/agent", None),
        ("/tmpfoo/agent", None),
    ],
)
def test_agent_home_must_sit_outside_temporary_directories(
    agent_home: str, error: str | None
) -> None:
    raw = yaml.safe_load(PACK_SPEC_PATH.read_text())
    raw["image"]["agent_home"] = agent_home
    if error is None:
        assert PackSpec.model_validate(raw).image.agent_home == agent_home
        return
    with pytest.raises(ValidationError, match=error):
        PackSpec.model_validate(raw)


def test_image_definition_names_no_scorer_tool_or_browser() -> None:
    spec, lock = load_pack_spec(), load_tool_lock()
    image_text = "\n".join(
        path.read_text().lower() for path in spec.context_dir.rglob("*") if path.is_file()
    )
    named = [
        name
        for name in (*lock.scorer_distributions(), *spec.image.forbidden_executables)
        if name.lower() in image_text
    ]
    assert named == []
    assert set(build_args(spec)) >= {
        "BASE_IMAGE",
        "AGENT_HOME",
        "CODEX_CLI_NPM_PACKAGE",
        "CODEX_CLI_VERSION",
        "CLAUDE_CODE_NPM_PACKAGE",
        "CLAUDE_CODE_VERSION",
    }
    assert build_args(spec)["AGENT_HOME"] == spec.image.agent_home


def test_build_reuses_only_a_labelled_clean_tag_and_force_rebuilds() -> None:
    spec, lock = load_pack_spec(), load_tool_lock()

    docker = FakeDocker(present=True, audit=_clean_audit(spec), label=image_lock_hash(spec))
    reused = build_image(spec, lock, docker=docker)
    assert reused.built is False
    assert reused.audit.ok, "a reused tag is audited too"
    assert reused.image_id == IMAGE_ID
    assert docker.verbs() == ["image inspect", "image inspect", "run"]

    for label in (None, "0" * 64):
        docker = FakeDocker(present=True, audit=_clean_audit(spec), label=label)
        assert build_image(spec, lock, docker=docker).built is True, f"label {label} not reused"

    docker = FakeDocker(present=True, audit=_clean_audit(spec), label=image_lock_hash(spec))
    rebuilt = build_image(spec, lock, force=True, docker=docker)
    assert rebuilt.built is True
    assert rebuilt.audit.ok
    assert rebuilt.audit.content["dpkg_manifest_sha256"] == "d" * 64
    assert docker.verbs() == ["image inspect", "build", "image inspect", "run"]
    build_call = docker.calls[1]
    assert f"{LOCK_HASH_LABEL}={image_lock_hash(spec)}" in build_call
    assert build_call[build_call.index("--tag") + 1] == image_tag(spec)
    audit_call = docker.calls[3]
    assert audit_call[audit_call.index("--network") + 1] == "none"
    assert IMAGE_ID in audit_call, "the audit runs the exact image it inspected"
    request = json.loads(audit_call[-1])
    assert request["harnesses"] == {
        h: harbor_version_command(pin) for h, pin in spec.image.harnesses.items()
    }, "the audit runs Harbor's own install version check"
    assert set(request["executables"]) >= {"ruff", "scb-check", "pytest"}
    assert set(request["distributions"]) >= {"ruff", "radon", "scb-check", "pytest"}


def _dirty(spec: PackSpec, **overrides: object) -> dict[str, object]:
    return {**_clean_audit(spec), **overrides}


def _dirty_audits(spec: PackSpec) -> dict[str, dict[str, object]]:
    clean = _interpreter("3.12.14")
    return {
        "scorer executable": _dirty(spec, executables=["ruff"]),
        "browser": _dirty(spec, browsers=["chromium"]),
        "module in default python": _dirty(
            spec, interpreters={DEFAULT_PYTHON: _interpreter("3.12.14", modules=["radon"])}
        ),
        "distribution in a second python": _dirty(
            spec,
            interpreters={
                DEFAULT_PYTHON: clean,
                DEBIAN_PYTHON: _interpreter("3.13.5", distributions=["pytest"]),
            },
        ),
        "unprobeable python": _dirty(
            spec, interpreters={DEFAULT_PYTHON: clean, DEBIAN_PYTHON: {"error": "boom"}}
        ),
        "default python off the pack pin": _dirty(spec, default_python="3.13.5"),
        "harness off its pin": _dirty(
            spec, harness={"codex-cli": _check("codex-cli 0.0.1"), "claude-code": _check("")}
        ),
        "harness prints its pin but exits non-zero": _dirty(
            spec,
            harness={
                **_clean_audit(spec)["harness"],  # type: ignore[dict-item]
                "codex-cli": _check(
                    _VERSION_OUTPUT["codex-cli"].format(
                        version=spec.image.harnesses["codex-cli"].version
                    ),
                    exit_code=1,
                ),
            },
        ),
        "codex warns before its version": _dirty(
            spec,
            harness={
                **_clean_audit(spec)["harness"],  # type: ignore[dict-item]
                "codex-cli": _check(
                    _TEMP_HOME_WARNING
                    + _VERSION_OUTPUT["codex-cli"].format(
                        version=spec.image.harnesses["codex-cli"].version
                    )
                ),
            },
        ),
    }


@pytest.mark.parametrize("case", list(_dirty_audits(load_pack_spec())))
@pytest.mark.parametrize("reused", [False, True])
def test_a_dirty_image_fails_and_is_untagged_so_it_is_never_reused(case: str, reused: bool) -> None:
    spec, lock = load_pack_spec(), load_tool_lock()
    label = image_lock_hash(spec) if reused else None
    docker = FakeDocker(present=reused, audit=_dirty_audits(spec)[case], label=label)
    with pytest.raises(ImageAuditError) as raised:
        build_image(spec, lock, docker=docker)
    assert raised.value.audit.ok is False
    assert docker.verbs()[-1] == "image rm"
    assert docker.present is False

    docker.audit = _clean_audit(spec)
    again = build_image(spec, lock, docker=docker)
    assert again.built is True, "the next build without --force rebuilds instead of reusing"


def test_audit_reports_scorer_findings_from_every_interpreter() -> None:
    spec, lock = load_pack_spec(), load_tool_lock()
    audit = _dirty_audits(spec)["distribution in a second python"]
    result = audit_image(image_tag(spec), spec, lock, FakeDocker(present=True, audit=audit))
    assert result.scorer_distributions == ("pytest",)
    assert result.python_interpreters == {DEFAULT_PYTHON: "3.12.14", DEBIAN_PYTHON: "3.13.5"}
    with pytest.raises(ImageNotBuiltError, match="not built"):
        audit_image(image_tag(spec), spec, lock, FakeDocker(present=False, audit=audit))


@pytest.mark.parametrize(
    ("harness", "reported", "exit_code", "matches"),
    [
        ("codex-cli", "codex-cli {version}\n", 0, True),
        ("claude-code", "{version} (Claude Code)\n", 0, True),
        ("codex-cli", "codex-cli {version}0\n", 0, False),
        ("codex-cli", _TEMP_HOME_WARNING + "codex-cli {version}\n", 0, False),
        ("claude-code", "", 0, False),
        ("codex-cli", "codex-cli {version}\n", 1, False),
    ],
)
def test_harness_matches_only_when_harbor_parses_exactly_the_pin(
    harness: str, reported: str, exit_code: int, matches: bool
) -> None:
    pin = load_pack_spec().image.harnesses[harness]
    parsed = harbor_reported_version(pin, reported.format(version=pin.version), exit_code)
    assert (parsed == pin.version) is matches


class _VersionReportingEnvironment:
    """Answers every exec with one ``--version`` output, like the pack image would."""

    def __init__(self, version_output: str, return_code: int = 0) -> None:
        self.version_output = version_output
        self.return_code = return_code
        self.commands: list[str] = []

    async def exec(self, command: str, **_: object) -> ExecResult:
        self.commands.append(command)
        return ExecResult(stdout=self.version_output, stderr="", return_code=self.return_code)


@pytest.mark.parametrize(
    ("harness", "agent_class", "version_output"),
    [
        ("codex-cli", Codex, "codex-cli {version}\n"),
        ("claude-code", ClaudeCode, "{version} (Claude Code)\n"),
    ],
)
def test_harbor_agent_on_the_pack_pin_skips_install(
    tmp_path: Path, harness: str, agent_class: type[BaseInstalledAgent], version_output: str
) -> None:
    spec = load_pack_spec()
    pinned = spec.image.harnesses[harness].version
    kwargs = harbor_agent_kwargs(spec, harness)
    assert kwargs == {"version": pinned}

    agent = agent_class(logs_dir=tmp_path, model_name="openai/gpt-6-luna", **kwargs)
    environment = _VersionReportingEnvironment(version_output.format(version=pinned))
    asyncio.run(agent.install(environment))  # type: ignore[arg-type]
    # Only the version check ran, and it is the command the image audit replays.
    assert environment.commands == [harbor_version_command(spec.image.harnesses[harness])]

    failing = _VersionReportingEnvironment(version_output.format(version=pinned), return_code=1)
    with contextlib.suppress(Exception):
        asyncio.run(agent.install(failing))  # type: ignore[arg-type]
    assert len(failing.commands) > 1, "a non-zero version check reinstalls, as the audit assumes"

    with pytest.raises(HarnessPinError, match="reinstall"):
        harbor_agent_kwargs(spec, harness, configured="0.0.1")
    assert harbor_agent_kwargs(spec, harness, configured=pinned) == kwargs
    with pytest.raises(HarnessPinError, match="pins no harness"):
        harbor_agent_kwargs(spec, "gemini-cli")


def test_compose_override_sets_init_and_keeps_task_services() -> None:
    spec = load_pack_spec()
    existing = {
        "services": {
            "main": {"init": False, "environment": {"A": "1"}},
            "db": {"image": "postgres:16"},
        },
        "volumes": {"data": {}},
    }
    merged = compose_override(existing, spec)
    assert merged["services"]["main"] == {"init": True, "environment": {"A": "1"}}
    assert merged["services"]["db"] == {"image": "postgres:16"}
    assert merged["volumes"] == {"data": {}}
    assert compose_override(None, spec) == {"services": {"main": {"init": True}}}


def test_bound_task_environment_always_uses_the_prebuilt_pack_image(tmp_path: Path) -> None:
    spec = load_pack_spec()
    environment_dir = tmp_path / "environment"
    fields = bind_task_environment(environment_dir, spec)

    assert fields == {"docker_image": image_tag(spec)}
    compose = yaml.safe_load((environment_dir / "docker-compose.yaml").read_text())
    assert compose == {"services": {"main": {"init": True}}}
    for force_build in (False, True):
        assert should_use_prebuilt_docker_image(
            environment_dir, docker_image=fields["docker_image"], force_build=force_build
        )


def test_binding_rejects_an_environment_that_still_builds_its_own_image(tmp_path: Path) -> None:
    environment_dir = tmp_path / "environment"
    environment_dir.mkdir()
    (environment_dir / "Dockerfile").write_text("FROM scratch\n")
    with pytest.raises(ValueError, match="prebuilt pack image only"):
        bind_task_environment(environment_dir, load_pack_spec())
    assert not (environment_dir / "docker-compose.yaml").exists()


def test_pack_commands_report_the_audit_and_fail_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec = load_pack_spec()
    monkeypatch.setenv("RAIDAR_TOOL_CACHE", str(tmp_path / "cache"))
    runner = CliRunner()
    label = image_lock_hash(spec)

    clean = FakeDocker(present=True, audit=_clean_audit(spec), label=label)
    monkeypatch.setattr(image_module, "run_docker", clean)
    info = runner.invoke(main, ["pack", "info", "python"])
    assert info.exit_code == 0, info.output
    payload = json.loads(info.output)
    assert payload["image_tag"] == image_tag(spec)
    assert payload["image_id"] == IMAGE_ID
    assert payload["provisioned"] == {"scb-python-quality": False, "scb-check": False}
    audited = runner.invoke(main, ["pack", "audit", "python"])
    assert audited.exit_code == 0, audited.output
    assert json.loads(audited.output)["ok"] is True

    dirty = _dirty(spec, executables=["ruff"])
    monkeypatch.setattr(image_module, "run_docker", FakeDocker(present=False, audit=dirty))
    built = runner.invoke(main, ["pack", "build", "python"])
    assert built.exit_code == 1
    assert "failed its audit" in built.output
    monkeypatch.setattr(image_module, "run_docker", FakeDocker(present=True, audit=dirty))
    assert runner.invoke(main, ["pack", "audit", "python"]).exit_code == 1

    missing = FakeDocker(present=False, audit=_clean_audit(spec))
    monkeypatch.setattr(image_module, "run_docker", missing)
    not_built = runner.invoke(main, ["pack", "audit", "python"])
    assert (not_built.exit_code, not_built.exception.__class__) == (1, SystemExit)
    assert "make pack-build" in not_built.output

    broken = FakeDocker(present=False, audit=_clean_audit(spec), build_fails=True)
    monkeypatch.setattr(image_module, "run_docker", broken)
    failed = runner.invoke(main, ["pack", "build", "python"])
    assert (failed.exit_code, failed.exception.__class__) == (1, SystemExit)
    assert "docker build" in failed.output
