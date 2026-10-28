"""P8: the agent saw only the workspace's skills and instructions."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from raidar.engine.health.audit import audit_trial
from raidar.engine.health.model import HealthCheck
from raidar.engine.trial.collect import health_validity
from raidar.subject.agent.config import Harness
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


def test_agents_lines_must_be_the_workspaces_own(tmp_path: Path) -> None:
    prompt = AGENTS.format(body="be terse\n--- project-doc ---\n\nuse uv\nhost secret rule")
    t = session(tmp_path, prompt)
    app = t.trial_dir / "steps" / STEP / "artifacts" / "app"
    (app / "AGENTS.md").write_text("be terse\nuse uv\n")
    found = p8(t)
    assert found.status == "violation"
    assert "host secret rule" in found.detail
    (app / "AGENTS.override.md").write_text("host secret rule\n")
    assert p8(t).status == "pass"


def place_component(t: HealthyTrial, path: str, text: str, sha256: str | None = None) -> None:
    agent = t.trial_dir / "steps" / STEP / "agent"
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
    t = session(tmp_path, AGENTS.format(body="be terse\nplaced rule"))
    (t.trial_dir / "steps" / STEP / "artifacts" / "app" / "AGENTS.md").write_text("be terse\n")
    assert p8(t).status == "violation"
    place_component(t, "AGENTS.md", "placed rule\n")
    assert p8(t).status == "pass"


def test_a_component_whose_record_is_not_its_digest_is_a_violation(tmp_path: Path) -> None:
    t = session(tmp_path, roots("/app/.agents/skills"))
    place_component(t, ".agents/skills/x/SKILL.md", "a skill\n", sha256="0" * 64)
    found = p8(t)
    assert found.status == "violation"
    assert "not the recorded digest" in found.detail


def test_agents_text_without_a_stored_snapshot_is_unmeasured(tmp_path: Path) -> None:
    t = session(tmp_path, AGENTS.format(body="be terse"))
    shutil.rmtree(t.trial_dir / "steps" / STEP / "artifacts")
    check = p8(t)
    assert check.status == "unmeasured"
    assert "snapshot" in check.detail


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
    trial = healthy_trial(tmp_path)
    agent = trial.trial_dir / "steps" / STEP / "agent"
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
        "message": {"content": f"Contents of /app/CLAUDE.md (project instructions):\n\n{rule}"},
    }
    system = {"type": "system", "content": "native harness instructions"}
    transcript.write_text(json.dumps(message) + "\n" + json.dumps(system) + "\n")
    (trial.trial_dir / "steps" / STEP / "artifacts" / "app" / "CLAUDE.md").write_text("use uv\n")
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

    check = p8(trial)

    assert check.status == status, check.detail
    assert check.evidence["messages"]["claude-code@2.1.285"] == [
        {"role": "system", "sha256": hashlib.sha256(b"native harness instructions").hexdigest()}
    ]


def test_changed_native_audit_source_invalidates_retained_evidence(tmp_path: Path) -> None:
    trial = session(tmp_path, "solve the task")
    source = next(trial.trial_dir.rglob("rollout-*.jsonl"))
    source.write_text(source.read_text() + "{}\n")

    check = p8(trial)

    assert check.status == "violation"
    assert "native audit source changed" in check.detail
