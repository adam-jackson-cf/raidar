"""The verify stage in one primary session: the hook, the seal, fix turns, cuts and ``G``.

The workflow runner drives a scripted native agent that edits and commits in a real git
workspace; the stage's container paths point into a temporary directory and its commands run
on the host's shell as the test's own user. The gate's tools are this test environment's own ruff
and pytest (a real install of the pinned tools is what the smoke proves). The privilege
architecture (nothing of the agent's runs as root) is proved in a real container by
``test_workflow_verify_docker``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from harbor.models.agent.context import AgentContext

from raidar.engine.health.gate import gate_family
from raidar.subject.primitive.gate_tool import RULES_BEGIN, witness_findings
from raidar.subject.workflow import verify
from raidar.subject.workflow.definition import load_workflow
from raidar.subject.workflow.runner import (
    PROMPT_FILE,
    RECORD_FILE,
    WORKFLOW_DIR,
    SessionWatch,
    WorkflowRunner,
)
from tests.release_support import REAL_LAB
from tests.workflow_support import (
    CODE,
    FAILING,
    PASSING,
    AgentTurn,
    LocalEnvironment,
    ScriptedAgent,
    commit,
    write,
)

TASK = "Add `add(a, b)` to calc.py, with a test."
EVIL = "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n"
"""A conftest that kills the tests check: the gate cannot decide."""


def definition(fix_rounds: int | None = 2, **lint: str) -> dict[str, Any]:
    """one-shot-verify with a lint and a tests check (the lint check's tool replaced).

    ``fix_rounds`` ``None`` leaves ``max_fix_rounds`` out, so the schema's own default applies.
    """
    resolved = load_workflow(REAL_LAB / "workflows", "one-shot-verify").to_json()
    stages: Any = resolved["stages"]
    lint_check = {"name": "lint", "category": "lint", "tool": "ruff==0.8.6", "run": "ruff check"}
    tests_check = {"name": "tests", "category": "tests", "tool": "pytest==9.1.1"}
    stages["verify"]["checks"] = [
        {**lint_check, **lint, "blocking": True},
        {**tests_check, "blocking": True},
    ]
    if fix_rounds is None:
        stages["verify"].pop("max_fix_rounds", None)
    else:
        stages["verify"]["max_fix_rounds"] = fix_rounds
    return resolved


def run(
    tmp_path: Path,
    env: dict[str, str],
    turns: list[AgentTurn],
    *,
    fix_rounds: int | None = 2,
    **lint: str,
) -> dict[str, Any]:
    """Run one checkpoint and return its workflow record; raises what the run raised."""
    ScriptedAgent.turns = list(turns)
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    runner = WorkflowRunner(
        logs_dir=logs,
        environment_logs_dir=PurePosixPath(logs),
        model_name="openai/gpt-6-luna",
        inner_agent={"name": "codex"},
        workflow="one-shot-verify@2",
        definition=definition(fix_rounds, **lint),
    )
    asyncio.run(runner.run(TASK, LocalEnvironment(env), AgentContext()))  # type: ignore[arg-type]
    return recorded(tmp_path)


def recorded(tmp_path: Path) -> dict[str, Any]:
    record: dict[str, Any] = json.loads(
        (tmp_path / "logs" / WORKFLOW_DIR / RECORD_FILE).read_text()
    )
    return record


def health(tmp_path: Path) -> dict[str, str]:
    return {c.id: c.status for c in gate_family([("checkpoint_1", tmp_path / "logs")])}


def hook_verdicts(tmp_path: Path, record: dict[str, Any]) -> list[str]:
    lines = (tmp_path / "logs" / record["verify"]["hook_log"]).read_text().splitlines()
    return [json.loads(line)["verdict"] for line in lines]


def snapshot(workspace: Path) -> dict[str, Any]:
    """What the gate must not change: HEAD, the index, the object store and the work tree."""
    git = workspace / ".git"

    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    objects = sorted(p.relative_to(git).as_posix() for p in (git / "objects").rglob("*"))
    files = {
        p.relative_to(workspace).as_posix(): sha(p)
        for p in sorted(workspace.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(workspace).parts and p.name != "AGENTS.md"
    }
    return {
        "head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=workspace, capture_output=True, text=True, check=True
        ).stdout,
        "index": sha(git / "index") if (git / "index").exists() else None,
        "objects": objects,
        "files": files,
        "config": sha(git / "config"),
    }


def test_a_commit_the_hook_passes_is_sealed_and_the_checkpoint_ends(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    seen: dict[str, Any] = {}

    def build(ws: Path, prompt: str) -> None:
        seen["rules"] = (ws / "AGENTS.md").read_text()
        write(ws, calc=CODE, test_calc=PASSING)
        seen["commit"] = commit(ws, agent_env)

    record = run(tmp_path, agent_env, [build])

    stage = record["verify"]
    assert (stage["ending"], stage["fix_rounds"], seen["commit"]) == ("gate_passed", 0, 0)
    assert [turn["stage"] for turn in record["turns"]] == ["build"]
    (gate,) = stage["gates"]
    assert (gate["who"], gate["verdict"]) == ("seal", "passed")
    assert "commit" not in gate  # the seal records; it never commits
    assert re.fullmatch(r"[0-9a-f]{40}", gate["tree"])
    assert gate["process"]["uid"] == os.getuid() != 0
    assert stage["setup_check"]["status"] == "passed"
    assert [c["status"] for c in gate["checks"]] == ["passed", "passed"]
    assert hook_verdicts(tmp_path, record) == ["passed"]
    prompt = (tmp_path / "logs" / WORKFLOW_DIR / "build-1" / PROMPT_FILE).read_text()
    assert prompt.endswith(TASK)
    assert "%%%" not in prompt
    assert seen["rules"].lstrip("\n").startswith(RULES_BEGIN)
    assert not (workspace / "AGENTS.md").exists()  # the stage's own file is gone before grading
    assert record["instructions"] == ["workflow/verify/rules.md"]
    assert stage["integrity"] == []
    assert record["integrity"] == []
    assert health(tmp_path) == {"G1": "pass", "G2": "pass", "G3": "pass"}


def test_a_refused_tree_resumes_the_session_with_the_gate_output_until_it_passes(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    commits: list[int] = []

    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=FAILING)
        commits.append(commit(ws, agent_env))

    def fix(ws: Path, prompt: str) -> None:
        write(ws, test_calc=PASSING)
        commits.append(commit(ws, agent_env))

    record = run(tmp_path, agent_env, [build, fix], fix_rounds=2)

    stage = record["verify"]
    assert commits == [1, 0]  # the hook refused the first commit inside the session
    assert (stage["ending"], stage["fix_rounds"]) == ("gate_passed", 1)
    assert [g["verdict"] for g in stage["gates"]] == ["refused", "passed"]
    resumed, fix_prompt = ScriptedAgent.prompts[1]
    assert resumed
    assert "refused your commit" in fix_prompt
    assert "test_add" in fix_prompt
    turns = record["turns"]
    assert [(t["stage"], t["resumed"]) for t in turns] == [("build", False), ("verify", True)]
    assert turns[1]["tokens"] == {"input": 1000, "cached": 600, "output": 50, "cost_usd": None}
    assert hook_verdicts(tmp_path, record) == ["refused", "passed"]
    assert health(tmp_path) == {"G1": "pass", "G2": "pass", "G3": "pass"}


@pytest.mark.parametrize("fix_rounds", [0, None], ids=["explicit-zero", "schema-default"])
def test_a_refusal_with_no_pushback_ends_gate_refused_and_grades_the_agents_own_tree(
    tmp_path: Path,
    workspace: Path,
    agent_env: dict[str, str],
    fix_rounds: int | None,
) -> None:
    seen: dict[str, Any] = {}

    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=FAILING, scratch="x = 1\n")
        commit(ws, agent_env)  # refused by the hook: the work stays staged, HEAD at the base
        seen["after_agent"] = snapshot(ws)

    record = run(tmp_path, agent_env, [build], fix_rounds=fix_rounds)

    stage = record["verify"]
    assert (stage["ending"], stage["gate_ending"], stage["fix_rounds"]) == (
        "gate_refused",
        "gate_refused",
        0,
    )
    assert stage["max_fix_rounds"] == 0
    assert [turn["kind"] for turn in record["turns"]] == ["first_build"]  # no fix turn
    (gate,) = stage["gates"]
    assert (gate["verdict"], gate["exit"]) == ("refused", 1)
    assert snapshot(workspace) == seen["after_agent"]  # HEAD, index, objects, files: untouched
    assert (workspace / "scratch.py").read_text() == "x = 1\n"
    assert (workspace / "test_calc.py").read_text() == FAILING
    assert not (workspace / "AGENTS.md").exists()
    assert health(tmp_path) == {"G1": "pass", "G2": "pass", "G3": "pass"}


def test_two_fix_rounds_are_used_and_a_refusal_after_them_still_grades_the_agents_tree(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=FAILING)

    def still_failing(ws: Path, prompt: str) -> None:
        write(ws, scratch=f"x = {len(ScriptedAgent.prompts)}\n")

    record = run(tmp_path, agent_env, [build, still_failing, still_failing], fix_rounds=2)

    stage = record["verify"]
    assert (stage["ending"], stage["fix_rounds"]) == ("gate_refused", 2)
    assert [t["kind"] for t in record["turns"]] == ["first_build", "gate_fix", "gate_fix"]
    assert [g["verdict"] for g in stage["gates"]] == ["refused"] * 3
    assert (workspace / "scratch.py").read_text() == "x = 3\n"  # what the last turn left


def test_ignored_files_are_checked_without_false_hook_seal_disagreement(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    commits: list[int] = []

    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=PASSING)
        (ws / ".gitignore").write_text("ignored/\n")
        ignored = ws / "ignored"
        ignored.mkdir()
        bad = ignored / "bad.py"
        bad.write_text("import os\n")
        commits.append(commit(ws, agent_env))
        bad.unlink()
        commits.append(commit(ws, agent_env))

    record = run(tmp_path, agent_env, [build])
    assert commits == [1, 0]
    assert record["verify"]["ending"] == "gate_passed"
    assert health(tmp_path) == {"G1": "pass", "G2": "pass", "G3": "pass"}
    lines = (tmp_path / "logs" / record["verify"]["hook_log"]).read_text().splitlines()
    first, second = map(json.loads, lines)
    assert first["tree"] == second["tree"]


def test_a_changed_ignored_input_reruns_a_refused_tree(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    def build(ws: Path, prompt: str) -> None:
        write(
            ws,
            calc=CODE,
            test_calc="from pathlib import Path\n\n\ndef test_settings() -> None:\n"
            '    assert Path("settings.txt").read_text() == "fixed"\n',
        )
        (ws / ".gitignore").write_text("settings.txt\n")
        (ws / "settings.txt").write_text("broken")

    def fix(ws: Path, prompt: str) -> None:
        (ws / "settings.txt").write_text("fixed")

    record = run(tmp_path, agent_env, [build, fix])
    first, second = record["verify"]["gates"]
    assert first["tree"] == second["tree"]
    assert first["worktree_sha256"] != second["worktree_sha256"]
    assert [first["verdict"], second["verdict"]] == ["refused", "passed"]
    assert record["verify"]["ending"] == "gate_passed"


def test_an_unchanged_refused_tree_is_checked_again_not_reused(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=FAILING)

    def nothing(ws: Path, prompt: str) -> None:
        return None

    record = run(tmp_path, agent_env, [build, nothing], fix_rounds=1)

    first, second = record["verify"]["gates"]
    assert (first["tree"], first["worktree_sha256"]) == (second["tree"], second["worktree_sha256"])
    assert all(c["seconds"] is not None and c["status"] for c in second["checks"])
    assert [first["verdict"], second["verdict"]] == ["refused", "refused"]


def test_a_gate_that_cannot_run_ends_the_checkpoint_without_a_fix_turn(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=PASSING, conftest=EVIL)
        commit(ws, agent_env)

    record = run(tmp_path, agent_env, [build])

    stage = record["verify"]
    assert (stage["ending"], stage["fix_rounds"], len(record["turns"])) == ("gate_error", 0, 1)
    assert stage["gates"][0]["verdict"] == "error"
    assert "tests" in stage["gates"][0]["error"]
    assert hook_verdicts(tmp_path, record) == ["error"]
    assert (workspace / "conftest.py").read_text() == EVIL  # nothing restored: the agent's tree


def test_a_tool_that_is_not_installed_fails_the_setup_check_before_the_agent_starts(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    def build(ws: Path, prompt: str) -> None:
        raise AssertionError("the agent must not start")

    with pytest.raises(verify.VerifySetupError, match="tool:lint"):
        run(tmp_path, agent_env, [build], tool="black==24.10.0", run="black --check")

    stage = recorded(tmp_path)["verify"]
    assert stage["setup_check"]["status"] == "failed"
    failed = [c["name"] for c in stage["setup_check"]["checks"] if c["status"] != "passed"]
    assert failed == ["tool:lint"]
    assert stage["ending"] == "turn_error"
    assert ScriptedAgent.prompts == []
    saved = json.loads((tmp_path / "logs" / "workflow/verify/setup-check.json").read_text())
    assert saved["status"] == "failed"


def test_a_deadline_in_a_fix_turn_leaves_the_workspace_as_the_agent_left_it(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=FAILING)

    def cut(ws: Path, prompt: str) -> None:
        write(ws, calc="def add(a, b):\n    return\n", scratch="x = 1\n")
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        run(tmp_path, agent_env, [build, cut])

    stage = recorded(tmp_path)["verify"]
    assert stage["ending"] == "deadline"
    assert "restored" not in stage
    assert (workspace / "calc.py").read_text() == "def add(a, b):\n    return\n"
    assert (workspace / "scratch.py").read_text() == "x = 1\n"
    assert not (workspace / "AGENTS.md").exists()
    assert [g["verdict"] for g in stage["gates"]] == ["refused"]  # the cut turn was never sealed


def test_a_commit_made_around_the_hook_is_found_as_a_bypass(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=PASSING)
        commit(ws, agent_env, "--no-verify")

    record = run(tmp_path, agent_env, [build])

    assert record["verify"]["ending"] == "gate_passed"  # the seal itself passes the tree
    assert hook_verdicts(tmp_path, record) == []
    checks = {c.id: c for c in gate_family([("checkpoint_1", tmp_path / "logs")])}
    assert checks["G2"].status == "violation"
    assert "no hook run passed" in checks["G2"].detail


def test_the_repository_configuration_the_agent_plants_is_never_run_by_the_seal(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    sentinels = [tmp_path / "fsmonitor-ran", tmp_path / "filter-ran"]

    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=PASSING)
        (ws / ".gitattributes").write_text("*.py filter=evil\n")
        config = ws / ".git" / "config"
        config.write_text(
            config.read_text()
            + f"[core]\n\tfsmonitor = touch {sentinels[0]}\n"
            + f'[filter "evil"]\n\tclean = touch {sentinels[1]}; cat\n'
            + f"\tsmudge = touch {sentinels[1]}; cat\n\tprocess = touch {sentinels[1]}\n"
        )

    record = run(tmp_path, agent_env, [build])

    stage = record["verify"]
    assert stage["gates"][0]["verdict"] == "passed"
    assert [s.exists() for s in sentinels] == [False, False]


def test_the_seal_builds_the_same_raw_tree_for_the_same_work_and_changes_nothing_of_the_agents(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=FAILING)

    def same(ws: Path, prompt: str) -> None:
        write(ws, test_calc=FAILING)

    record = run(tmp_path, agent_env, [build, same], fix_rounds=1)

    first, second = record["verify"]["gates"]
    assert first["tree"] == second["tree"]
    files = json.loads((tmp_path / "logs" / first["record"]).read_text())["files"]
    assert set(files) >= {"calc.py", "test_calc.py"}
    assert first["head"] == second["head"] == record["verify"]["base"]
    after = subprocess.run(
        ["git", "status", "--porcelain"], cwd=workspace, capture_output=True, text=True, check=True
    ).stdout
    assert "calc.py" in after  # still untracked: the seal staged and committed nothing


def test_a_rewritten_hook_log_is_a_named_integrity_violation(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    log = Path(verify.PATHS.hook_state) / "hook-runs.jsonl"

    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=FAILING)
        commit(ws, agent_env)  # the hook refuses and logs it

    def rewrite(ws: Path, prompt: str) -> None:
        log.write_text(log.read_text().replace('"verdict": "refused"', '"verdict": "passed"'))
        write(ws, test_calc=PASSING)
        commit(ws, agent_env)

    record = run(tmp_path, agent_env, [build, rewrite])

    names = {item["name"] for item in record["verify"]["integrity"]}
    assert {"hook_log_rewritten", "hook_log_chain_broken"} <= names


def test_a_deleted_hook_log_is_a_named_integrity_violation(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    log = Path(verify.PATHS.hook_state) / "hook-runs.jsonl"

    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=FAILING)
        commit(ws, agent_env)  # refused and logged

    def erase(ws: Path, prompt: str) -> None:
        log.unlink()  # the refusal disappears; the next hook run starts a new log
        write(ws, test_calc=PASSING)
        commit(ws, agent_env)

    record = run(tmp_path, agent_env, [build, erase])

    assert {i["name"] for i in record["verify"]["integrity"]} == {"hook_log_rewritten"}


def test_records_the_agent_wrote_into_the_mounted_logs_are_replaced_by_the_host_kept_ones(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    forged = tmp_path / "logs" / "workflow" / "verify"

    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=PASSING)
        commit(ws, agent_env)
        forged.mkdir(parents=True)
        (forged / "gate-1.json").write_text('{"verdict": "forged"}')
        (forged / "extra.json").write_text("{}")

    record = run(tmp_path, agent_env, [build])

    gate = json.loads((tmp_path / "logs" / record["verify"]["gates"][0]["record"]).read_text())
    assert gate["verdict"] == "passed"
    assert not (forged / "extra.json").exists()


def test_a_native_session_log_rewritten_between_turns_is_a_named_integrity_violation(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    session = tmp_path / "logs" / "sessions" / "rollout.jsonl"

    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=FAILING)
        session.parent.mkdir(parents=True, exist_ok=True)
        session.write_text('{"turn": 1}\n')

    def fix(ws: Path, prompt: str) -> None:
        write(ws, test_calc=PASSING)
        session.write_text('{"turn": "rewritten"}\n{"turn": 2}\n')

    record = run(tmp_path, agent_env, [build, fix])

    assert [(i["name"], i["turn"]) for i in record["integrity"]] == [
        ("session_log_rewritten", "workflow/verify-2")
    ]
    assert record["turns"][1]["integrity"][0]["name"] == "session_log_rewritten"


def test_a_session_log_only_appended_to_is_clean_and_a_write_then_restore_is_seen(
    tmp_path: Path,
) -> None:
    source, staged = tmp_path / "source", tmp_path / "staged"
    (source / "sessions").mkdir(parents=True)
    (staged / "sessions").mkdir(parents=True)
    original = b'{"a": 1}\n'
    watch = SessionWatch()

    def remote() -> dict[str, int]:  # what the container reports for the file's ctime
        return {"sessions/r.jsonl": (source / "sessions" / "r.jsonl").stat().st_ctime_ns}

    def keep(text: bytes, *, grew: bool = True) -> list[dict[str, str]]:
        (source / "sessions" / "r.jsonl").write_bytes(text)
        (staged / "sessions" / "r.jsonl").write_bytes(text)
        return watch.observe(staged, remote(), ("sessions",), grew_expected=grew)

    assert keep(original) == []
    assert keep(original + b'{"a": 2}\n') == []  # appended
    appended = original + b'{"a": 2}\n'
    time.sleep(0.05)  # a file system's ctime is coarse
    (source / "sessions" / "r.jsonl").write_bytes(b"tampered\n")  # a write ...
    (source / "sessions" / "r.jsonl").write_bytes(appended)  # ... and a restore
    (staged / "sessions" / "r.jsonl").write_bytes(appended)
    found = watch.observe(staged, remote(), ("sessions",), grew_expected=True)
    assert [item["name"] for item in found] == ["session_log_touched"]
    (staged / "sessions" / "r.jsonl").unlink()
    removed = watch.observe(staged, remote(), ("sessions",), grew_expected=True)
    assert [item["name"] for item in removed] == ["session_log_removed"]


def test_a_link_planted_in_the_agents_logs_is_named_and_never_copied(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    secret = tmp_path / "host-secret.txt"
    secret.write_text("host secret")

    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=PASSING)
        commit(ws, agent_env)
        (tmp_path / "logs" / "planted.txt").symlink_to(secret)
        (tmp_path / "logs" / "sessions").mkdir(exist_ok=True)
        (tmp_path / "logs" / "sessions" / "deep.txt").symlink_to(secret)

    record = run(tmp_path, agent_env, [build])

    names = [item["name"] for item in record["integrity"]]
    assert names.count("source_symlink") >= 2
    published = tmp_path / "logs" / "workflow" / "build-1"
    assert not (published / "planted.txt").exists()
    assert not (published / "planted.txt").is_symlink()
    assert not (published / "sessions" / "deep.txt").is_symlink()


def test_a_forged_hook_request_writes_no_record_and_names_no_tree(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    log = Path(verify.PATHS.hook_state) / "hook-runs.jsonl"
    forged = (
        "import socket\n"
        "s = socket.socket(socket.AF_UNIX)\ns.connect('b.sock')\n"
        's.sendall(b\'{"verdict": "passed", "tree": "x"}\\n\')\n'
        "s.shutdown(socket.SHUT_WR)\nprint(s.recv(1000).decode())\n"
    )
    replies: list[str] = []
    lines: list[int] = []

    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=PASSING)
        lines.append(len(log.read_text().splitlines()))
        done = subprocess.run(
            [sys.executable, "-c", forged],
            cwd=verify.PATHS.run,
            capture_output=True,
            text=True,
            check=True,
        )
        replies.append(done.stdout)
        lines.append(len(log.read_text().splitlines()))
        env = {**agent_env, "GIT_INDEX_FILE": str(tmp_path / "crafted-index")}
        subprocess.run(["git", "add", "-A"], cwd=ws, check=True, env=agent_env)
        hook = Path(verify.PATHS.hooks) / "pre-commit"
        subprocess.run([str(hook)], cwd=ws, env=env, check=False)  # an index that is not the tree
        lines.append(len(log.read_text().splitlines()))

    record = run(tmp_path, agent_env, [build])

    assert "malformed request" in replies[0]
    assert lines[:2] == [0, 0]  # the forged request left no record
    assert lines[2] == 1  # a request that runs the decision does: the broker's own record
    first = json.loads(log.read_text().splitlines()[0])
    assert first["tree"] is None  # the crafted index is not the work tree that was checked
    assert first["peer_uid"] in (os.getuid(), None)
    assert record["verify"]["ending"] == "gate_passed"


def test_the_checks_changing_the_work_tree_are_named_and_nothing_is_put_back(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    mutate = (
        "from pathlib import Path\n\n"
        "Path('generated.txt').write_text('made by the checks')\n"
        "Path('calc.py').write_text('def add(a, b):\\n    return a + b + 0\\n')\n"
    )

    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=PASSING, conftest=mutate)

    record = run(tmp_path, agent_env, [build])

    stage = record["verify"]
    (gate,) = stage["gates"]
    assert {"generated.txt", "calc.py"} <= set(gate["changed_during_gate"])
    assert gate["tree"] != gate["final_tree"]
    assert gate["worktree_sha256"] != gate["final_worktree_sha256"]
    assert "workspace_changed_during_gate" in {i["name"] for i in stage["integrity"]}
    assert (workspace / "generated.txt").read_text() == "made by the checks"  # not restored
    saved = json.loads((tmp_path / "logs" / gate["record"]).read_text())
    assert saved["files"]["generated.txt"].startswith("file:")  # the final tree is the record's


def test_a_watched_state_file_written_and_restored_or_changed_during_a_gate_run_is_named() -> None:
    def seen(sha: str, ctime: int) -> dict[str, Any]:
        return {"files": {"state.json": {"sha256": sha, "ctime_ns": ctime}}}

    same = witness_findings("define", seen("a", 1), seen("a", 1), set(), "w")
    touched = witness_findings("define", seen("a", 1), seen("a", 2), set(), "w")
    changed = witness_findings("define", seen("a", 1), seen("b", 2), set(), "w")
    added = witness_findings("define", {"files": {}}, seen("a", 1), set(), "w")
    allowed = witness_findings("define", seen("a", 1), seen("b", 2), {"state.json"}, "w")

    assert same == []
    assert allowed == []
    assert [f["name"] for f in touched] == ["define_state_touched"]
    assert [f["name"] for f in changed] == ["define_state_rewritten"]
    assert [f["name"] for f in added] == ["define_state_rewritten"]
