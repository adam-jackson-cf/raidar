"""``gate/1``: package checks, ordering, the record-only seal, pytest outcomes, error attribution.

The checks run in the gate payload as the test's own user on temporary workspaces; no package
code is run or imported (the commands are test-authored stand-ins for an installed tool).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import venv
from argparse import Namespace
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import coverage
import pytest
from raidar_contract.package.manifest import GateCheck, Output, parse_manifest

from raidar.contract.snapshot import tree_digest
from raidar.subject.primitive import calls, gate_tool
from raidar.subject.primitive.gate import (
    AgentFacts,
    Attribution,
    GateInvalid,
    GatePackage,
    GatePaths,
    GateRun,
    GateSettings,
    attribute,
    check_canary,
    context_path,
    gate_config,
)
from raidar.subject.primitive.payload import install_tree, walk
from tests.workflow_support import LocalEnvironment

FIXTURE = Path(__file__).parent / "fixtures" / "stages" / "acme" / "gate-check"
PASS = FIXTURE / "fixtures" / "pass" / "state"
REFUSE = FIXTURE / "fixtures" / "refuse" / "state"
FACTS = AgentFacts(owner="agent", trusted_uid=0)
CONTEXT = {"task": "t", "params": {"marker": "FIXME"}, "round": 0, "carry": {}}

SCAN = """\
import json, os, sys
from pathlib import Path
context = json.loads(Path(os.environ["RAIDAR_STAGE_CONTEXT"]).read_text())
marker = context["params"]["marker"]
hits = [str(p) for p in sorted(Path().rglob("*.py")) if marker in p.read_text()]
print("\\n".join(hits))
sys.exit(1 if hits else 0)
"""
PROBE = """\
import json, os, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "argv": sys.argv[1:], "env": sorted(os.environ), "home": os.environ["HOME"],
    "context": os.environ["RAIDAR_STAGE_CONTEXT"], "cwd": os.getcwd(),
}))
"""
SCRIBBLE = "from pathlib import Path\nPath('generated.txt').write_text('made by a check')\n"
NAP = "import time\ntime.sleep(30)\n"


@dataclass
class FakeInstalled:
    """The installed package as the gate sees it (``InstalledPackage``'s shape)."""

    path: str
    root: str
    python: str = sys.executable
    commands: dict[str, list[str]] = field(default_factory=dict)
    ok: bool = True

    @property
    def digest(self) -> str:
        limits = walk.Limits.from_spec({**calls.TREE_LIMITS, "owner": os.getuid()})
        return str(walk.scan(self.root, limits, entries=False)["digest"])

    async def verify(self) -> Any:
        return SimpleNamespace(ok=self.ok, digest="now", expected="installed")


@dataclass
class Rig:
    tmp: Path
    workspace: Path
    bin: Path
    installed: FakeInstalled
    paths: GatePaths

    def script(self, name: str, body: str) -> Path:
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        path.chmod(0o755)
        return path

    def package(
        self,
        checks: tuple[GateCheck, ...] | list[GateCheck],
        outputs: dict[str, Output] | None = None,
    ) -> GatePackage:
        package = GatePackage(
            "gate-check", self.installed, tuple(checks), CONTEXT, None, outputs or {}
        )
        target = Path(context_path(self.paths, package))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(CONTEXT), encoding="utf-8")
        return package

    def config(self, checks: tuple[GateCheck, ...] | list[GateCheck]) -> dict[str, Any]:
        package = self.package(checks)
        return gate_config(self.paths, [package], FACTS, hook_sha256="unused")

    def decide(self, checks: tuple[GateCheck, ...] | list[GateCheck]) -> gate_tool.Decision:
        return gate_tool.decide(self.config(checks), self.tmp / "decide")

    def record(self, checks: tuple[GateCheck, ...] | list[GateCheck]) -> dict[str, Any]:
        decision = self.decide(checks)
        return {"verdict": decision.verdict, "checks": decision.summary(), "error": decision.error}


def make_rig(tmp_path: Path, source: Path | None = None) -> Rig:
    workspace = tmp_path / "app"
    workspace.mkdir()
    if source is not None:
        (workspace / "pytest.ini").write_text("[pytest]\npython_files = spec_*.py\n")
        (workspace / "calc_ok.py").write_text("def add(a, b):\n    return a + b\n")
        marker = "# FIXME\n" if source == REFUSE else ""
        (workspace / "spec_probe.py").write_text(
            "from calc_ok import add\n\ndef test_probe():\n    assert add(2, 3) == 5\n" + marker
        )
    tools = tmp_path / "tools"
    tools.mkdir()
    paths = GatePaths(
        workspace=str(workspace),
        home=str(tmp_path / "home"),
        state=str(tmp_path / "state"),
        system_python=sys.executable,
        reap=False,
        trusted_root=str(Path(calls.__file__).parent),  # the supervisor, as the container has it
        confine=False,  # a test host cannot install seccomp as the container can
        tool_owner=os.getuid(),
    )
    installed = FakeInstalled(path=f"{tools}:/usr/bin:/bin", root=str(tools))
    return Rig(tmp_path, workspace, tools, installed, paths)


def fixture_checks(rig: Rig) -> tuple[GateCheck, ...]:
    """The gate-check package's checks; its ``gate-check`` command is a test-authored stand-in."""
    manifest = parse_manifest((FIXTURE / "stage.yaml").read_text(encoding="utf-8"))
    rig.script("gate-check", SCAN)
    return tuple(manifest.gate)


def names(decision: gate_tool.Decision) -> list[str]:
    return [check.name for check in decision.checks]


def command(name: str, *run: str, **extra: Any) -> GateCheck:
    return GateCheck(name=name, run=run, **extra)


# free names and clean calls ---------------------------------------------------------------------


def test_a_fresh_command_name_runs_unmodified_in_a_clean_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = make_rig(tmp_path)
    rig.script("zorp", PROBE)
    seen = tmp_path / "seen.json"
    check = command("zorp-lint", "zorp", str(seen), "--strict")
    monkeypatch.setenv("HOST_SECRET", "s3cret")

    first = rig.decide([check])
    homes = [json.loads(seen.read_text())["home"]]
    second = rig.decide([check])
    homes.append(json.loads(seen.read_text())["home"])

    assert (first.verdict, second.verdict) == ("passed", "passed")
    assert names(first) == ["gate-check.zorp-lint"]
    facts = json.loads(seen.read_text())
    assert facts["argv"] == [str(seen), "--strict"]  # nothing appended, not even --context
    assert {"PATH", "LANG", "HOME", "TMPDIR", "RAIDAR_STAGE_CONTEXT"} <= set(facts["env"])
    assert "HOST_SECRET" not in facts["env"]  # nothing inherited from the host
    assert facts["context"] == context_path(rig.paths, rig.package([check]))
    assert Path(facts["cwd"]).resolve() == rig.workspace.resolve()
    assert homes[0] != homes[1]
    assert not Path(homes[0]).exists()  # fresh per call, gone after


def test_a_tool_command_runs_the_installed_wrapper_and_its_venv(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, PASS)
    root = Path(rig.installed.root)
    tool_venv = root / "venv"
    venv.EnvBuilder(symlinks=True).create(tool_venv)
    site = next((tool_venv / "lib").glob("python*/site-packages"))
    (site / "zorp_probe.py").write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "Path(sys.argv[1]).write_text(json.dumps("
        "{'argv': sys.argv[1:], 'prefix': sys.prefix}))\n"
    )
    links = tmp_path / "links"
    links.mkdir()
    rig.installed.commands = {"zorp": ["python", "-I", "-B", "-m", "zorp_probe"]}
    install_tree._wrap(root, rig.installed.commands, str(links))
    rig.installed.path = f"{root}/bin:{rig.installed.path}"
    seen = tmp_path / "seen.json"

    decision = rig.decide([command("lint", "zorp", str(seen), "--all")])

    assert decision.verdict == "passed"
    facts = json.loads(seen.read_text())
    assert facts["argv"] == [str(seen), "--all"]
    assert Path(facts["prefix"]) == tool_venv


def test_watched_state_runs_nothing_but_is_witnessed(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, PASS)
    probe = tmp_path / "probe.json"
    rig.script("zorp", PROBE)
    watched = GateCheck(
        name="note", run=("zorp", str(probe)), watch="/app/.raidar/note", timeout_s=30
    )
    config = rig.config([watched])
    watches = gate_tool.watches(config["prerequisites"])
    assert watches == [("gate-check", "/app/.raidar/note", set())]
    assert gate_tool.prerequisites(config) == []  # a watched directory is not a check to run
    assert names(gate_tool.decide(config, tmp_path / "scratch")) == ["gate-check.note"]


# order -----------------------------------------------------------------------------------------


def test_a_refused_prerequisite_prevents_the_ordinary_checks(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, REFUSE)
    decision = rig.decide(fixture_checks(rig))
    assert decision.verdict == "refused"
    assert names(decision) == ["gate-check.no-marker"]  # the failing specs were never run


def test_prerequisites_run_first_wherever_they_are_listed(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, PASS)
    checks = fixture_checks(rig)
    decision = rig.decide(list(reversed(checks)))
    assert decision.verdict == "passed"
    assert names(decision) == ["gate-check.no-marker", "gate-check.unit-specs"]


def test_a_closed_flag_makes_the_packages_before_checks_fail_whatever_the_work_is(
    tmp_path: Path,
) -> None:
    rig = make_rig(tmp_path, PASS)
    flag = tmp_path / "closed-flag"
    config = rig.config(fixture_checks(rig))
    for entry in config["package_checks"]:
        entry["closed_flag"] = str(flag)
    assert gate_tool.decide(config, tmp_path / "open").verdict == "passed"

    flag.write_text("closed by the package")
    decision = gate_tool.decide(config, tmp_path / "closed")

    assert decision.verdict == "refused"
    assert names(decision) == ["gate-check.no-marker"]  # nothing else ran
    assert decision.checks[0].cause == "closed"


def test_the_closed_flag_path_is_in_each_gate_check_entry(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, PASS)
    package = GatePackage("gate-check", rig.installed, tuple(fixture_checks(rig)), CONTEXT, "/flag")
    entries = gate_config(rig.paths, [package], FACTS, hook_sha256="x")["package_checks"]
    assert {entry["closed_flag"] for entry in entries} == {"/flag"}


# the pytest kind -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "status"),
    [
        ("def test_a():\n    assert True\n", "passed"),
        ("import pytest\n\n\n@pytest.mark.skip\ndef test_a():\n    pass\n", "passed"),
        ("def test_a():\n    assert False\n\n\ndef test_b():\n    pass\n", "failed"),
        ("", "failed"),
    ],
)
def test_pytest_passes_only_with_a_collected_test_and_no_failure(
    tmp_path: Path, source: str, status: str
) -> None:
    rig = make_rig(tmp_path)
    (rig.workspace / "pytest.ini").write_text("[pytest]\npython_files = spec_*.py\n")
    (rig.workspace / "spec_x.py").write_text(source)
    decision = rig.decide([GateCheck(name="specs", kind="pytest")])
    (check,) = decision.checks
    assert check.status == status
    assert decision.verdict == ("passed" if status == "passed" else "refused")
    assert check.category == "pytest"


def test_pytest_records_each_individual_outcome(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    (rig.workspace / "pytest.ini").write_text("[pytest]\npython_files = spec_*.py\n")
    body = "def test_a():\n    assert False\n\n\ndef test_b():\n    pass\n"
    (rig.workspace / "spec_x.py").write_text(body)
    (check,) = rig.decide([GateCheck(name="specs", kind="pytest")]).checks
    assert check.tests is not None
    assert check.tests["collected"] == ["spec_x.py::test_a", "spec_x.py::test_b"]
    assert check.tests["outcomes"] == {"spec_x.py::test_a": "failed", "spec_x.py::test_b": "passed"}


def test_pytest_arguments_come_from_run(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    (rig.workspace / "one.py").write_text("def test_one():\n    pass\n")
    (rig.workspace / "two.py").write_text("def test_two():\n    assert False\n")
    (check,) = rig.decide([GateCheck(name="specs", kind="pytest", run=("one.py",))]).checks
    assert check.status == "passed"
    assert check.tests is not None
    assert check.tests["collected"] == ["one.py::test_one"]


# attribution -----------------------------------------------------------------------------------


def test_agent_tests_that_exit_126_after_a_passing_canary_are_the_agents(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, PASS)
    checks = fixture_checks(rig)
    assert check_canary(rig.record(checks)) is None  # the starting workspace decides

    (rig.workspace / "conftest.py").write_text("import os\nos._exit(126)\n")
    record = rig.record(checks)

    assert record["verdict"] == "error"
    assert [c["cause"] for c in record["checks"] if c["status"] == "error"] == ["crashed"]
    attribution, reason = attribute(record)
    assert attribution is Attribution.AGENT_ERROR
    assert "unit-specs" in reason


def test_a_check_past_its_timeout_is_the_agents(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.script("nap", NAP)
    record = rig.record([command("slow", "nap", timeout_s=1)])
    assert [c["cause"] for c in record["checks"]] == ["timeout"]
    assert attribute(record)[0] is Attribution.AGENT_ERROR


def test_a_check_whose_tool_cannot_start_is_an_integrity_failure(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    record = rig.record([command("gone", "no-such-tool")])
    assert [c["cause"] for c in record["checks"]] == ["unrunnable"]
    assert attribute(record)[0] is Attribution.INTEGRITY


def test_changed_tool_bytes_or_a_missing_record_are_integrity_failures() -> None:
    assert attribute({"verdict": "passed", "checks": []}, tools_intact=False)[0] is (
        Attribution.INTEGRITY
    )
    nothing = {"verdict": "error", "error": "no record", "checks": []}
    attribution, reason = attribute(nothing)
    assert attribution is Attribution.INTEGRITY
    assert "no record" in reason


def test_passed_and_refused_records_keep_their_verdicts() -> None:
    assert attribute({"verdict": "passed"})[0] is Attribution.PASSED
    assert attribute({"verdict": "refused"})[0] is Attribution.REFUSED
    assert Attribution.REFUSED.value == "refused"


def test_a_canary_that_cannot_decide_invalidates_the_instrument(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    broken = rig.record([command("gone", "no-such-tool")])
    with pytest.raises(GateInvalid) as caught:
        check_canary(broken)
    assert caught.value.category == "instrument"
    failing = command("strict", sys.executable, "-c", "raise SystemExit(1)")
    refused = rig.record([failing])
    assert refused["verdict"] == "refused"
    check_canary(refused)  # a refusal on the starting workspace is not an instrument failure


# the record-only seal --------------------------------------------------------------------------


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return done.stdout


def start(rig: Rig, checks: tuple[GateCheck, ...] | list[GateCheck]) -> tuple[dict[str, Any], str]:
    config = rig.config(checks)
    base = gate_tool.workspace_setup(config)["base"]
    return config, str(base)


def seal(config: dict[str, Any], base: str, run: int = 1) -> dict[str, Any]:
    return gate_tool.seal(config, Namespace(run=run, after=f"turn-{run}", base=base))


def test_the_seal_records_the_tree_it_judged_and_changes_nothing(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, PASS)
    config, base = start(rig, fixture_checks(rig))
    (rig.workspace / "calc_ok.py").write_text("def add(a, b):\n    return b + a\n")
    (rig.workspace / "extra.py").write_text("X = 1\n")
    status, staged, head = (
        git(rig.workspace, "status", "--porcelain"),
        git(rig.workspace, "ls-files", "-s"),
        git(rig.workspace, "rev-parse", "HEAD"),
    )

    first = seal(config, base)
    second = seal(config, base, 2)

    assert first["verdict"] == "passed"
    assert [c["name"] for c in first["checks"]] == ["gate-check.no-marker", "gate-check.unit-specs"]
    assert first["head"] == head.strip() == base  # nothing committed
    assert git(rig.workspace, "status", "--porcelain") == status  # nothing staged or restored
    assert git(rig.workspace, "ls-files", "-s") == staged
    assert (rig.workspace / "calc_ok.py").read_text().endswith("return b + a\n")
    assert first["tree"] == first["final_tree"] == second["tree"]  # the same tree, judged twice
    assert first["worktree_sha256"] == second["worktree_sha256"]
    assert first["changed_during_gate"] == []


def test_a_check_that_writes_the_workspace_is_recorded_not_undone(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, PASS)
    rig.script("scribble", SCRIBBLE)
    config, base = start(rig, [command("writer", "scribble")])
    record = seal(config, base)
    assert record["changed_during_gate"] == ["generated.txt"]
    assert (rig.workspace / "generated.txt").read_text() == "made by a check"  # left as it is
    assert record["tree"] != record["final_tree"]


def test_the_seal_of_a_gate_error_is_recorded_for_attribution(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, PASS)
    checks = fixture_checks(rig)
    config, base = start(rig, checks)
    (rig.workspace / "conftest.py").write_text("import os\nos._exit(126)\n")
    record = seal(config, base)
    assert record["verdict"] == "error"
    assert attribute(record)[0] is Attribution.AGENT_ERROR


# the gate of one checkpoint: canary, seals, snapshots and closing ------------------------------


class Container(LocalEnvironment):
    """The test's shell as the container; a workspace download honours the exclusions."""

    async def download_dir_with_exclusions(
        self, *, source_dir: str, target_dir: Path, exclude: list[str]
    ) -> None:
        shutil.copytree(source_dir, target_dir, ignore=shutil.ignore_patterns(*exclude))

    async def download_file(self, source_path: str, target_path: Path | str) -> None:
        shutil.copyfile(source_path, target_path)


@pytest.fixture
def gate_env(tmp_path: Path) -> Iterator[dict[str, str]]:
    """The container's environment; under coverage the uploaded payload measures itself."""
    env = dict(os.environ)
    current = coverage.Coverage.current()
    if current is None:
        yield env
        return
    data = tmp_path / "coverage"
    data.mkdir()
    rc = tmp_path / "coveragerc"
    rc.write_text(f"[run]\nsource = {tmp_path / 'home'}\nparallel = true\ndata_file = {data}/.c\n")
    env["COVERAGE_PROCESS_START"] = str(rc)
    env.pop("COVERAGE_FILE", None)
    yield env
    copy, source = str(tmp_path / "home" / Path(gate_tool.__file__).name), gate_tool.__file__
    collected = current.get_data()
    for path in data.glob(".c.*"):
        child = coverage.CoverageData(basename=str(path))
        child.read()
        collected.update(child, map_path=lambda name: source if name == copy else name)


async def open_gate(
    rig: Rig,
    env: dict[str, str],
    checks: tuple[GateCheck, ...] | list[GateCheck],
    *,
    rounds: int = 0,
    outputs: dict[str, Output] | None = None,
) -> GateRun:
    run = GateRun(
        Container(env),
        rig.tmp / "logs",
        [rig.package(checks, outputs)],
        GateSettings(max_fix_rounds=rounds),
        rig.paths,
    )
    await run.prepare()
    return run


@pytest.fixture
async def opened(tmp_path: Path, gate_env: dict[str, str]) -> AsyncIterator[tuple[Rig, GateRun]]:
    rig = make_rig(tmp_path, PASS)
    run = await open_gate(rig, gate_env, fixture_checks(rig), rounds=2)
    yield rig, run
    await run.close()


async def test_the_canary_runs_on_the_starting_workspace_before_the_agent(
    opened: tuple[Rig, GateRun],
) -> None:
    rig, run = opened
    assert run.record()["canary"]["verdict"] == "passed"
    assert run.base is not None
    assert (rig.tmp / "logs" / "gate" / "canary.json").is_file()
    assert run.record()["setup_check"]["status"] == "passed"


async def test_a_refusing_starting_workspace_is_still_a_working_canary(
    tmp_path: Path, gate_env: dict[str, str]
) -> None:
    rig = make_rig(tmp_path, REFUSE)  # the starting workspace may fail its checks
    run = await open_gate(rig, gate_env, fixture_checks(rig))
    try:
        assert run.record()["canary"]["verdict"] == "refused"
    finally:
        await run.close()


async def test_a_missing_tool_invalidates_the_instrument(
    tmp_path: Path, gate_env: dict[str, str]
) -> None:
    rig = make_rig(tmp_path, PASS)
    with pytest.raises(GateInvalid) as caught:
        await open_gate(rig, gate_env, [command("gone", "no-such-tool")])
    assert caught.value.category == "instrument"


async def test_a_gate_that_crashes_on_the_starting_workspace_invalidates_the_instrument(
    tmp_path: Path, gate_env: dict[str, str]
) -> None:
    rig = make_rig(tmp_path, PASS)
    (rig.workspace / "conftest.py").write_text("import os\nos._exit(126)\n")
    with pytest.raises(GateInvalid) as caught:
        await open_gate(rig, gate_env, fixture_checks(rig))
    assert caught.value.category == "instrument"
    assert "canary" in str(caught.value)


async def test_tool_bytes_changed_before_the_agent_invalidate_the_trial(
    tmp_path: Path, gate_env: dict[str, str]
) -> None:
    rig = make_rig(tmp_path, PASS)
    rig.installed.ok = False
    with pytest.raises(GateInvalid) as caught:
        await open_gate(rig, gate_env, fixture_checks(rig))
    assert caught.value.category == "integrity"


async def test_each_seal_is_attributed_to_the_agent_or_the_instrument(
    opened: tuple[Rig, GateRun],
) -> None:
    rig, run = opened
    kept = (rig.workspace / "calc_ok.py").read_text()

    passed = await run.seal("turn-1")
    assert passed.passed
    assert passed.record["attribution"] == "passed"

    (rig.workspace / "calc_ok.py").write_text(kept.replace("a + b", "a - b"))
    refused = await run.seal("turn-2")
    assert refused.attribution is Attribution.REFUSED
    assert refused.resumable
    assert "unit-specs" in refused.record["output"]

    (rig.workspace / "conftest.py").write_text("import os\nos._exit(126)\n")
    broken = await run.seal("turn-3")
    assert broken.attribution is Attribution.AGENT_ERROR  # the agent's own test code
    assert broken.resumable

    rig.installed.ok = False
    tampered = await run.seal("turn-4")
    assert tampered.attribution is Attribution.INTEGRITY
    assert not tampered.resumable
    assert [gate["run"] for gate in run.record()["gates"]] == [1, 2, 3, 4]


async def test_a_fix_turn_starts_from_a_retained_prefix_tree(opened: tuple[Rig, GateRun]) -> None:
    rig, run = opened
    (rig.workspace / "calc_ok.py").write_text("def add(a, b):\n    return 0\n")
    (rig.workspace / "__pycache__").mkdir()
    (rig.workspace / "__pycache__" / "junk.pyc").write_bytes(b"x")

    entry = await run.retain_prefix(1)

    assert entry is not None
    kept = rig.tmp / "logs" / entry["path"]
    assert (kept / "calc_ok.py").read_text() == "def add(a, b):\n    return 0\n"
    assert not (kept / "__pycache__").exists()  # the verifier's own exclusions
    assert entry["tree_digest"] == tree_digest(kept)
    assert run.record()["pre_fix_snapshots"] == [entry]


async def test_no_prefix_tree_is_kept_without_fix_rounds(
    tmp_path: Path, gate_env: dict[str, str]
) -> None:
    rig = make_rig(tmp_path, PASS)
    run = await open_gate(rig, gate_env, fixture_checks(rig), rounds=0)
    try:
        assert await run.retain_prefix(1) is None
        assert not (tmp_path / "logs" / "gate" / "pre_fix_1").exists()
    finally:
        await run.close()


async def test_a_rewritten_hook_log_and_changed_watched_state_are_integrity_findings(
    tmp_path: Path, gate_env: dict[str, str]
) -> None:
    rig = make_rig(tmp_path, PASS)
    state = tmp_path / "state-dir"
    state.mkdir()
    rig.script("touch-state", "import sys\nopen(sys.argv[1] + '/note.md', 'w').write('x')\n")
    watched = GateCheck.model_construct(
        name="touch", run=("touch-state", str(state)), kind="command", before=False,
        watch=str(state), timeout_s=30,
    )  # fmt: skip
    run = await open_gate(rig, gate_env, [watched])
    try:
        log = Path(rig.paths.hook_state) / gate_tool.HOOK_RUNS
        log.write_text('{"first": true}\n')
        await run.seal("turn-1")
        log.write_text('{"second": true}\n')  # the agent's user may rewrite its own log
        await run.seal("turn-2")
        found = {item["name"] for item in run.record()["integrity"]}
    finally:
        await run.close()
    assert "hook_log_rewritten" in found  # a reading; health never invalidates on it
    assert "hook_log_chain_broken" not in found  # there is no chain: nothing root-written to chain
    assert any(name.startswith("gate-check_state_") for name in found)  # the watched state moved


async def test_the_hook_log_is_the_agents_and_a_forged_line_changes_no_attribution(
    tmp_path: Path, gate_env: dict[str, str]
) -> None:
    rig = make_rig(tmp_path, REFUSE)
    run = await open_gate(rig, gate_env, fixture_checks(rig))
    try:
        log = Path(rig.paths.hook_state) / gate_tool.HOOK_RUNS
        assert log.stat().st_uid == os.getuid()  # no root-owned log, no broker beside it
        assert os.access(log, os.W_OK)
        assert not Path(rig.paths.state, "run").exists()
        forged = {"verdict": "passed", "tree": "0" * 40, "who": "hook", "checks": []}
        log.write_text(json.dumps(forged) + "\n")
        outcome = await run.seal("turn-1")
        errors = run.record()["errors"]
    finally:
        await run.close()
    assert outcome.attribution is Attribution.REFUSED  # the seal decides, whatever the log says
    assert errors == []
    assert "0" * 40 in (tmp_path / "logs" / "turn-1" / "hook-runs.jsonl").read_text()


WRITER = "import sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(sys.argv[2])\n"
TAMPER = "import glob, sys\nfor path in glob.glob(sys.argv[1]):\n    open(path, 'wb').write(b'x')\n"


class Unreachable(Container):
    """A container whose downloads fail."""

    async def download_file(self, source_path: str, target_path: Path | str) -> None:
        raise ConnectionError("the container went away")


async def test_a_failed_output_download_is_an_integrity_failure_not_output_evidence(
    tmp_path: Path, gate_env: dict[str, str]
) -> None:
    rig = make_rig(tmp_path, PASS)
    checks, outputs, _ = overwriting_checks(rig, tmp_path)
    run = await open_gate(rig, gate_env, checks, outputs=outputs)
    run.outputs.environment = Unreachable(gate_env)
    try:
        with pytest.raises(GateInvalid) as caught:
            await run.seal("turn-1")
    finally:
        await run.close()
    assert caught.value.category == "integrity"


async def test_a_later_check_tampering_with_an_earlier_capture_is_that_outputs_error_only(
    tmp_path: Path, gate_env: dict[str, str]
) -> None:
    rig = make_rig(tmp_path, PASS)
    rig.script("noop", "pass\n")
    rig.script("tamper", TAMPER)
    note = tmp_path / "out" / "note.md"
    note.parent.mkdir()
    note.write_text("kept")
    archives = f"{rig.paths.scratch}/outputs/*/note.tar"
    checks = [command("idle", "noop"), command("evil", "tamper", archives)]
    outputs = {"note": Output.model_construct(path=str(note), contract=None)}
    run = await open_gate(rig, gate_env, checks, outputs=outputs)
    try:
        outcome = await run.seal("turn-1")
    finally:
        await run.close()
    idle = next(c for c in outcome.record["checks"] if c["name"] == "gate-check.idle")
    assert outcome.passed
    assert idle["outputs"]["note"]["host_path"] is None
    assert idle["outputs"]["note"]["error"]


async def test_a_hostile_manifest_never_reaches_the_host_outside_its_folder(
    tmp_path: Path, gate_env: dict[str, str]
) -> None:
    rig = make_rig(tmp_path, PASS)
    checks, outputs, _ = overwriting_checks(rig, tmp_path)
    run = await open_gate(rig, gate_env, checks, outputs=outputs)
    forged = {"path": "x", "exists": True, "violation": None, "files": 1, "bytes": 1}
    forged["entries"] = [
        {"path": "../../escaped", "type": "file", "size": 1, "sha256": "0" * 64, "mode": 420}
    ]
    forged["digest"] = calls._entry_digest(forged["entries"])
    forged["tar"] = f"{rig.paths.scratch}/outputs/x/note.tar"
    copy = await run.outputs._copy("note", outputs["note"], forged, tmp_path / "dest")
    await run.close()
    assert copy.host_path is None
    assert "unsafe" in (copy.error or "")
    assert not (tmp_path / "escaped").exists()


def overwriting_checks(rig: Rig, tmp_path: Path) -> tuple[list[GateCheck], dict[str, Any], Path]:
    """Two checks that each leave a different value in the one declared output."""
    rig.script("writer", WRITER)
    note = tmp_path / "out" / "note.md"
    note.parent.mkdir()
    checks = [
        command("first", "writer", str(note), "one"),
        command("second", "writer", str(note), "two"),
    ]
    return checks, {"note": Output.model_construct(path=str(note), contract=None)}, note


def kept(record: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Each check's host copy of the note: its host path and bytes."""
    found = {}
    for check in record["checks"]:
        copy = check["outputs"]["note"]
        found[check["name"]] = (copy["host_path"], Path(copy["host_path"]).read_text())
    return found


async def test_each_checks_output_copy_survives_a_later_check_and_a_later_seal(
    tmp_path: Path, gate_env: dict[str, str]
) -> None:
    rig = make_rig(tmp_path, PASS)
    checks, outputs, note = overwriting_checks(rig, tmp_path)
    run = await open_gate(rig, gate_env, checks, outputs=outputs)
    try:
        first = (await run.seal("turn-1")).record
        second = (await run.seal("turn-1")).record  # the same turn label repeats
    finally:
        await run.close()

    one, again = kept(first), kept(second)
    assert note.read_text() == "two"  # the second check overwrote the first's value
    assert [text for _, text in one.values()] == ["one", "two"]
    assert [text for _, text in again.values()] == ["one", "two"]
    paths = [path for path, _ in (*one.values(), *again.values())]
    assert len(set(paths)) == 4  # four calls, four immutable copies
    assert all(Path(path).read_text() in ("one", "two") for path in paths)


async def test_a_missing_or_refused_output_is_evidence_not_a_gate_failure(
    tmp_path: Path, gate_env: dict[str, str]
) -> None:
    rig = make_rig(tmp_path, PASS)
    rig.script("noop", "pass\n")
    absent = tmp_path / "out" / "never.md"
    absent.parent.mkdir()
    outputs = {"note": Output.model_construct(path=str(absent), contract=None)}
    run = await open_gate(rig, gate_env, [command("idle", "noop")], outputs=outputs)
    try:
        outcome = await run.seal("turn-1")
    finally:
        await run.close()

    assert outcome.passed
    (check,) = outcome.record["checks"]
    assert check["outputs"]["note"]["error"] == "missing"
    assert check["outputs"]["note"]["host_path"] is None


async def test_commit_hook_runs_keep_each_checks_output_copy(
    tmp_path: Path, gate_env: dict[str, str]
) -> None:
    rig = make_rig(tmp_path, PASS)
    checks, outputs, _ = overwriting_checks(rig, tmp_path)
    run = await open_gate(rig, gate_env, checks, outputs=outputs)
    try:
        (rig.workspace / "calc_ok.py").write_text("def add(a, b):\n    return b + a\n")
        git(rig.workspace, "add", "-A")
        git(rig.workspace, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
        await run.seal("turn-1")  # copies the hook log, and with it the hook run's captures
    finally:
        await run.close()

    (hook_run,) = run.record()["hook_outputs"]
    copies = {
        check["name"]: Path(check["outputs"]["note"]["host_path"]).read_text()
        for check in hook_run["checks"]
    }
    assert hook_run["seq"] == 1
    assert copies == {"gate-check.first": "one", "gate-check.second": "two"}


async def test_closing_leaves_no_privileged_service_and_records_no_error(
    opened: tuple[Rig, GateRun],
) -> None:
    rig, run = opened
    assert not Path(rig.paths.state, "run").exists()  # no broker socket directory is created
    await run.close()
    assert run.record()["errors"] == []


# every check is a supervised call ----------------------------------------------------------------


def test_each_check_leaves_a_call_record_with_hashes_and_the_clean_environment(
    tmp_path: Path,
) -> None:
    rig = make_rig(tmp_path)
    rig.script("zorp", PROBE)
    seen = tmp_path / "seen.json"
    (check,) = rig.decide([command("lint", "zorp", str(seen))]).checks
    call = check.call
    assert call is not None
    assert call["argv"][1:] == [str(seen)]
    assert call["uid"] == os.getuid()
    assert call["exit_code"] == 0
    assert {"PATH", "HOME", "TMPDIR", "RAIDAR_STAGE_CONTEXT"} <= set(call["env_names"])
    assert len(call["env_sha256"]) == 64
    assert len(call["stdout_sha256"]) == 64
    assert call["timeout_s"] == 60
    assert call["tool_before"][0]["ok"]


def test_watched_state_outside_the_bounds_is_refused_before_the_tool_runs(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    state = tmp_path / "state-dir"
    state.mkdir()
    (state / "loop").symlink_to(state)
    marker = tmp_path / "ran.json"
    rig.script("zorp", PROBE)
    watched = GateCheck.model_construct(
        name="note", run=("zorp", str(marker)), kind="command", before=False,
        watch=str(state), timeout_s=30,
    )  # fmt: skip
    record = rig.record([watched])
    assert not marker.exists()  # the authored command was never invoked
    assert [c["cause"] for c in record["checks"]] == ["bounds"]
    assert attribute(record)[0] is Attribution.AGENT_ERROR


def test_changed_installed_tool_bytes_are_an_integrity_failure(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.script("zorp", PROBE)
    config = rig.config([command("lint", "zorp", str(tmp_path / "seen.json"))])
    (rig.bin / "planted").write_text("changed after install")
    decision = gate_tool.decide(config, tmp_path / "decide")
    record = {"verdict": decision.verdict, "checks": decision.summary(), "error": decision.error}
    assert [c["cause"] for c in record["checks"]] == ["tool_changed"]
    assert attribute(record)[0] is Attribution.INTEGRITY
    assert not (tmp_path / "seen.json").exists()
