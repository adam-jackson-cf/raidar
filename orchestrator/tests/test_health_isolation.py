"""P8: the agent saw only the workspace's skills and instructions."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from raidar.engine.health.audit import audit_trial
from raidar.engine.health.model import HealthCheck
from raidar.engine.trial.collect import health_validity
from raidar.subject.agent.config import Harness
from tests.execution_support import StepOutcome
from tests.health_support import STEP, HealthyTrial, healthy_trial, write_agent_session
from tests.native_support import session_audit

SKILLS = "<skills_instructions>\n### Skill roots\n{roots}\n</skills_instructions>"
AGENTS = "# AGENTS.md instructions for /app\n\n<INSTRUCTIONS>\n{body}\n</INSTRUCTIONS>"


def roots(*paths: str) -> str:
    return SKILLS.format(roots="\n".join(f"- `r{i}` = `{p}`" for i, p in enumerate(paths)))


def p8(t: HealthyTrial) -> HealthCheck:
    report = audit_trial(t.job_dir, t.trial_dir, t.record, t.job())
    return next(c for c in report.checks if c.id == "P8")


def session(tmp_path: Path, *rows: str) -> HealthyTrial:
    t = healthy_trial(tmp_path)
    write_agent_session(t.trial_dir, STEP, list(rows))
    return t


def test_workspace_skills_and_a_plain_prompt_pass(tmp_path: Path) -> None:
    check = p8(session(tmp_path, roots("/app/.agents/skills"), "solve the task"))
    assert check.status == "pass", check.detail


def test_a_bundled_skill_root_is_a_violation_naming_root_and_turn(tmp_path: Path) -> None:
    check = p8(session(tmp_path, roots("/app/.agents/skills", "/tmp/codex-home/skills/.system")))
    assert check.status == "violation"
    assert "/tmp/codex-home/skills/.system" in check.detail
    assert f"{STEP}/build/1" in check.detail


def test_recommended_plugins_are_a_violation_and_invalidate_the_trial(tmp_path: Path) -> None:
    t = session(tmp_path, "<recommended_plugins>\n- github\n</recommended_plugins>")
    check = p8(t)
    assert check.status == "violation"
    report = audit_trial(t.job_dir, t.trial_dir, t.record, t.job())
    isolation = next(c for c in health_validity(report) if c.name == "health_isolation")
    assert not isolation.passed
    assert "P8" in isolation.detail


SECOND = "checkpoint_2"


def second_step(tmp_path: Path, *rows: str) -> HealthyTrial:
    """A two-step trial whose second step ran ``rows``; step one's snapshot is its start state."""
    t = healthy_trial(tmp_path, [StepOutcome(STEP), StepOutcome(SECOND)])
    write_agent_session(t.trial_dir, SECOND, list(rows))
    return t


def snapshot(t: HealthyTrial, step: str) -> Path:
    return t.trial_dir / "steps" / step / "artifacts" / "app"


def test_agents_lines_must_be_the_workspaces_own_before_the_turn(tmp_path: Path) -> None:
    prompt = AGENTS.format(body="be terse\n--- project-doc ---\n\nuse uv\nhost secret rule")
    t = second_step(tmp_path, prompt)
    (snapshot(t, STEP) / "AGENTS.md").write_text("be terse\nuse uv\n")
    found = p8(t)
    assert found.status == "violation"
    assert "host secret rule" in found.detail
    (snapshot(t, STEP) / "AGENTS.override.md").write_text("host secret rule\n")
    assert p8(t).status == "pass"


def test_text_only_the_end_of_the_step_holds_is_not_permitted_before_its_first_turn(
    tmp_path: Path,
) -> None:
    t = second_step(tmp_path, AGENTS.format(body="be terse\nlate rule"))
    (snapshot(t, STEP) / "AGENTS.md").write_text("be terse\n")
    (snapshot(t, SECOND) / "AGENTS.md").write_text("be terse\nlate rule\n")
    found = p8(t)
    assert found.status == "violation"
    assert "late rule" in found.detail


def test_text_written_after_a_resumed_turn_is_not_vouched_for_by_the_final_snapshot(
    tmp_path: Path,
) -> None:
    """Turn 2 merged ``late rule``; the step's final snapshot holds it, but that proves no order."""
    t = second_step(tmp_path, AGENTS.format(body="be terse\nlate rule"))
    (snapshot(t, STEP) / "AGENTS.md").write_text("be terse\n")
    (snapshot(t, SECOND) / "AGENTS.md").write_text("be terse\nlate rule\n")
    flow_path = t.trial_dir / "steps" / SECOND / "agent" / "workflow" / "workflow.json"
    flow = json.loads(flow_path.read_text())
    first = json.loads(json.dumps(flow["turns"][0]))
    first["session_audit"]["instructions"] = []
    flow["turns"] = [first, {**flow["turns"][0], "turn": 2}]
    flow_path.write_text(json.dumps(flow))
    check = p8(t)
    assert check.status == "unmeasured"
    assert "resumed turn" in check.detail


def test_the_first_step_cannot_check_unplaced_text_and_is_unmeasured(tmp_path: Path) -> None:
    t = session(tmp_path, AGENTS.format(body="be terse"))
    (snapshot(t, STEP) / "AGENTS.md").write_text("be terse\n")  # only the end of the step
    check = p8(t)
    assert check.status == "unmeasured"
    assert "before the first step" in check.detail


def rewrite_audits(t: HealthyTrial, change: Callable[[dict[str, Any]], None]) -> None:
    """Apply ``change`` to every turn's recorded ``session_audit`` (as another adapter would)."""
    for flow in t.trial_dir.rglob("workflow.json"):
        document = json.loads(flow.read_text())
        for turn in document["turns"]:
            change(turn["session_audit"])
        flow.write_text(json.dumps(document))


def test_an_instruction_with_no_recorded_origin_is_unmeasured(tmp_path: Path) -> None:
    t = second_step(tmp_path, AGENTS.format(body="be terse"))
    (snapshot(t, STEP) / "AGENTS.md").write_text("be terse\n")
    assert p8(t).status == "pass"

    def forget(audit: dict[str, Any]) -> None:
        for instruction in audit["instructions"]:
            instruction["path"] = None

    rewrite_audits(t, forget)
    check = p8(t)
    assert check.status == "unmeasured"
    assert "origin" in check.detail


def test_an_instruction_from_outside_the_workspace_is_a_violation(tmp_path: Path) -> None:
    t = second_step(tmp_path, AGENTS.format(body="be terse"))
    (snapshot(t, STEP) / "AGENTS.md").write_text("be terse\n")

    def move(audit: dict[str, Any]) -> None:
        for instruction in audit["instructions"]:
            instruction["path"] = "/root/.codex"

    rewrite_audits(t, move)
    found = p8(t)
    assert found.status == "violation"
    assert "/root/.codex" in found.detail


def place_component(
    t: HealthyTrial, path: str, text: str, sha256: str | None = None, step: str = STEP
) -> None:
    agent = t.trial_dir / "steps" / step / "agent"
    record = agent / "workflow" / "components" / "rules.md"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(text)
    flow = json.loads((agent / "workflow" / "workflow.json").read_text())
    digest = sha256 or hashlib.sha256(text.encode()).hexdigest()
    flow["components"] = [
        {"path": path, "sha256": digest, "record": "workflow/components/rules.md"}
    ]
    (agent / "workflow" / "workflow.json").write_text(json.dumps(flow))


def test_a_recorded_agents_component_allows_exactly_its_own_lines(tmp_path: Path) -> None:
    t = second_step(tmp_path, AGENTS.format(body="be terse\nplaced rule"))
    (snapshot(t, STEP) / "AGENTS.md").write_text("be terse\n")
    assert p8(t).status == "violation"
    place_component(t, "AGENTS.md", "placed rule\n", step=SECOND)
    assert p8(t).status == "pass"


def test_a_component_placed_in_another_file_does_not_vouch_for_this_one(tmp_path: Path) -> None:
    t = second_step(tmp_path, AGENTS.format(body="be terse\nplaced rule"))
    (snapshot(t, STEP) / "AGENTS.md").write_text("be terse\n")
    place_component(t, "docs/NOTES.md", "placed rule\n", step=SECOND)
    assert p8(t).status == "violation"


def test_placed_text_vouches_on_the_first_step(tmp_path: Path) -> None:
    t = session(tmp_path, AGENTS.format(body="placed rule"))
    place_component(t, "AGENTS.md", "placed rule\n")
    assert p8(t).status == "pass"


def test_a_component_whose_record_is_not_its_digest_is_a_violation(tmp_path: Path) -> None:
    t = session(tmp_path, roots("/app/.agents/skills"))
    place_component(t, ".agents/skills/x/SKILL.md", "a skill\n", sha256="0" * 64)
    found = p8(t)
    assert found.status == "violation"
    assert "not the recorded digest" in found.detail


def test_agents_text_without_a_stored_snapshot_is_unmeasured(tmp_path: Path) -> None:
    t = second_step(tmp_path, AGENTS.format(body="be terse"))
    shutil.rmtree(t.trial_dir / "steps" / STEP / "artifacts")
    check = p8(t)
    assert check.status == "unmeasured"
    assert "before the first step" in check.detail


def test_an_instructions_message_without_a_block_is_unmeasured(tmp_path: Path) -> None:
    check = p8(session(tmp_path, "# AGENTS.md instructions for /app\n\nno block"))
    assert check.status == "unmeasured"


def test_a_turn_without_a_rollout_or_a_workflow_record_is_unmeasured(tmp_path: Path) -> None:
    t = session(tmp_path, "solve the task")
    agent = t.trial_dir / "steps" / STEP / "agent"
    shutil.rmtree(agent / "workflow" / "build-1")
    missing = p8(t)
    assert missing.status == "unmeasured"
    assert missing.evidence["unmeasured"]
    (agent / "workflow" / "workflow.json").write_text(json.dumps({"turns": []}))
    assert "workflow.json" in p8(t).detail
    assert p8(t).status == "unmeasured"


def test_a_violation_outranks_missing_evidence_and_findings_are_deduplicated(
    tmp_path: Path,
) -> None:
    bad = roots("/etc/codex/skills")
    t = session(tmp_path, bad, bad)
    agent = t.trial_dir / "steps" / STEP / "agent"
    turns = [
        {"stage": "build", "turn": 1, "log_dir": "workflow/build-1"},
        {"stage": "build", "turn": 2, "log_dir": "workflow/build-2"},
    ]
    workflow_path = agent / "workflow" / "workflow.json"
    original = json.loads(workflow_path.read_text())
    turns[0]["session_audit"] = original["turns"][0]["session_audit"]
    workflow_path.write_text(json.dumps({"turns": turns}))
    check = p8(t)
    assert check.status == "violation"
    assert len(check.evidence["findings"]) == 1
    assert check.evidence["unmeasured"]


def test_many_findings_are_summarised(tmp_path: Path) -> None:
    check = p8(session(tmp_path, roots(*(f"/x/{i}" for i in range(5)))))
    assert check.detail.endswith("and 2 more")
    assert len(check.evidence["findings"]) == 5


def test_an_agent_override_runs_no_native_session_and_is_unmeasured(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    record = t.record.model_copy(update={"agent_override": "oracle"})
    report = audit_trial(t.job_dir, t.trial_dir, record, t.job())
    check = next(c for c in report.checks if c.id == "P8")
    assert check.status == "unmeasured"
    assert "oracle" in check.detail


def claude_trial(
    tmp_path: Path,
    rule: str,
    skills: list[str],
    *,
    origin: str = "/app/CLAUDE.md",
    transcript_tail: str = "",
) -> HealthyTrial:
    """A two-step trial whose second step ran claude; step one ended with CLAUDE.md = use uv."""
    trial = healthy_trial(tmp_path, [StepOutcome(STEP), StepOutcome(SECOND)])
    agent = trial.trial_dir / "steps" / SECOND / "agent"
    turn_dir = agent / "workflow" / "build-1"
    stream = {
        "type": "system",
        "subtype": "init",
        "claude_code_version": "2.1.285",
        "skills": skills,
        "plugins": [{"name": "cc-plugin-agents-md", "path": "builtin"}],
        "mcp_servers": [],
    }
    (turn_dir / "claude-code.txt").write_text(json.dumps(stream) + "\n")
    transcript = turn_dir / "sessions" / "projects" / "-app" / "claude.jsonl"
    transcript.parent.mkdir(parents=True)
    message = {
        "type": "user",
        "message": {"content": f"Contents of {origin} (project instructions):\n\n{rule}"},
    }
    system = {"type": "system", "content": "native harness instructions"}
    transcript.write_text(json.dumps(message) + "\n" + json.dumps(system) + "\n" + transcript_tail)
    (snapshot(trial, STEP) / "CLAUDE.md").write_text("use uv\n")
    workflow = {
        "turns": [
            {
                "stage": "build",
                "turn": 1,
                "log_dir": "workflow/build-1",
                "session_audit": session_audit(Harness.CLAUDE_CODE, turn_dir),
            }
        ]
    }
    (agent / "workflow" / "workflow.json").write_text(json.dumps(workflow))
    return trial


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
    trial = claude_trial(tmp_path, rule, skills)

    check = p8(trial)

    assert check.status == status, check.detail
    assert check.evidence["messages"]["claude-code@2.1.285"] == [
        {"role": "system", "sha256": hashlib.sha256(b"native harness instructions").hexdigest()}
    ]


def test_claude_instructions_are_checked_against_their_own_file(tmp_path: Path) -> None:
    """A line a different workspace file holds does not vouch for CLAUDE.md (one pooled set did)."""
    trial = claude_trial(tmp_path, "use uv\nonly the agents file has this", [])
    (snapshot(trial, STEP) / "AGENTS.md").write_text("only the agents file has this\n")
    check = p8(trial)
    assert check.status == "violation"
    assert "only the agents file has this" in check.detail


def test_a_claude_instruction_file_outside_the_workspace_is_a_violation(tmp_path: Path) -> None:
    check = p8(claude_trial(tmp_path, "use uv", [], origin="/root/.claude/CLAUDE.md"))
    assert check.status == "violation"
    assert "/root/.claude/CLAUDE.md" in check.detail


def test_a_transcript_line_that_is_not_parsed_leaves_p8_unmeasured(tmp_path: Path) -> None:
    check = p8(claude_trial(tmp_path, "use uv", [], transcript_tail="{truncated\n"))
    assert check.status == "unmeasured"
    assert "not JSON objects" in check.detail


@pytest.mark.parametrize(
    "tail",
    [
        '{"type": "future_instruction_schema", "message": {"content": "a rule"}}\n',
        '{"type": "user", "message": {"content": 7}}\n',
        '{"type": "attachment", "attachment": "text"}\n',
    ],
)
def test_a_claude_event_of_an_unknown_type_or_shape_leaves_p8_unmeasured(
    tmp_path: Path, tail: str
) -> None:
    check = p8(claude_trial(tmp_path, "use uv", [], transcript_tail=tail))
    assert check.status == "unmeasured"
    assert "not JSON objects" in check.detail


@pytest.mark.parametrize("tail", ['{"type": "future_event"}\n', "unparsed instruction text\n"])
def test_a_malformed_claude_stream_line_leaves_p8_unmeasured(tmp_path: Path, tail: str) -> None:
    trial = claude_trial(tmp_path, "use uv", [])
    turn_dir = trial.trial_dir / "steps" / SECOND / "agent" / "workflow" / "build-1"
    stream = turn_dir / "claude-code.txt"
    stream.write_text(stream.read_text() + tail)
    flow_path = stream.parents[1] / "workflow.json"
    flow = json.loads(flow_path.read_text())
    flow["turns"][0]["session_audit"] = session_audit(Harness.CLAUDE_CODE, stream.parent)
    flow_path.write_text(json.dumps(flow))
    check = p8(trial)
    assert check.status == "unmeasured"
    assert "claude stream" in check.detail


@pytest.mark.parametrize(
    "row",
    [
        {"type": "future_instruction_schema", "payload": {"text": "a rule"}},
        {"type": "response_item", "payload": {"type": "future_item", "text": "a rule"}},
        {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": [{"image": "x"}]},
        },
    ],
)
def test_a_rollout_row_of_an_unknown_type_or_message_variant_leaves_p8_unmeasured(
    tmp_path: Path, row: dict[str, Any]
) -> None:
    trial = session(tmp_path, "solve the task")
    agent = trial.trial_dir / "steps" / STEP / "agent"
    rollout = next(trial.trial_dir.rglob("rollout-*.jsonl"))
    rollout.write_text(rollout.read_text() + json.dumps(row) + "\n")
    flow_path = agent / "workflow" / "workflow.json"
    flow = json.loads(flow_path.read_text())
    turn_dir = agent / "workflow" / "build-1"
    flow["turns"][0]["session_audit"] = session_audit(Harness.CODEX_CLI, turn_dir)
    flow_path.write_text(json.dumps(flow))
    check = p8(trial)
    assert check.status == "unmeasured"
    assert "not JSON objects" in check.detail


def test_a_valid_json_line_of_an_unsupported_shape_leaves_p8_unmeasured(tmp_path: Path) -> None:
    check = p8(claude_trial(tmp_path, "use uv", [], transcript_tail='{"foo": "bar"}\n'))
    assert check.status == "unmeasured"
    assert "not JSON objects" in check.detail


def test_a_rollout_line_that_is_not_parsed_leaves_p8_unmeasured(tmp_path: Path) -> None:
    trial = session(tmp_path, "solve the task")
    agent = trial.trial_dir / "steps" / STEP / "agent"
    rollout = next(trial.trial_dir.rglob("rollout-*.jsonl"))
    rollout.write_text(rollout.read_text() + "[1, 2]\n")
    flow_path = agent / "workflow" / "workflow.json"
    flow = json.loads(flow_path.read_text())
    turn_dir = agent / "workflow" / "build-1"
    flow["turns"][0]["session_audit"] = session_audit(Harness.CODEX_CLI, turn_dir)
    flow_path.write_text(json.dumps(flow))
    check = p8(trial)
    assert check.status == "unmeasured"
    assert "not JSON objects" in check.detail


def test_changed_native_audit_source_invalidates_retained_evidence(tmp_path: Path) -> None:
    trial = session(tmp_path, "solve the task")
    source = next(trial.trial_dir.rglob("rollout-*.jsonl"))
    source.write_text(source.read_text() + "{}\n")

    check = p8(trial)

    assert check.status == "violation"
    assert "native audit source changed" in check.detail
