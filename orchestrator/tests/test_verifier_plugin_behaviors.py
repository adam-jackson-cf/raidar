"""In-sandbox pytest plugin: outcome precedence, ledger and coverage plumbing.

The plugin normally runs only inside the grader sandbox; here it is loaded
in-process (ledger mode) and its coverage helpers are exercised directly.
"""

from __future__ import annotations

import configparser
import importlib.util
import json
import os
import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from raidar.grading.sandbox import PLUGIN_FILE, RUNTIME_DIR


@pytest.fixture(scope="module", name="plugin")
def plugin_fixture() -> ModuleType:
    spec = importlib.util.spec_from_file_location("raidar_oracle_plugin_under_test", PLUGIN_FILE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_plugin(plugin: ModuleType, tmp_path: Path, *, coverage: bool) -> Any:
    return plugin.OraclePlugin(
        ledger=tmp_path / "out" / "ledger.json",
        root=tmp_path / "app",
        scratch=tmp_path / "scratch",
        coverage=coverage,
        runtime="/runtime",
    )


@pytest.mark.parametrize(
    ("categories", "status"),
    [
        (["", "passed", ""], "passed"),
        (["", "xpassed", ""], "passed"),
        (["error"], "error"),
        (["", "passed", "error"], "error"),
        (["", "failed", ""], "failed"),
        (["skipped"], "skipped"),
        (["", "xfailed", ""], "skipped"),
        ([], "error"),
    ],
)
def test_phase_categories_collapse_with_error_precedence(
    plugin: ModuleType, categories: list[str], status: str
) -> None:
    assert plugin._node_status(categories) == status


def test_subprocess_coverage_injection_only_in_coverage_mode(
    plugin: ModuleType, tmp_path: Path
) -> None:
    ledger_mode = make_plugin(plugin, tmp_path, coverage=False)
    ledger_mode._active = ("test_x.py::test_a", "call")
    assert ledger_mode._subprocess_kwargs({"env": {"A": "1"}}) == {"env": {"A": "1"}}

    coverage_mode = make_plugin(plugin, tmp_path, coverage=True)
    assert coverage_mode._subprocess_kwargs({}) == {}  # no active test: untouched
    coverage_mode._active = ("test_x.py::test_a", "call")
    env = coverage_mode._subprocess_kwargs({"env": {"PYTHONPATH": "/x"}})["env"]
    assert env["PYTHONPATH"].endswith(":/x")
    assert env[plugin.ENV_CONTEXT] == "test_x.py::test_a|call"
    assert env["COVERAGE_PROCESS_START"].endswith("subprocess.coveragerc")


def test_coverage_rows_keep_only_submission_files_and_count_symlink_escapes(
    plugin: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "main.py").write_text("x = 1\n")
    outside = tmp_path / "outside.py"
    outside.write_text("y = 1\n")
    os.symlink(outside, app / "escape.py")

    class Data:
        def measured_files(self) -> list[str]:
            return [str(app / "main.py"), str(app / "escape.py"), "/usr/lib/python3/os.py"]

        def contexts_by_lineno(self, path: str) -> dict[int, list[str]]:
            return {1: ["test_a.py::test_x|call", "collection", "test_a.py::test_x|setup"]}

    class Coverage:
        def get_data(self) -> Data:
            return Data()

    monkeypatch.setattr(plugin, "_current_coverage", lambda: Coverage())
    instance = make_plugin(plugin, tmp_path, coverage=True)

    rows = instance._coverage_contexts()

    assert rows == [
        {"node_id": "test_a.py::test_x", "phase": "call", "path": "main.py", "executed_line": 1},
        {"node_id": "test_a.py::test_x", "phase": "setup", "path": "main.py", "executed_line": 1},
    ]
    assert instance._run.invalid_coverage_paths == 1


# ---------------------------------------------------------- hooks, driven in memory
class _Marker:
    def __init__(self, name: str) -> None:
        self.name = name


class _Item:
    def __init__(self, nodeid: str, *markers: str) -> None:
        self.nodeid = nodeid
        self._markers = [_Marker(name) for name in markers]

    def iter_markers(self) -> list[_Marker]:
        return self._markers


class _Report:
    def __init__(
        self, nodeid: str, when: str, outcome: str = "passed", duration: float | None = 0.0
    ) -> None:
        self.nodeid = nodeid
        self.when = when
        self.outcome = outcome
        self.duration = duration
        self.failed = outcome == "failed"


class _Hook:
    def pytest_report_teststatus(self, *, report: _Report, config: object) -> tuple[str, ...]:
        return ("error" if report.nodeid.endswith("::broken") else report.outcome, ".", "")


class _Config:
    hook = _Hook()


def _configured(plugin: ModuleType, tmp_path: Path, **kwargs: Any) -> Any:
    instance = plugin.OraclePlugin(
        ledger=tmp_path / "out" / "ledger.json",
        root=tmp_path / "app",
        scratch=tmp_path / "scratch",
        coverage=False,
        runtime="/runtime",
        **kwargs,
    )
    instance._config = _Config()
    return instance


def test_collected_items_record_only_group_markers_sorted(
    plugin: ModuleType, tmp_path: Path
) -> None:
    instance = _configured(plugin, tmp_path)
    instance.pytest_collection_modifyitems(
        [_Item("t.py::a", "regression", "slow", "error"), _Item("t.py::b")]
    )
    assert instance._run.markers == {"t.py::a": ["error", "regression"], "t.py::b": []}
    assert instance._run.phases["t.py::a"] == {"collection": "passed"}


def test_failed_collection_reports_are_listed_for_the_host(
    plugin: ModuleType, tmp_path: Path
) -> None:
    instance = _configured(plugin, tmp_path)
    instance.pytest_collectstart(SimpleNamespace(nodeid="t.py"))
    assert instance._active == ("t.py", "collection")
    instance.pytest_collectreport(_Report("t.py", "collect", "failed"))
    instance.pytest_collectreport(_Report("", "collect", "failed"))
    instance.pytest_collectreport(_Report("ok.py", "collect", "passed"))
    assert instance._run.collect_errors == ["t.py", "<session>"]
    assert instance._run.phases["t.py"] == {"collection": "failed"}
    assert "ok.py" not in instance._run.phases
    assert instance._active is None
    instance.pytest_collectstart(SimpleNamespace(nodeid=""))
    assert instance._active == ("<session>", "collection")


def test_phase_reports_accumulate_categories_durations_and_outcomes(
    plugin: ModuleType, tmp_path: Path
) -> None:
    instance = _configured(plugin, tmp_path)
    item = _Item("t.py::a")
    instance.pytest_runtest_setup(item)
    assert instance._active == ("t.py::a", "setup")
    instance.pytest_runtest_logreport(_Report("t.py::a", "setup", duration=0.25))
    instance.pytest_runtest_call(item)
    assert instance._active == ("t.py::a", "call")
    instance.pytest_runtest_logreport(_Report("t.py::a", "call", "failed", duration=None))
    instance.pytest_runtest_teardown(item)
    assert instance._active == ("t.py::a", "teardown")
    instance.pytest_runtest_logreport(_Report("t.py::a", "teardown", duration=0.5))
    instance.pytest_runtest_logreport(_Report("t.py::a", "collect", "failed", duration=9.0))

    assert instance._run.categories == {"t.py::a": ["passed", "failed", "passed"]}
    assert instance._run.durations == {"t.py::a": 0.75}
    assert instance._run.phases["t.py::a"] == {
        "setup": "passed",
        "call": "failed",
        "teardown": "passed",
    }
    assert instance._run.budget_exhausted == []


def test_a_case_that_starts_after_the_budget_fails_in_setup(
    plugin: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [100.0]
    monkeypatch.setattr(plugin, "time", SimpleNamespace(monotonic=lambda: now[0]))
    instance = _configured(plugin, tmp_path, budget_s=10.0)  # deadline: 110.0

    now[0] = 109.9
    instance.pytest_runtest_setup(_Item("t.py::early"))
    assert instance._run.budget_exhausted == []

    now[0] = 110.0
    with pytest.raises(pytest.fail.Exception, match="budget exhausted"):
        instance.pytest_runtest_setup(_Item("t.py::late"))
    assert instance._run.budget_exhausted == ["t.py::late"]

    unbudgeted = _configured(plugin, tmp_path)
    now[0] = 1e9
    unbudgeted.pytest_runtest_setup(_Item("t.py::any"))
    assert unbudgeted._run.budget_exhausted == []


def test_unconfigure_writes_the_ledger_atomically_over_a_forged_one(
    plugin: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plugin, "_environment_probe", lambda: {"grader_python": "py"})
    instance = _configured(plugin, tmp_path)
    forged = tmp_path / "out" / "ledger.json"
    forged.parent.mkdir()
    forged.write_text('{"forged": true}')
    forged.chmod(0o444)
    instance.pytest_collection_modifyitems([_Item("t.py::a", "error"), _Item("t.py::never_ran")])
    instance.pytest_runtest_logreport(_Report("t.py::a", "call", "failed", duration=0.1234567))
    instance.pytest_runtest_logreport(_Report("t.py::a", "teardown"))
    instance.pytest_sessionfinish(object(), 1)

    instance.pytest_unconfigure(instance._config)

    ledger = json.loads(forged.read_text())
    assert ledger["plugin_id"] == plugin.PLUGIN_ID
    assert (ledger["exit_status"], ledger["coverage"], ledger["coverage_enabled"]) == (1, [], False)
    nodes = {n["node_id"]: n for n in ledger["nodes"]}
    assert nodes["t.py::a"] == {
        "node_id": "t.py::a",
        "markers": ["error"],
        "status": "failed",
        "ran": True,
        "duration_s": 0.123457,
    }
    assert nodes["t.py::never_ran"]["ran"] is False
    assert nodes["t.py::never_ran"]["status"] == "error"
    assert [(r["node_id"], r["phase"], r["outcome"]) for r in ledger["ledger"]] == [
        ("t.py::a", "collection", "passed"),
        ("t.py::a", "call", "failed"),
        ("t.py::a", "teardown", "passed"),
        ("t.py::never_ran", "collection", "passed"),
    ]
    assert ledger["environment"] == {"grader_python": "py"}
    assert not list(forged.parent.glob(".*.tmp"))


def test_unconfigure_in_coverage_mode_combines_and_embeds_the_coverage_rows(
    plugin: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "main.py").write_text("x = 1\n")
    combined: list[list[str]] = []

    class Data:
        def measured_files(self) -> list[str]:
            return [str(app / "main.py")]

        def contexts_by_lineno(self, path: str) -> dict[object, list[str]]:
            return {3: ["t.py::a|call"], "odd": ["t.py::a|call"]}

    class Coverage:
        def combine(self, *, data_paths: list[str]) -> None:
            combined.append(data_paths)

        def get_data(self) -> Data:
            return Data()

    monkeypatch.setattr(plugin, "_current_coverage", lambda: Coverage())
    monkeypatch.setattr(plugin, "_environment_probe", dict)
    instance = _configured(plugin, tmp_path)
    instance._coverage = True

    instance.pytest_unconfigure(instance._config)

    assert combined == [[str(tmp_path / "scratch" / "coverage-data")]]
    ledger = json.loads((tmp_path / "out" / "ledger.json").read_text())
    assert ledger["coverage_enabled"] is True
    assert ledger["coverage"] == [
        {"node_id": "t.py::a", "phase": "call", "path": "main.py", "executed_line": 3}
    ]


def test_coverage_without_a_running_collector_yields_no_rows(
    plugin: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plugin, "_current_coverage", lambda: None)
    instance = _configured(plugin, tmp_path)
    instance._coverage = True
    instance._combine_subprocess_coverage()
    instance._activate("t.py::a", "call")  # no collector to switch: still records the phase
    assert instance._coverage_contexts() == []
    assert instance._active == ("t.py::a", "call")


def test_context_is_switched_on_the_running_collector_only_in_coverage_mode(
    plugin: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    switched: list[str] = []
    collector = SimpleNamespace(switch_context=switched.append)
    monkeypatch.setattr(plugin, "_current_coverage", lambda: collector)
    ledger_mode = _configured(plugin, tmp_path)
    ledger_mode._activate("t.py::a", "setup")
    assert switched == []

    coverage_mode = _configured(plugin, tmp_path)
    coverage_mode._coverage = True
    coverage_mode._activate("t.py::a", "setup")
    coverage_mode._switch_context("collection")
    assert switched == ["t.py::a|setup", "collection"]


def test_audited_calls_record_the_active_phase_and_pass_through_arguments(
    plugin: ModuleType, tmp_path: Path
) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def original(*args: Any, **kwargs: Any) -> str:
        calls.append((args, kwargs))
        return "result"

    owner = SimpleNamespace(spawn=original, Popen=original)
    instance = _configured(plugin, tmp_path)
    instance._patch(owner, "spawn", "pty.spawn")
    instance._patch(owner, "Popen", "subprocess.Popen")

    assert owner.spawn("a") == "result"  # outside any test: not recorded
    assert instance._run.process_events == []
    instance._active = ("t.py::a", "call")
    assert owner.spawn("b", key=1) == "result"
    assert owner.Popen(["x"], env={"A": "1"}) == "result"  # ledger mode: kwargs untouched

    assert instance._run.process_events == [
        {"event": "pty.spawn", "node_id": "t.py::a", "phase": "call"},
        {"event": "subprocess.Popen", "node_id": "t.py::a", "phase": "call"},
    ]
    assert calls == [(("a",), {}), (("b",), {"key": 1}), ((["x"],), {"env": {"A": "1"}})]

    instance._restore_process_audit()
    assert owner.spawn is original
    assert owner.Popen is original
    assert instance._originals == []


@pytest.mark.parametrize("optional_spawn_apis", [True, False])
def test_process_events_keep_test_provenance_without_platform_specific_spawn_apis(
    plugin: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    optional_spawn_apis: bool,
) -> None:
    if not optional_spawn_apis:
        monkeypatch.delattr(plugin.os, "posix_spawn", raising=False)
        monkeypatch.delattr(plugin.os, "fork", raising=False)
    monkeypatch.setattr(plugin.subprocess, "Popen", lambda *args, **kwargs: None)
    instance = _configured(plugin, tmp_path)
    instance._install_process_audit()
    try:
        plugin.subprocess.Popen(["fixture"])
        assert instance._run.process_events == []
        instance._active = ("test_a.py::test_run", "call")
        plugin.subprocess.Popen(["fixture"])
        assert instance._run.process_events == [
            {"event": "subprocess.Popen", "node_id": "test_a.py::test_run", "phase": "call"}
        ]
    finally:
        instance._restore_process_audit()


def test_configure_strips_the_runtime_dir_and_enters_collection(
    plugin: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/runtime:/keep")
    instance = _configured(plugin, tmp_path)
    instance._install_process_audit = lambda: None  # type: ignore[method-assign]
    instance.pytest_configure(_Config())
    assert os.environ["PYTHONPATH"] == "/keep"
    assert (tmp_path / "scratch" / "home").is_dir()
    assert instance._active is None  # ledger mode has no collector to switch

    monkeypatch.setenv("PYTHONPATH", "/runtime")
    instance.pytest_configure(_Config())
    assert "PYTHONPATH" not in os.environ


def test_child_interpreters_start_measuring_through_the_prepared_startup_dir(
    plugin: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "site" / "coverage"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    started: list[bool] = []
    fake = ModuleType("coverage")
    fake.__file__ = str(package / "__init__.py")
    fake.process_startup = lambda: started.append(True)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "coverage", fake)
    monkeypatch.setattr(plugin, "_current_coverage", lambda: None)
    instance = _configured(plugin, tmp_path)
    instance._coverage = True
    instance._install_process_audit = lambda: None  # type: ignore[method-assign]

    instance.pytest_configure(_Config())

    startup = tmp_path / "scratch" / "coverage-startup"
    assert (startup / "coverage" / "__init__.py").is_file()
    exec(compile((startup / "sitecustomize.py").read_text(), "sitecustomize", "exec"), {})
    assert started == [True]  # a child started with this dir on PYTHONPATH begins measuring
    rc = configparser.ConfigParser(interpolation=None)
    rc.read(tmp_path / "scratch" / "subprocess.coveragerc")
    assert rc["run"].getboolean("parallel") is True
    assert rc["run"]["source"] == str(tmp_path / "app")  # only the submission is measured
    assert rc["run"]["context"] == f"${plugin.ENV_CONTEXT}"


def test_oracle_environment_is_taken_out_of_the_process_environment(
    plugin: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIDAR_ORACLE_LEDGER", "/x")
    monkeypatch.setenv("RAIDAR_ORACLE_BUDGET_S", "5")
    monkeypatch.setenv("UNRELATED", "1")
    taken = plugin._take_oracle_environment()
    assert taken["RAIDAR_ORACLE_LEDGER"] == "/x"
    assert not any(key.startswith("RAIDAR_ORACLE_") for key in os.environ)
    assert os.environ["UNRELATED"] == "1"


def test_context_rows_ignore_non_line_keys_and_non_test_phases(plugin: ModuleType) -> None:
    assert plugin._context_rows("a.py", "3", ["t|call"]) == []
    assert plugin._context_rows("a.py", 3, ["collection", "t|import", "t|teardown"]) == [
        {"node_id": "t", "phase": "teardown", "path": "a.py", "executed_line": 3}
    ]


def test_ledger_replaces_a_read_only_forgery_via_atomic_rename(
    plugin: ModuleType, tmp_path: Path
) -> None:
    target = tmp_path / "deep" / "ledger.json"
    plugin._write_ledger(target, "first")
    target.chmod(0o444)
    plugin._write_ledger(target, "second")
    assert target.read_text() == "second"
    assert [p.name for p in target.parent.iterdir()] == ["ledger.json"]


def test_a_missing_coverage_module_means_no_current_collector(
    plugin: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "coverage", None)  # import raises ModuleNotFoundError
    assert plugin._current_coverage() is None


def test_the_environment_probe_names_the_interpreter_and_normalises_tools(
    plugin: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    distributions = [
        SimpleNamespace(metadata={"Name": "ZPkg"}, version="2"),
        SimpleNamespace(metadata={"Name": "APkg"}, version="1"),
    ]
    monkeypatch.setattr(plugin.importlib.metadata, "distributions", lambda: iter(distributions))
    probe = plugin._environment_probe()
    assert probe["grader_python"] == sys.executable
    assert probe["grader_python_sha256"] == plugin._sha256_file(sys.executable)
    assert probe["grader_tools"] == ["apkg==1", "zpkg==2"]
    assert plugin._sha256_file(None) == plugin._sha256_file("/no/such/interpreter") == ""
    assert set(probe["platform"]) == {
        "system",
        "release",
        "machine",
        "python_implementation",
    }


def test_declared_package_closure_is_exposed_like_site_packages_including_pth_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closure, extra = tmp_path / "closure", tmp_path / "extra"
    hooked = tmp_path / "hooked"
    for directory in (closure, extra, hooked):
        directory.mkdir()
    (closure / "nspkg.pth").write_text(f"{hooked}\n")  # only site.addsitedir reads .pth files
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv("RAIDAR_SUBMISSION_DEPS", f"{closure}{os.pathsep}{os.pathsep}{extra}")

    runpy.run_path(str(RUNTIME_DIR / "sitecustomize.py"))

    assert {str(closure), str(extra), str(hooked)} <= set(sys.path)


def test_no_declared_closure_leaves_the_import_path_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = list(sys.path)
    monkeypatch.setattr(sys, "path", list(before))
    monkeypatch.delenv("RAIDAR_SUBMISSION_DEPS", raising=False)

    runpy.run_path(str(RUNTIME_DIR / "sitecustomize.py"))

    assert sys.path == before


def test_relative_measured_paths_keep_submission_line_attribution(
    plugin: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Data:
        def measured_files(self) -> list[str]:
            return ["main.py"]

        def contexts_by_lineno(self, path: str) -> dict[int, list[str]]:
            return {7: ["test_a.py::test_x|call"]}

    class Collector:
        def get_data(self) -> Data:
            return Data()

    monkeypatch.setattr(plugin, "_current_coverage", lambda: Collector())
    instance = make_plugin(plugin, tmp_path, coverage=True)
    assert instance._coverage_contexts() == [
        {"node_id": "test_a.py::test_x", "phase": "call", "path": "main.py", "executed_line": 7}
    ]
    assert instance._run.invalid_coverage_paths == 0
