"""Python pack measurement tools: lock pins and lazy host provisioning (WS-I I2)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from pydantic import ValidationError

from raidar.cli import main
from raidar.oracle.grader_image import GRADER_TOOLS
from raidar.packs.python import (
    EvalSandboxToolError,
    ToolExecution,
    ToolLock,
    ToolProvisioner,
    ToolProvisioningError,
    load_tool_lock,
)
from raidar.packs.python import provisioner as provisioner_module
from raidar.packs.python.models import TOOL_LOCK_PATH
from raidar.packs.python.provisioner import (
    MARKER_NAME,
    PROVISIONING_PHASE,
    REASON_DRIFT,
    REASON_FIRST_USE,
    TOOL_CACHE_ENV,
    default_cache_root,
)

UV = "/fake/bin/uv"


class FakeUv:
    """Stands in for uv: materialises the files a real install would leave behind."""

    def __init__(self, *, fail_on: str | None = None, python_version: str = "3.12.12") -> None:
        self.fail_on = fail_on
        self.python_version = python_version
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(
        self, argv: Sequence[str], env: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        self.calls.append((args, dict(env)))
        if self.fail_on and self.fail_on in args:
            return subprocess.CompletedProcess(args, 2, "", "resolution failed")
        if args[1:2] == ["venv"]:
            bin_dir = Path(args[-1]) / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "python").touch()
        elif args[1:3] == ["pip", "install"]:
            bin_dir = Path(args[args.index("--python") + 1]).parent
            for exe in ("ruff", "radon"):
                (bin_dir / exe).touch()
        elif args[1:3] == ["tool", "install"]:
            bin_dir = Path(env["UV_TOOL_BIN_DIR"])
            bin_dir.mkdir(parents=True, exist_ok=True)
            (bin_dir / "scb-check").touch()
            tool_python = Path(env["UV_TOOL_DIR"]) / "scb-check" / "bin" / "python"
            tool_python.parent.mkdir(parents=True)
            tool_python.touch()
        elif Path(args[0]).name == "python" and args[1] == "-c":
            return subprocess.CompletedProcess(args, 0, self._probe(args[3:]), "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def _probe(self, modules: list[str]) -> str:
        resolved = {
            "python_version": self.python_version,
            "python_executable": f"/uv/python/cpython-{self.python_version}/bin/python3.12",
            "distributions": sorted(f"{module}==1.0" for module in modules),
        }
        return json.dumps(resolved)

    def probed_modules(self) -> list[tuple[str, list[str]]]:
        return [
            (Path(args[0]).parent.parent.name, args[3:])
            for args, _ in self.calls
            if Path(args[0]).name == "python"
        ]

    def executables(self) -> set[str]:
        return {Path(args[0]).name for args, _ in self.calls}


def _provisioner(cache: Path, runner: FakeUv, lock: ToolLock | None = None) -> ToolProvisioner:
    return ToolProvisioner(
        lock or load_tool_lock(), cache_root=cache, runner=runner, uv_executable=UV
    )


def test_tool_lock_pins_the_scb_fork_contract_versions() -> None:
    lock = load_tool_lock()
    packages = {p for spec in lock.tools.values() for p in spec.packages}
    assert {"ruff==0.8.6", "radon==6.0.1", "scb-check==0.1.3"} <= packages
    assert lock.python == "3.12"
    host = {n for n, s in lock.tools.items() if s.execution is ToolExecution.HOST_PARSE}
    evals = {n for n, s in lock.tools.items() if s.execution is ToolExecution.EVAL_SANDBOX}
    assert "scb-oracle-pytest" in evals
    assert not any(p.startswith("pytest") for n in host for p in lock.tools[n].packages)


@pytest.mark.parametrize(
    "package",
    [
        "scb-check>=0.1",
        "scb-check==0.1.*",
        "scb-check>=0.1,==0.1.3",
        'scb-check ==0.1.3 ; python_version<"3"',
        "scb-check[extra]==0.1.3",
        "scb-check",
    ],
)
def test_tool_lock_rejects_anything_but_an_exact_pin(package: str) -> None:
    raw = yaml.safe_load(TOOL_LOCK_PATH.read_text())
    raw["tools"]["scb-check"]["packages"] = [package]
    with pytest.raises(ValidationError, match="exact-pinned"):
        ToolLock.model_validate(raw)


def test_eval_sandbox_lock_entry_is_the_grader_image_tool_set() -> None:
    lock = load_tool_lock()
    eval_packages = {
        package
        for spec in lock.tools.values()
        if spec.execution is ToolExecution.EVAL_SANDBOX
        for package in spec.packages
    }
    assert eval_packages == set(GRADER_TOOLS)
    assert "coverage" in lock.scorer_distributions()


def test_creating_a_provisioner_installs_nothing(tmp_path: Path) -> None:
    runner = FakeUv()
    provisioner = _provisioner(tmp_path / "cache", runner)
    assert provisioner.is_provisioned("scb-check") is False
    assert runner.calls == []
    assert not (tmp_path / "cache").exists()


def test_first_use_is_timed_separately_and_the_cache_is_reused(tmp_path: Path) -> None:
    runner = FakeUv()
    provisioner = _provisioner(tmp_path / "cache", runner)

    first = provisioner.ensure("scb-python-quality")
    assert first.first_use is not None
    record = first.first_use.as_record()
    assert record["phase"] == PROVISIONING_PHASE
    assert record["excluded_from_arm_efficiency"] is True
    assert record["lock_hash"] == load_tool_lock().lock_hash()
    marker = json.loads((first.root / MARKER_NAME).read_text())
    assert marker["packages"] == list(load_tool_lock().tool("scb-python-quality").packages)
    assert marker["resolved"]["python_version"] == "3.12.12"
    assert record["reason"] == REASON_FIRST_USE
    assert first.executable("ruff").exists()
    calls_after_first = len(runner.calls)

    again = _provisioner(tmp_path / "cache", runner).ensure("scb-python-quality")
    assert again.first_use is None
    assert again.root == first.root
    reuse_calls = [args for args, _ in runner.calls[calls_after_first:]]
    assert all(args[0] != UV for args in reuse_calls), "reuse only re-probes; nothing installs"
    assert provisioner.is_provisioned("scb-check") is False, "only the requested tool is installed"


def test_install_commands_are_pinned_and_scoped_to_the_lock_directory(tmp_path: Path) -> None:
    runner = FakeUv()
    provisioner = _provisioner(tmp_path / "cache", runner)
    lock = load_tool_lock()
    provisioner.ensure("scb-python-quality")
    provisioner.ensure("scb-check")

    install_calls = [(a, e) for a, e in runner.calls if a[0] == UV and "install" in a]
    for argv, _ in install_calls:
        assert argv[argv.index("--exclude-newer") + 1] == lock.exclude_newer
        assert "--no-config" in argv
    pip_argv = next(a for a, _ in install_calls if a[1] == "pip")
    assert pip_argv[-len(lock.tool("scb-python-quality").packages) :] == list(
        lock.tool("scb-python-quality").packages
    )
    tool_argv, tool_env = next((a, e) for a, e in install_calls if a[1] == "tool")
    assert "scb-check==0.1.3" in tool_argv
    assert tool_argv[tool_argv.index("--python") + 1] == "3.12"
    assert Path(tool_env["UV_TOOL_DIR"]).is_relative_to(provisioner.lock_dir)
    assert Path(tool_env["UV_TOOL_BIN_DIR"]).is_relative_to(provisioner.lock_dir)


def test_python_only_provisioning_never_reaches_other_runtimes(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    runner = FakeUv()
    provisioner = _provisioner(cache, runner)
    host_tools = [
        name
        for name, spec in load_tool_lock().tools.items()
        if spec.execution is ToolExecution.HOST_PARSE
    ]
    for name in host_tools:
        provisioner.ensure(name)

    lock_dir = cache / "python" / load_tool_lock().lock_hash()
    assert runner.executables() <= {"uv", "python"}
    assert all(args[0] == UV or Path(args[0]).is_relative_to(lock_dir) for args, _ in runner.calls)
    assert [p.name for p in cache.iterdir()] == ["python"]
    assert [p.name for p in (cache / "python").iterdir()] == [load_tool_lock().lock_hash()]


def test_eval_sandbox_tools_are_never_provisioned_on_the_host(tmp_path: Path) -> None:
    runner = FakeUv()
    provisioner = _provisioner(tmp_path / "cache", runner)
    with pytest.raises(EvalSandboxToolError, match="eval container"):
        provisioner.ensure("scb-oracle-pytest")
    assert runner.calls == []
    with pytest.raises(KeyError, match="unknown python tool"):
        provisioner.ensure("eslint")


def test_a_failed_install_leaves_no_reusable_directory_and_retries(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    provisioner = _provisioner(cache, FakeUv(fail_on="install"))
    with pytest.raises(ToolProvisioningError, match="resolution failed"):
        provisioner.ensure("scb-check")
    assert not (provisioner.lock_dir / "scb-check").exists()
    assert provisioner.is_provisioned("scb-check") is False

    retried = _provisioner(cache, FakeUv()).ensure("scb-check")
    assert retried.first_use is not None


def test_an_install_missing_a_declared_executable_is_rejected(tmp_path: Path) -> None:
    lock = load_tool_lock()
    spec = lock.tool("scb-check").model_copy(update={"executables": ("scb-check", "scb-extra")})
    changed = lock.model_copy(update={"tools": {**lock.tools, "scb-check": spec}})
    provisioner = _provisioner(tmp_path / "cache", FakeUv(), changed)
    with pytest.raises(ToolProvisioningError, match="scb-extra"):
        provisioner.ensure("scb-check")
    assert not (provisioner.lock_dir / "scb-check").exists()


def test_changing_any_pin_provisions_into_a_new_cache_key(tmp_path: Path) -> None:
    lock = load_tool_lock()
    spec = lock.tool("scb-check").model_copy(update={"packages": ("scb-check==0.1.2",)})
    changed = lock.model_copy(update={"tools": {**lock.tools, "scb-check": spec}})
    assert changed.lock_hash() != lock.lock_hash()

    runner = FakeUv()
    original = _provisioner(tmp_path / "cache", runner, lock).ensure("scb-check")
    bumped = _provisioner(tmp_path / "cache", runner, changed).ensure("scb-check")
    assert bumped.first_use is not None
    assert bumped.root != original.root


def test_cache_root_precedence_is_explicit_then_xdg_then_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv(TOOL_CACHE_ENV, raising=False)
    assert default_cache_root() == tmp_path / "home" / ".cache" / "raidar" / "tools"
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert default_cache_root() == tmp_path / "xdg" / "raidar" / "tools"
    monkeypatch.setenv(TOOL_CACHE_ENV, str(tmp_path / "explicit"))
    assert default_cache_root() == tmp_path / "explicit"


def test_provision_command_reports_first_use_and_rejects_eval_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(TOOL_CACHE_ENV, str(tmp_path / "cache"))
    monkeypatch.setattr(provisioner_module, "run_command", FakeUv())
    runner = CliRunner()

    first = runner.invoke(main, ["pack", "provision", "python", "scb-check"])
    assert first.exit_code == 0, first.output
    assert json.loads(first.output)["first_use"]["excluded_from_arm_efficiency"] is True
    again = runner.invoke(main, ["pack", "provision", "python", "scb-check"])
    assert json.loads(again.output)["first_use"] is None

    rejected = runner.invoke(main, ["pack", "provision", "python", "scb-oracle-pytest"])
    assert rejected.exit_code == 1
    assert "eval container" in rejected.output


def test_every_installer_imports_its_declared_modules_in_its_own_env(tmp_path: Path) -> None:
    runner = FakeUv()
    provisioner = _provisioner(tmp_path / "cache", runner)
    lock = load_tool_lock()
    quality = provisioner.ensure("scb-python-quality")
    check = provisioner.ensure("scb-check")

    probed = dict(runner.probed_modules())
    assert probed["venv"] == list(lock.tool("scb-python-quality").modules)
    assert probed["scb-check"] == ["scb_check"]
    assert quality.python.is_relative_to(quality.root)
    assert check.python.is_relative_to(check.root / "tools")


def test_a_reused_install_that_drifted_is_reinstalled(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    first = _provisioner(cache, FakeUv(python_version="3.12.12")).ensure("scb-check")
    assert first.first_use is not None

    same = _provisioner(cache, FakeUv(python_version="3.12.12")).ensure("scb-check")
    assert same.first_use is None

    upgraded = FakeUv(python_version="3.12.13")
    drifted = _provisioner(cache, upgraded).ensure("scb-check")
    assert drifted.first_use is not None
    assert drifted.first_use.reason == REASON_DRIFT
    marker = json.loads((drifted.root / MARKER_NAME).read_text())
    assert marker["resolved"]["python_version"] == "3.12.13"
    assert any(args[1:3] == ["tool", "install"] for args, _ in upgraded.calls)
