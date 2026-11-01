"""P8: the agent saw only the workspace's skills and instructions (pure decisions, in memory)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, cast

import pytest

from raidar.engine.health.base import Audit
from raidar.engine.health.isolation import (
    _agent_reader,
    _audit_findings,
    _before_snapshot,
    _broken_components,
    _json,
    _origin_files,
    _placed_lines,
    _placements,
    _PreTurn,
    _record_messages,
    _snapshot_reader,
    _source_findings,
    _step_findings,
    _stored_snapshot,
    _turn_findings,
    _verdict,
    check_p8,
)
from raidar.engine.health.model import HealthCheck, HealthReport
from raidar.engine.trial.collect import health_validity
from raidar.subject.agent.config import Harness
from tests.native_support import session_audit

STEP = "checkpoint_1"
SOURCE = b"native rollout"
LOG = "workflow/build-1"
RECORD = "workflow/components/rules.md"
FILES = {f"{LOG}/rollout.jsonl": SOURCE}
ROOT = "/app/.agents/skills"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def audit(**over: Any) -> dict[str, Any]:
    """A turn's normalized ``session_audit`` that passes unless ``over`` changes it."""
    base: dict[str, Any] = {
        "harness": "codex",
        "version": "1",
        "records": 1,
        "sources": [{"path": "rollout.jsonl", "sha256": sha(SOURCE)}],
        "skill_roots": [ROOT],
        "instructions": [],
        "violations": [],
        "unmeasured": [],
        "messages": [],
    }
    return base | over


def instruction(text: str, path: Any = "/app") -> dict[str, Any]:
    return {"path": path, "text": text}


def component(text: bytes, path: str = "AGENTS.md", record: Any = RECORD) -> dict[str, Any]:
    return {"path": path, "sha256": sha(text), "record": record}


def workflow(*audits: Any, components: Any = None) -> dict[str, Any]:
    turns = [
        {"stage": "build", "turn": n, "log_dir": LOG, "session_audit": a}
        for n, a in enumerate(audits, 1)
    ]
    flow: dict[str, Any] = {"turns": turns}
    if components is not None:
        flow["components"] = components
    return flow


def reader(files: Mapping[str, bytes]) -> Any:
    return lambda path: files.get(path.as_posix())


def workspace(files: Mapping[str, str] | None) -> Any:
    """The workspace as the step began (None for the first step, which has none)."""
    return None if files is None else lambda path: files.get(path.as_posix())


def run(
    flow: Any,
    files: Mapping[str, bytes] | None = None,
    before: Mapping[str, str] | None = None,
    step: str = STEP,
) -> HealthCheck:
    violations: dict[str, str] = {}
    unmeasured: list[str] = []
    messages: dict[str, list[dict[str, str]]] = {}
    checked = _step_findings(
        step,
        flow,
        reader(FILES if files is None else files),
        workspace(before),
        violations,
        unmeasured,
        messages,
    )
    return _verdict(checked, violations, unmeasured, messages)


# --- verdicts over whole steps ---------------------------------------------------------------


def test_workspace_skills_and_a_plain_prompt_pass() -> None:
    check = run(workflow(audit()))
    assert check.status == "pass", check.detail
    assert check.evidence["messages"] == {"codex@1": []}


def test_a_bundled_skill_root_is_a_violation_naming_root_and_turn() -> None:
    check = run(workflow(audit(skill_roots=[ROOT, "/tmp/codex-home/skills/.system"])))
    assert check.status == "violation"
    assert "/tmp/codex-home/skills/.system" in check.detail
    assert f"{STEP}/build/1" in check.detail


def test_recommended_plugins_are_a_violation_and_invalidate_the_trial() -> None:
    check = run(workflow(audit(violations=["recommended plugins were offered: github"])))
    assert check.status == "violation"
    report = HealthReport(trial_id="t", checks=(check,))
    isolation_validity = next(c for c in health_validity(report) if c.name == "health_isolation")
    assert not isolation_validity.passed
    assert "P8" in isolation_validity.detail


def test_an_instructions_message_without_a_block_is_unmeasured() -> None:
    reason = "an instructions message has no <INSTRUCTIONS> block"
    check = run(workflow(audit(unmeasured=[reason])))
    assert check.status == "unmeasured"
    assert reason in check.detail


def test_a_turn_without_its_source_or_a_workflow_record_is_unmeasured() -> None:
    missing = run(workflow(audit()), files={})
    assert missing.status == "unmeasured"
    assert "native audit source missing: rollout.jsonl" in missing.detail
    assert missing.evidence["unmeasured"]
    assert "workflow/workflow.json" in run({"turns": []}).detail
    assert run({"turns": []}).status == "unmeasured"
    assert run(None).status == "unmeasured"
    assert run({"turns": "not a list"}).status == "unmeasured"


def test_a_violation_outranks_missing_evidence_and_findings_are_deduplicated() -> None:
    bad = audit(skill_roots=["/etc/codex/skills"])
    flow = workflow(bad, bad)
    flow["turns"].append({"stage": "build", "turn": 3, "log_dir": LOG})
    check = run(flow)
    assert check.status == "violation"
    assert len(check.evidence["findings"]) == 1
    assert check.evidence["unmeasured"] == [f"{STEP}/build/3: no native session audit"]


def test_many_findings_are_summarised() -> None:
    check = run(workflow(audit(skill_roots=[f"/x/{i}" for i in range(5)])))
    assert check.detail.endswith("and 2 more")
    assert len(check.evidence["findings"]) == 5


def test_no_checked_records_and_no_reason_is_unmeasured_for_no_attempted_step() -> None:
    check = _verdict(0, {}, [], {})
    assert check.status == "unmeasured"
    assert not check.evidence["unmeasured"]


def test_unmeasured_reasons_are_deduplicated_in_the_verdict() -> None:
    check = _verdict(1, {}, ["same", "same", "other"], {})
    assert check.evidence["unmeasured"] == ["same", "other"]


def test_an_agent_override_runs_no_native_session_and_is_unmeasured() -> None:
    stub = SimpleNamespace(record=SimpleNamespace(agent_override="oracle"))
    check = check_p8(cast(Audit, stub))
    assert check.status == "unmeasured"
    assert "oracle" in check.detail


# --- instruction origins and the workspace's own lines ---------------------------------------


def test_agents_lines_must_be_the_workspaces_own_before_the_turn() -> None:
    flow = workflow(audit(instructions=[instruction("be terse\nuse uv\n\n  host secret rule  ")]))
    before = {"AGENTS.md": "be terse\nuse uv\n"}
    found = run(flow, before=before)
    assert found.status == "violation"
    assert "host secret rule" in found.detail
    assert len(found.evidence["findings"]) == 1
    assert run(flow, before=before | {"AGENTS.override.md": "host secret rule\n"}).status == "pass"


def test_the_workspaces_own_text_of_a_folder_above_the_origin_vouches_for_it() -> None:
    flow = workflow(audit(instructions=[instruction("root rule\nsub rule", "/app/sub/dir")]))
    before = {"AGENTS.md": "root rule\n", "sub/CLAUDE.md": "sub rule\n"}
    assert run(flow, before=before).status == "pass"
    assert run(flow, before={"other/AGENTS.md": "root rule\nsub rule"}).status == "violation"


def test_a_long_foreign_line_is_cut_and_the_rest_are_counted() -> None:
    text = "x" * 100 + "\nsecond\nthird"
    found = run(workflow(audit(instructions=[instruction(text)])), before={})
    assert "'" + "x" * 60 + "'" in found.detail
    assert "x" * 61 not in found.detail
    assert len(found.evidence["findings"]) == 1


def test_text_only_the_end_of_the_step_holds_is_not_permitted_before_its_first_turn() -> None:
    flow = workflow(audit(instructions=[instruction("be terse\nlate rule")]))
    found = run(flow, before={"AGENTS.md": "be terse\n"})
    assert found.status == "violation"
    assert "late rule" in found.detail


def test_text_written_after_a_resumed_turn_is_not_vouched_for_by_a_later_snapshot() -> None:
    """Turn 2 merged ``late rule``; nothing from before it proves the agent did not write it."""
    first = audit()
    second = audit(instructions=[instruction("be terse\nlate rule")])
    check = run(workflow(first, second), before={"AGENTS.md": "be terse\n"})
    assert check.status == "unmeasured"
    assert "resumed turn" in check.detail


def test_a_resumed_turn_is_checked_against_placed_text() -> None:
    second = audit(instructions=[instruction("placed rule")])
    flow = workflow(audit(), second, components=[component(b"placed rule\n")])
    files = FILES | {RECORD: b"placed rule\n"}
    assert run(flow, files=files, before={}).status == "pass"


def test_the_first_step_cannot_check_unplaced_text_and_is_unmeasured() -> None:
    check = run(workflow(audit(instructions=[instruction("be terse")])))
    assert check.status == "unmeasured"
    assert "before the first step" in check.detail


def test_matching_text_needs_no_stored_snapshot() -> None:
    flow = workflow(audit(instructions=[instruction("\n  \n")]))
    assert run(flow).status == "pass"
    assert run(workflow(audit(instructions=[{"path": "/app"}]))).status == "pass"


@pytest.mark.parametrize("origin", [None, "", 7])
def test_an_instruction_with_no_recorded_origin_is_unmeasured(origin: Any) -> None:
    flow = workflow(audit(instructions=[instruction("be terse", origin)]))
    check = run(flow, before={"AGENTS.md": "be terse\n"})
    assert check.status == "unmeasured"
    assert "origin" in check.detail


@pytest.mark.parametrize(
    "origin",
    ["/root/.codex", "/application/AGENTS.md", "/app/../etc/AGENTS.md", "relative/AGENTS.md"],
)
def test_an_instruction_from_outside_the_workspace_is_a_violation(origin: str) -> None:
    flow = workflow(audit(instructions=[instruction("be terse", origin)]))
    found = run(flow, before={"AGENTS.md": "be terse\n"})
    assert found.status == "violation"
    assert origin in found.detail


def test_origin_files_name_the_file_or_the_folders_up_to_the_root() -> None:
    assert _origin_files("/app/CLAUDE.md") == [PurePosixPath("CLAUDE.md")]
    assert _origin_files("/app/sub/AGENTS.override.md") == [PurePosixPath("sub/AGENTS.override.md")]
    assert _origin_files("/app") == [
        PurePosixPath(name) for name in ("AGENTS.md", "AGENTS.override.md", "CLAUDE.md")
    ]
    assert _origin_files("/app/a/b") == [
        PurePosixPath(folder) / name
        for folder in ("a/b", "a", ".")
        for name in ("AGENTS.md", "AGENTS.override.md", "CLAUDE.md")
    ]
    assert _origin_files("/elsewhere") is None
    assert _origin_files("/app/../x") is None
    assert _origin_files("app/CLAUDE.md") is None


def test_claude_instructions_are_checked_against_their_own_file() -> None:
    """A line a different workspace file holds does not vouch for CLAUDE.md (one pooled set did)."""
    rule = "use uv\nonly the agents file has this"
    flow = workflow(audit(instructions=[instruction(rule, "/app/CLAUDE.md")]))
    before = {"CLAUDE.md": "use uv\n", "AGENTS.md": "only the agents file has this"}
    check = run(flow, before=before)
    assert check.status == "violation"
    assert "only the agents file has this" in check.detail
    assert run(flow, before={"CLAUDE.md": rule}).status == "pass"


def test_a_claude_instruction_file_outside_the_workspace_is_a_violation() -> None:
    origin = "/root/.claude/CLAUDE.md"
    flow = workflow(audit(instructions=[instruction("use uv", origin)]))
    check = run(flow, before={"CLAUDE.md": "use uv\n"})
    assert check.status == "violation"
    assert origin in check.detail


@pytest.mark.parametrize(
    ("rule", "skills_unmeasured", "status"),
    [
        ("use uv", [], "pass"),
        ("unrecorded host rule", [], "violation"),
        ("use uv", ["claude loaded 2 skill(s) with unknown origin"], "unmeasured"),
    ],
)
def test_normalized_claude_audit_feeds_p8(
    rule: str, skills_unmeasured: list[str], status: str
) -> None:
    message = {"role": "system", "sha256": sha(b"native harness instructions")}
    claude = audit(
        harness="claude-code",
        version="2.1.285",
        instructions=[instruction(rule, "/app/CLAUDE.md")],
        unmeasured=skills_unmeasured,
        messages=[message],
    )
    check = run(workflow(claude), before={"CLAUDE.md": "use uv\n"})
    assert check.status == status, check.detail
    assert check.evidence["messages"]["claude-code@2.1.285"] == [message]


# --- components the workflow placed ----------------------------------------------------------


def test_a_recorded_agents_component_allows_exactly_its_own_lines() -> None:
    flow = workflow(audit(instructions=[instruction("be terse\nplaced rule")]))
    before = {"AGENTS.md": "be terse\n"}
    assert run(flow, before=before).status == "violation"
    flow["components"] = [component(b"placed rule\n")]
    files = FILES | {RECORD: b"placed rule\n"}
    assert run(flow, files=files, before=before).status == "pass"


def test_a_component_placed_in_another_file_does_not_vouch_for_this_one() -> None:
    flow = workflow(
        audit(instructions=[instruction("be terse\nplaced rule")]),
        components=[component(b"placed rule\n", path="docs/NOTES.md")],
    )
    files = FILES | {RECORD: b"placed rule\n"}
    assert run(flow, files=files, before={"AGENTS.md": "be terse\n"}).status == "violation"


def test_placed_text_vouches_on_the_first_step() -> None:
    flow = workflow(
        audit(instructions=[instruction("placed rule")]), components=[component(b"placed rule\n")]
    )
    assert run(flow, files=FILES | {RECORD: b"placed rule\n"}).status == "pass"


def test_a_component_whose_record_is_not_its_digest_is_a_violation() -> None:
    entry = component(b"a skill\n", path=".agents/skills/x/SKILL.md") | {"sha256": "0" * 64}
    found = run(workflow(audit(), components=[entry]), files=FILES | {RECORD: b"a skill\n"})
    assert found.status == "violation"
    assert "not the recorded digest" in found.detail
    assert "component .agents/skills/x/SKILL.md: its record is not the recorded digest" in (
        found.detail
    )


def test_broken_components_name_what_is_wrong_with_each() -> None:
    text = b"placed\n"
    read = reader({RECORD: text})
    entries = [
        component(text),
        component(text, path="../escape.md"),
        component(text, path="/abs.md"),
        component(text, record=None),
        component(text, record="workflow/missing.md"),
        component(b"other\n"),
        {"sha256": sha(text), "record": RECORD},
        {"path": 3, "record": RECORD},
        "not an entry",
    ]
    assert _broken_components({"components": entries}, read) == [
        (PurePosixPath("../escape.md"), "it is placed outside the workspace"),
        (PurePosixPath("/abs.md"), "it is placed outside the workspace"),
        (PurePosixPath("AGENTS.md"), "its record is missing"),
        (PurePosixPath("AGENTS.md"), "its record is missing"),
        (PurePosixPath("AGENTS.md"), "its record is not the recorded digest"),
    ]
    assert _broken_components({"components": "none"}, read) == []
    assert _broken_components({}, read) == []
    assert _broken_components(None, read) == []


def test_placements_keep_only_recorded_relative_files() -> None:
    entries = [
        component(b"", path="AGENTS.md"),
        component(b"", path="/abs.md"),
        component(b"", path="../up.md"),
        component(b"", path="ok.md", record="../record.md"),
        component(b"", path="ok.md", record="/record.md"),
        {"path": "no-record.md"},
        {"record": RECORD},
        "not an entry",
    ]
    assert _placements({"components": entries}) == [
        (PurePosixPath("AGENTS.md"), PurePosixPath(RECORD))
    ]
    assert _placements({"components": "none"}) == []
    assert _placements({}) == []
    assert _placements(["not", "a", "workflow"]) == []


def test_placed_lines_pool_the_records_of_a_file_and_tolerate_a_missing_record() -> None:
    flow = {
        "components": [
            component(b"", record="workflow/a.md"),
            component(b"", record="workflow/b.md"),
            component(b"", path="other.md", record="workflow/gone.md"),
        ]
    }
    records = {"workflow/a.md": b"one\n\n two \n", "workflow/b.md": b"\xff3"}
    placed = _placed_lines(flow, reader(records))
    assert placed == {
        PurePosixPath("AGENTS.md"): {"one", "two", "\ufffd3"},
        PurePosixPath("other.md"): set(),
    }


# --- turn and audit findings -----------------------------------------------------------------


def test_source_findings_flag_missing_changed_and_escaping_sources() -> None:
    log = PurePosixPath(LOG)
    sources = [
        {"path": "ok.jsonl", "sha256": sha(b"ok")},
        {"path": "changed.jsonl", "sha256": sha(b"before")},
        {"path": "gone.jsonl", "sha256": sha(b"x")},
        {"path": "../other.jsonl", "sha256": sha(b"x")},
        {"path": "/etc/passwd", "sha256": sha(b"x")},
    ]
    read = reader({f"{LOG}/ok.jsonl": b"ok", f"{LOG}/changed.jsonl": b"after"})
    assert _source_findings(sources, read, log) == (
        [
            "native audit source changed: changed.jsonl",
            "audit source outside the turn: ../other.jsonl",
            "audit source outside the turn: /etc/passwd",
        ],
        ["native audit source missing: gone.jsonl"],
    )
    assert _source_findings([], read, log) == ([], ["no native audit source files"])


def test_changed_native_audit_source_invalidates_retained_evidence() -> None:
    check = run(workflow(audit()), files={f"{LOG}/rollout.jsonl": SOURCE + b"{}\n"})
    assert check.status == "violation"
    assert "native audit source changed" in check.detail


def test_audit_findings_pool_adapter_reasons_and_flag_empty_evidence() -> None:
    pre = _PreTurn()
    bare = audit(
        records=0,
        sources=[],
        skill_roots=["/app", "/app/skills", "/tmp/x"],
        violations=["adapter violation"],
        unmeasured=["adapter gap"],
    )
    assert _audit_findings(bare, pre, reader({}), PurePosixPath(LOG)) == (
        ["adapter violation", "skill root outside the workspace: /tmp/x"],
        ["no native audit source files", "adapter gap", "no native session records"],
    )
    empty = {"sources": None, "skill_roots": None, "instructions": None, "violations": None}
    assert _audit_findings(empty, pre, reader({}), PurePosixPath(LOG)) == (
        [],
        ["no native audit source files", "no native session records"],
    )


@pytest.mark.parametrize(
    ("turn", "reason"),
    [
        ({"log_dir": LOG}, "no native session audit"),
        ({"log_dir": LOG, "session_audit": "text"}, "no native session audit"),
        ({"session_audit": {}}, "no valid turn log directory"),
        ({"log_dir": 3, "session_audit": {}}, "no valid turn log directory"),
        ({"log_dir": "/abs", "session_audit": {}}, "no valid turn log directory"),
        ({"log_dir": "../up", "session_audit": {}}, "no valid turn log directory"),
    ],
)
def test_a_turn_without_an_audit_or_a_valid_log_directory_is_unmeasured(
    turn: dict[str, Any], reason: str
) -> None:
    assert _turn_findings(turn, _PreTurn(), reader(FILES)) == (0, [], [reason])


def test_a_turn_reports_its_records_and_findings() -> None:
    turn = {"log_dir": LOG, "session_audit": audit(records="3", skill_roots=["/tmp/x"])}
    assert _turn_findings(turn, _PreTurn(), reader(FILES)) == (
        3,
        ["skill root outside the workspace: /tmp/x"],
        [],
    )
    unrecorded = {"log_dir": LOG, "session_audit": {"records": None}}
    assert _turn_findings(unrecorded, _PreTurn(), reader({}))[0] == 0


def test_messages_are_grouped_by_harness_version_and_deduplicated() -> None:
    messages: dict[str, list[dict[str, str]]] = {}
    one = {"role": "system", "sha256": "a"}
    two = {"role": "developer", "sha256": "b"}
    _record_messages(audit(messages=[one, two]), messages)
    _record_messages(audit(messages=[one]), messages)
    _record_messages(audit(harness="claude-code", version="2", messages=None), messages)
    _record_messages("not an audit", messages)
    assert messages == {"codex@1": [one, two], "claude-code@2": []}


def test_step_findings_label_findings_by_step_stage_and_turn_and_sum_records() -> None:
    violations: dict[str, str] = {}
    unmeasured: list[str] = []
    flow = workflow(audit(records=2, skill_roots=["/x"]), audit(records=3, unmeasured=["gap"]))
    checked = _step_findings(STEP, flow, reader(FILES), None, violations, unmeasured, {})
    assert checked == 5
    assert violations == {
        "skill root outside the workspace: /x": (
            f"skill root outside the workspace: /x ({STEP}/build/1)"
        )
    }
    assert unmeasured == [f"{STEP}/build/2: gap"]


# --- thin disk readers -----------------------------------------------------------------------


def test_readers_return_none_for_unreadable_files(tmp_path: Path) -> None:
    (tmp_path / "doc.json").write_text('{"a": 1}')
    (tmp_path / "bad.json").write_text("{")
    (tmp_path / "dir").mkdir()
    (tmp_path / "AGENTS.md").write_bytes(b"line\xff\n")
    assert _json(tmp_path / "doc.json") == {"a": 1}
    assert _json(tmp_path / "bad.json") is None
    assert _json(tmp_path / "missing.json") is None
    agent = _agent_reader(tmp_path)
    assert agent(PurePosixPath("doc.json")) == b'{"a": 1}'
    assert agent(PurePosixPath("missing")) is None
    assert agent(PurePosixPath("dir")) is None
    snapshot = _snapshot_reader(tmp_path)
    assert snapshot(PurePosixPath("AGENTS.md")) == "line\ufffd\n"
    assert snapshot(PurePosixPath("dir")) is None
    assert snapshot(PurePosixPath("CLAUDE.md")) is None


def stored(root: Path, *steps: tuple[str, str | None]) -> Any:
    results = [
        SimpleNamespace(step=name, snapshot=None if path is None else SimpleNamespace(path=path))
        for name, path in steps
    ]
    return SimpleNamespace(
        trial_dir=root, record=SimpleNamespace(step_results=results), attempted=tuple(results)
    )


def test_the_starting_workspace_is_the_previous_steps_stored_snapshot(tmp_path: Path) -> None:
    (tmp_path / "snap").mkdir()
    (tmp_path / "file").write_text("x")
    steps = (("one", "snap"), ("two", None), ("three", "file"), ("four", "gone"), ("five", "snap"))
    trial = stored(tmp_path, *steps)
    assert _stored_snapshot(trial, "one") == tmp_path / "snap"
    assert _stored_snapshot(trial, "two") is None
    assert _stored_snapshot(trial, "three") is None
    assert _stored_snapshot(trial, "four") is None
    assert _stored_snapshot(trial, "unknown") is None
    assert _before_snapshot(trial, "one") is None
    assert _before_snapshot(trial, "two") == tmp_path / "snap"
    assert _before_snapshot(trial, "unknown") is None
    assert _before_snapshot(trial, "five") is None


def test_check_p8_reads_the_workflow_and_the_previous_snapshot(tmp_path: Path) -> None:
    agent = tmp_path / "steps" / "two" / "agent"
    (agent / LOG).mkdir(parents=True)
    (agent / LOG / "rollout.jsonl").write_bytes(SOURCE)
    flow = workflow(audit(instructions=[instruction("be terse\nhost rule")]))
    (agent / "workflow" / "workflow.json").write_text(json.dumps(flow))
    app = tmp_path / "snap"
    app.mkdir()
    (app / "AGENTS.md").write_text("be terse\n")
    trial = stored(tmp_path, ("one", "snap"), ("two", None))
    trial.attempted = trial.attempted[1:]
    trial.record.agent_override = None
    check = check_p8(cast(Audit, trial))
    assert check.status == "violation"
    assert "host rule" in check.detail
    assert "native audit source" not in check.detail
    (app / "AGENTS.md").write_text("be terse\nhost rule\n")
    assert check_p8(cast(Audit, trial)).status == "pass"


# --- the native adapters, driven through their public session audit --------------------------

SKILLS = "<skills_instructions>\n### Skill roots\n{roots}\n</skills_instructions>"
AGENTS = "# AGENTS.md instructions for /app\n\n<INSTRUCTIONS>\n{body}\n</INSTRUCTIONS>"


def roots(*paths: str) -> str:
    return SKILLS.format(roots="\n".join(f"- `r{i}` = `{p}`" for i, p in enumerate(paths)))


def p8_of(turn_dir: Path, harness: Harness, before: Mapping[str, str] | None = None) -> HealthCheck:
    """P8 over the adapter's own audit of ``turn_dir``, its sources read as stored."""
    native = session_audit(harness, turn_dir)
    files = {f"{LOG}/{s['path']}": (turn_dir / s["path"]).read_bytes() for s in native["sources"]}
    return run(workflow(native), files=files, before=before)


def message_row(text: str) -> str:
    row = {
        "type": "response_item",
        "payload": {"type": "message", "role": "user", "content": [{"text": text}]},
    }
    return json.dumps(row)


def rollout(turn_dir: Path, *rows: str) -> None:
    sessions = turn_dir / "sessions" / "2026" / "10" / "26"
    sessions.mkdir(parents=True)
    (sessions / "rollout-2026-10-26T00-00-00-abc.jsonl").write_text("\n".join(rows) + "\n")


def test_a_codex_rollout_of_plain_messages_and_workspace_skills_passes(tmp_path: Path) -> None:
    rollout(tmp_path, message_row(roots(ROOT)), message_row("solve the task"))
    check = p8_of(tmp_path, Harness.CODEX_CLI)
    assert check.status == "pass", check.detail


def test_a_codex_bundled_skill_root_is_a_violation(tmp_path: Path) -> None:
    rollout(tmp_path, message_row(roots(ROOT, "/tmp/codex-home/skills/.system")))
    check = p8_of(tmp_path, Harness.CODEX_CLI)
    assert check.status == "violation"
    assert "/tmp/codex-home/skills/.system" in check.detail


def test_codex_recommended_plugins_are_a_violation(tmp_path: Path) -> None:
    rollout(tmp_path, message_row("<recommended_plugins>\n- github\n</recommended_plugins>"))
    assert p8_of(tmp_path, Harness.CODEX_CLI).status == "violation"


def test_codex_agents_lines_are_checked_against_the_starting_workspace(tmp_path: Path) -> None:
    rollout(tmp_path, message_row(AGENTS.format(body="be terse\nhost secret rule")))
    found = p8_of(tmp_path, Harness.CODEX_CLI, before={"AGENTS.md": "be terse\n"})
    assert found.status == "violation"
    assert "host secret rule" in found.detail
    placed = {"AGENTS.md": "be terse\n", "AGENTS.override.md": "host secret rule\n"}
    assert p8_of(tmp_path, Harness.CODEX_CLI, before=placed).status == "pass"
    assert p8_of(tmp_path, Harness.CODEX_CLI).status == "unmeasured"


def test_a_codex_instructions_message_without_a_block_is_unmeasured(tmp_path: Path) -> None:
    rollout(tmp_path, message_row("# AGENTS.md instructions for /app\n\nno block"))
    assert p8_of(tmp_path, Harness.CODEX_CLI).status == "unmeasured"


@pytest.mark.parametrize(
    "row",
    [
        '{"type": "future_instruction_schema", "payload": {"text": "a rule"}}',
        '{"type": "response_item", "payload": {"type": "future_item", "text": "a rule"}}',
        '{"type": "response_item", "payload": {"type": "message", "role": "user", '
        '"content": [{"image": "x"}]}}',
        "[1, 2]",
        "{truncated",
    ],
)
def test_a_codex_row_that_is_not_understood_leaves_p8_unmeasured(tmp_path: Path, row: str) -> None:
    rollout(tmp_path, message_row("solve the task"), row)
    check = p8_of(tmp_path, Harness.CODEX_CLI)
    assert check.status == "unmeasured"
    assert "not JSON objects" in check.detail


def claude_turn(
    turn_dir: Path,
    rule: str = "use uv",
    skills: list[str] | None = None,
    *,
    origin: str = "/app/CLAUDE.md",
    transcript_tail: str = "",
    stream_tail: str = "",
) -> None:
    """Write a Claude stream init and transcript with instruction and system rows."""
    init = {
        "type": "system",
        "subtype": "init",
        "claude_code_version": "2.1.285",
        "skills": skills or [],
        "plugins": [{"name": "cc-plugin-agents-md", "path": "builtin"}],
        "mcp_servers": [],
    }
    (turn_dir / "claude-code.txt").write_text(json.dumps(init) + "\n" + stream_tail)
    transcript = turn_dir / "sessions" / "projects" / "-app" / "claude.jsonl"
    transcript.parent.mkdir(parents=True)
    content = f"Contents of {origin} (project instructions):\n\n{rule}"
    user = {"type": "user", "message": {"content": content}}
    system = {"type": "system", "content": "native harness instructions"}
    transcript.write_text(json.dumps(user) + "\n" + json.dumps(system) + "\n" + transcript_tail)


@pytest.mark.parametrize(
    ("rule", "skills", "status"),
    [
        ("use uv", [], "pass"),
        ("unrecorded host rule", [], "violation"),
        ("use uv", ["unknown-origin"], "unmeasured"),
    ],
)
def test_claude_native_audit_feeds_p8(
    tmp_path: Path, rule: str, skills: list[str], status: str
) -> None:
    claude_turn(tmp_path, rule, skills)
    check = p8_of(tmp_path, Harness.CLAUDE_CODE, before={"CLAUDE.md": "use uv\n"})
    assert check.status == status, check.detail
    assert check.evidence["messages"]["claude-code@2.1.285"] == [
        {"role": "system", "sha256": sha(b"native harness instructions")}
    ]


def test_a_claude_instruction_file_outside_the_workspace_is_a_violation_from_its_transcript(
    tmp_path: Path,
) -> None:
    claude_turn(tmp_path, origin="/root/.claude/CLAUDE.md")
    check = p8_of(tmp_path, Harness.CLAUDE_CODE, before={"CLAUDE.md": "use uv\n"})
    assert check.status == "violation"
    assert "/root/.claude/CLAUDE.md" in check.detail


def test_claude_transcript_instructions_are_checked_against_their_own_file(tmp_path: Path) -> None:
    claude_turn(tmp_path, "use uv\nonly the agents file has this")
    before = {"CLAUDE.md": "use uv\n", "AGENTS.md": "only the agents file has this\n"}
    check = p8_of(tmp_path, Harness.CLAUDE_CODE, before=before)
    assert check.status == "violation"
    assert "only the agents file has this" in check.detail


@pytest.mark.parametrize(
    "tail",
    [
        "{truncated\n",
        '{"type": "future_instruction_schema", "message": {"content": "a rule"}}\n',
        '{"type": "user", "message": {"content": 7}}\n',
        '{"type": "attachment", "attachment": "text"}\n',
        '{"foo": "bar"}\n',
    ],
)
def test_a_claude_transcript_line_that_is_not_understood_leaves_p8_unmeasured(
    tmp_path: Path, tail: str
) -> None:
    claude_turn(tmp_path, transcript_tail=tail)
    check = p8_of(tmp_path, Harness.CLAUDE_CODE, before={"CLAUDE.md": "use uv\n"})
    assert check.status == "unmeasured"
    assert "not JSON objects" in check.detail


@pytest.mark.parametrize("tail", ['{"type": "future_event"}\n', "unparsed instruction text\n"])
def test_a_malformed_claude_stream_line_leaves_p8_unmeasured(tmp_path: Path, tail: str) -> None:
    claude_turn(tmp_path, stream_tail=tail)
    check = p8_of(tmp_path, Harness.CLAUDE_CODE, before={"CLAUDE.md": "use uv\n"})
    assert check.status == "unmeasured"
    assert "claude stream" in check.detail


def test_a_claude_turn_without_a_stream_init_is_unmeasured(tmp_path: Path) -> None:
    claude_turn(tmp_path)
    (tmp_path / "claude-code.txt").write_text("")
    check = p8_of(tmp_path, Harness.CLAUDE_CODE, before={"CLAUDE.md": "use uv\n"})
    assert check.status == "unmeasured"
    assert "no system init event" in check.detail
