"""process-compliance@1 behaviour: deterministic trace checks, Jev residue, eligibility."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from raidar.jev.audit import DecisionStage
from raidar.jev.policies import PolicyMode
from raidar.jev.process import PROCESS_POLICY
from raidar.jev.protocol import JEV_PINNED_MODEL
from raidar.jev.runtime import JevRuntime
from raidar.scorers.catalog import default_scorer_registry
from raidar.scorers.process_compliance import (
    ProcessComplianceScorer,
    process_compliance_scorer_for_store,
)
from raidar.scorers.sdk.context import AssessContext
from raidar.scorers.sdk.result import AssessResult, AssessStatus, EvidenceSource, Observation
from raidar.scorers.sdk.validation import validate_result
from tests.jev_support import RecordedTransport, make_runtime, noul_answer, policy
from tests.trace_trial_support import (
    TracedStep,
    agent_step,
    codex_exec,
    codex_patch,
    delegate,
    session,
    tool_call,
    traced_context,
    trajectory,
)

OK, NA = AssessStatus.OK, AssessStatus.NOT_APPLICABLE
AMBIGUOUS = AssessStatus.INSUFFICIENT_EVIDENCE
PLAN = tool_call("p0", "update_plan", {"plan": [{"step": "write"}]})
EDIT = codex_patch("e0", "/app/app/main.py")


def _assess(ctx: AssessContext, jev: JevRuntime | None = None) -> AssessResult:
    scorer = ProcessComplianceScorer(jev)
    return validate_result(scorer.manifest, ctx, scorer.assess(ctx))


def _by_key(result: AssessResult) -> dict[tuple[str, str], Observation]:
    return {(item.metric, item.subject): item for item in result.observations}


def _state(item: Observation) -> tuple[AssessStatus, float | None]:
    return item.status, item.value


def _ctx(tmp_path: Path, *traces: dict, **config: Any) -> AssessContext:
    steps = [
        TracedStep(f"checkpoint_{index}", trace=trace) for index, trace in enumerate(traces, 1)
    ]
    return traced_context(tmp_path, steps, **config)


def _runtime(root: Path, mode: PolicyMode = PolicyMode.SHADOW, **kwargs: Any) -> JevRuntime:
    return make_runtime(root, policies=(policy(PROCESS_POLICY, mode),), **kwargs)


def test_checks_decide_order_of_edits_tests_and_plans_across_harnesses(tmp_path: Path) -> None:
    codex_good = trajectory(
        agent_step(1, PLAN),
        agent_step(2, codex_patch("e1", "/app/app/main.py")),
        agent_step(3, codex_exec("t1", "uv run pytest -q")),
    )
    claude_bad = trajectory(
        agent_step(1, tool_call("t2", "Bash", {"command": "pytest -q"})),
        agent_step(2, tool_call("e2", "Edit", {"file_path": "/app/app/main.py", "old": "a"})),
        agent_step(3, tool_call("p2", "TodoWrite", {"todos": ["later"]})),
    )
    no_edit = trajectory(
        agent_step(1, codex_exec("r1", "cat app/main.py > /tmp/copy.txt 2>&1")),
        agent_step(2, codex_exec("i1", "pip install pytest")),
    )
    observed = _by_key(_assess(_ctx(tmp_path, codex_good, claude_bad, no_edit)))

    assert _state(observed["tests_run_before_finish", "checkpoint_1"]) == (OK, 1.0)
    assert _state(observed["plan_written", "checkpoint_1"]) == (OK, 1.0)
    assert _state(observed["tests_run_before_finish", "checkpoint_2"]) == (OK, 0.0)
    assert _state(observed["plan_written", "checkpoint_2"]) == (OK, 0.0)
    # Writes outside the workspace and installs are neither edits nor test runs.
    assert observed["tests_run_before_finish", "checkpoint_3"].status is NA
    assert observed["plan_written", "checkpoint_3"].status is NA
    rate = observed["compliance_rate", "trajectory"]
    assert (rate.value, rate.numerator, rate.denominator) == (0.5, 2, 4)
    assert observed["jev_ambiguous_compliance", "checkpoint_1"].status is NA


def _claude(call_id: str, name: str, **arguments: Any) -> dict[str, Any]:
    return tool_call(call_id, name, arguments)


@pytest.mark.parametrize(
    ("calls", "expected"),
    [
        # What an edit writes is not what it runs: test files mention pytest.
        (
            [PLAN, codex_patch("e1", "/app/tests/test_x.py", "+import pytest")],
            {"tests_run_before_finish": 0.0, "plan_written": 1.0},
        ),
        (
            [
                _claude("p1", "TodoWrite", todos=["x"]),
                _claude("e1", "Write", file_path="/app/test_y.py", content="import pytest"),
            ],
            {"tests_run_before_finish": 0.0, "plan_written": 1.0},
        ),
        # Reading about the test runner is not running it.
        ([PLAN, EDIT, codex_exec("r1", "cat pytest.ini")], {"tests_run_before_finish": 0.0}),
        ([PLAN, EDIT, codex_exec("r1", "pip show pytest")], {"tests_run_before_finish": 0.0}),
        (
            [PLAN, EDIT, _claude("r1", "Bash", command="ls", description="Run pytest")],
            {"tests_run_before_finish": 0.0},
        ),
        # Order inside one call is kept: tests, then an edit, is a failure.
        (
            [PLAN, codex_exec("x1", "pytest -q; cat > /app/a.py <<'EOF'\nx = 1\nEOF")],
            {"tests_run_before_finish": 0.0},
        ),
        (
            [PLAN, codex_exec("x1", "cat > /app/a.py <<'EOF'\nimport pytest\nEOF\npytest -q")],
            {"tests_run_before_finish": 1.0},
        ),
        # Comparisons in scripts and scratch output are not source edits.
        (
            [
                PLAN,
                EDIT,
                codex_exec("t1", "pytest -q"),
                codex_exec("c1", 'python -c "print(1 > 0)"'),
            ],
            {"tests_run_before_finish": 1.0},
        ),
        (
            [codex_exec("l1", "ls -R > files.txt"), PLAN, EDIT, codex_exec("t1", "pytest -q")],
            {"plan_written": 1.0, "tests_run_before_finish": 1.0},
        ),
        # Test selection by keyword or path is still a test run.
        ([PLAN, EDIT, codex_exec("t1", "pytest -k add")], {"tests_run_before_finish": 1.0}),
        (
            [
                PLAN,
                EDIT,
                _claude("t1", "Bash", command="cd /app && timeout 60 uv run pytest tests/add"),
            ],
            {"tests_run_before_finish": 1.0},
        ),
        (
            [
                PLAN,
                EDIT,
                tool_call("t1", "shell", {"command": ["bash", "-lc", "python3 -m pytest"]}),
            ],
            {"tests_run_before_finish": 1.0},
        ),
    ],
)
def test_only_what_a_call_runs_counts_as_a_test_run(
    tmp_path: Path, calls: list[dict[str, Any]], expected: dict[str, float]
) -> None:
    trace = trajectory(*(agent_step(index, call) for index, call in enumerate(calls, 1)))
    observed = _by_key(_assess(_ctx(tmp_path, trace)))
    for metric, value in expected.items():
        assert _state(observed[metric, "checkpoint_1"]) == (OK, value), metric


def test_interpreter_scripts_that_write_files_are_ambiguous(tmp_path: Path) -> None:
    script = "python - <<'PY'\nopen('app/main.py', 'w').write('x')\nPY"
    written = "cat > app/io.py <<'EOF'\nopen('out', 'w')\nEOF"
    trace = trajectory(
        agent_step(1, PLAN),
        agent_step(2, codex_exec("t1", "pytest -q")),
        agent_step(3, codex_exec("s1", script)),
    )
    plain = trajectory(
        agent_step(1, PLAN),
        agent_step(2, codex_exec("w1", written)),
        agent_step(3, codex_exec("t1", "pytest -q")),
    )
    observed = _by_key(_assess(_ctx(tmp_path, trace, plain)))
    assert observed["tests_run_before_finish", "checkpoint_1"].status is AMBIGUOUS
    # A file whose content mentions open(..., 'w') is a plain edit.
    assert _state(observed["tests_run_before_finish", "checkpoint_2"]) == (OK, 1.0)


def test_shell_writes_plan_files_and_evidence(tmp_path: Path) -> None:
    trace = trajectory(
        agent_step(1, codex_exec("p1", "cat > PLAN.md <<'EOF'\n1. add\nEOF")),
        agent_step(2, codex_exec("e1", "cat > app/main.py <<'EOF'\nx = 1\nEOF")),
        agent_step(3, codex_exec("t1", "python -m pytest && echo done")),
    )
    step = TracedStep(
        "checkpoint_1", trace=trace, files={"app/main.py": "x = 1\n", "PLAN.md": "1. add\n"}
    )
    result = _assess(traced_context(tmp_path, [step]))
    observed = _by_key(result)
    assert _state(observed["plan_written", "checkpoint_1"]) == (OK, 1.0)
    assert _state(observed["tests_run_before_finish", "checkpoint_1"]) == (OK, 1.0)

    lines = (tmp_path / "trial/steps/checkpoint_1/agent/trajectory.json").read_text().splitlines()
    trace_evidence = {
        item.metric: item for item in result.evidence if item.source is EvidenceSource.TRACE
    }
    test_line = trace_evidence["tests_run_before_finish"].spans[0].line
    assert '"t1"' in lines[test_line - 1]
    plan_file = [item for item in result.evidence if item.source is EvidenceSource.SNAPSHOT]
    assert [item.path for item in plan_file] == ["PLAN.md"]


def test_review_loop_and_nested_sub_agent_sessions_count_for_the_step(tmp_path: Path) -> None:
    implement = session("implement", agent_step(1, codex_patch("e1", "app/main.py")))
    tester = session("tester", agent_step(1, codex_exec("t1", "pytest")))
    review = session("review", delegate("tester", source="agent"), subagents=[tester])
    trace = trajectory(delegate("implement"), delegate("review"), subagents=[review, implement])
    observed = _by_key(_assess(_ctx(tmp_path, trace)))
    assert _state(observed["tests_run_before_finish", "checkpoint_1"]) == (OK, 1.0)


def _ambiguous_trace() -> dict:
    return trajectory(
        agent_step(1, message="Plan:\n1. edit main\n2. run tests"),
        agent_step(2, codex_patch("e1", "/app/app/main.py")),
        agent_step(3, codex_exec("t1", "pytest -q")),
        agent_step(4, codex_exec("s1", "sed -i 's/a/b/' app/main.py")),
    )


def _answers(question_id: str, _question: dict[str, Any]) -> dict[str, Any]:
    return noul_answer({"plan_before_edit": 0.9, "tests_run_before_finish": 0.6}[question_id])


def test_ambiguous_checks_go_to_the_jev_residue_and_every_decision_is_audited(
    tmp_path: Path,
) -> None:
    decided = trajectory(
        agent_step(1, PLAN), agent_step(2, EDIT), agent_step(3, codex_exec("t1", "pytest"))
    )
    ctx = _ctx(tmp_path / "trial", _ambiguous_trace(), decided)
    unbound = _by_key(_assess(ctx))
    for metric in ("tests_run_before_finish", "plan_written"):
        item = unbound[metric, "checkpoint_1"]
        assert (item.status, item.value) == (AMBIGUOUS, None)
    jev_missing = unbound["jev_ambiguous_compliance", "checkpoint_1"]
    assert jev_missing.status is AssessStatus.UNAVAILABLE
    assert "no Jev runtime" in (jev_missing.reason or "")
    assert _state(unbound["compliance_rate", "trajectory"]) == (OK, 1.0)

    transport = RecordedTransport(answer_for=_answers)
    runtime = _runtime(tmp_path / "store", transport=transport)
    result = _assess(ctx, runtime)
    observed = _by_key(result)
    # The step complied only if it complied with each ambiguous check.
    assert _state(observed["jev_ambiguous_compliance", "checkpoint_1"]) == (OK, 0.6)
    assert observed["jev_ambiguous_compliance", "checkpoint_2"].status is NA
    assert result.provenance.jev_model_resolved == JEV_PINNED_MODEL
    assert result.provenance.policy_modes == {"jev_ambiguous_compliance": "shadow"}
    asked = [set(call["body"]["questions"]) for call in transport.calls]
    assert asked == [{"tests_run_before_finish"}, {"plan_before_edit"}]

    records = runtime.audit.decisions(PROCESS_POLICY)
    stages = sorted((record.subject, record.stage) for record in records)
    assert stages == [
        ("checkpoint_1:plan_before_edit", DecisionStage.JEV),
        ("checkpoint_1:tests_run_before_finish", DecisionStage.JEV),
        ("checkpoint_2:plan_before_edit", DecisionStage.DETERMINISTIC),
        ("checkpoint_2:tests_run_before_finish", DecisionStage.DETERMINISTIC),
    ]
    assert all(not record.counts_toward_verdicts for record in records)

    replayed = _assess(ctx, _runtime(tmp_path / "store", replay_only=True))
    assert replayed.observations == result.observations


@pytest.mark.parametrize(
    ("mode", "transport", "reason"),
    [
        (PolicyMode.OFF, RecordedTransport(), "skipped under the off policy"),
        (PolicyMode.SHADOW, None, "missing_key"),
        (PolicyMode.SHADOW, RecordedTransport(status=500), "Jev decision failed"),
    ],
)
def test_jev_failures_leave_the_value_unavailable(
    tmp_path: Path, mode: PolicyMode, transport: RecordedTransport | None, reason: str
) -> None:
    ctx = _ctx(tmp_path / "trial", _ambiguous_trace())
    result = _assess(ctx, _runtime(tmp_path / "store", mode, transport=transport))
    item = _by_key(result)["jev_ambiguous_compliance", "checkpoint_1"]
    assert (item.status, item.value) == (AssessStatus.UNAVAILABLE, None)
    assert reason in (item.reason or "")
    assert result.provenance.policy_modes == {"jev_ambiguous_compliance": mode.value}


def test_a_store_bound_scorer_uses_the_versioned_policy_and_audits_in_the_store(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path / "trial", _ambiguous_trace())
    scorer = process_compliance_scorer_for_store(tmp_path / "store", replay_only=True)
    result = validate_result(scorer.manifest, ctx, scorer.assess(ctx))
    item = _by_key(result)["jev_ambiguous_compliance", "checkpoint_1"]
    # Replay-only never calls the provider: an uncached residue is a cache miss.
    assert (item.status, item.value) == (AssessStatus.UNAVAILABLE, None)
    assert "cache_miss" in (item.reason or "")
    assert result.provenance.policy_modes == {"jev_ambiguous_compliance": "shadow"}
    assert (tmp_path / "store" / "jev-audit").is_dir()


def test_ineligible_snapshots_and_missing_traces_are_statused(tmp_path: Path) -> None:
    good = trajectory(agent_step(1, codex_patch("e1", "app/main.py")))
    steps = [
        TracedStep("checkpoint_1", trace=good, files={"app/main.py": b"x = '\xff'\n"}),
        TracedStep("checkpoint_2", trace=None),
    ]
    observed = _by_key(_assess(traced_context(tmp_path, steps, planned=["checkpoint_3"])))
    for metric in ("tests_run_before_finish", "plan_written", "jev_ambiguous_compliance"):
        assert observed[metric, "checkpoint_1"].status is AssessStatus.INELIGIBLE
        assert observed[metric, "checkpoint_2"].status is AssessStatus.UNAVAILABLE
        assert observed[metric, "checkpoint_3"].status is NA
    assert observed["compliance_rate", "trajectory"].status is AssessStatus.UNAVAILABLE


def test_config_extends_patterns_and_rejects_bad_values(tmp_path: Path) -> None:
    trace = trajectory(
        agent_step(1, codex_patch("e1", "/work/app/main.py")),
        agent_step(2, codex_exec("t1", "./run-checks.sh")),
    )
    config = {"workdir": "/work", "extra_test_patterns": [r"\./run-checks\.sh"]}
    observed = _by_key(_assess(_ctx(tmp_path, trace, **config)))
    assert _state(observed["tests_run_before_finish", "checkpoint_1"]) == (OK, 1.0)

    bad = _assess(_ctx(tmp_path / "bad", trace, extra_test_patterns=["("]))
    assert bad.status is AssessStatus.ERROR
    retired = _assess(_ctx(tmp_path / "retired", trace, jev_policy="shadow"))
    assert retired.status is AssessStatus.ERROR


def test_process_compliance_is_bound_in_the_default_registry() -> None:
    registration = default_scorer_registry().get("process-compliance@1")
    assert registration.load().manifest == registration.manifest
