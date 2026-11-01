"""trace_events: what one tool call did, from the parts of its arguments that act.

Edits, tests and plans are read from commands, patch headers and edit-tool paths;
written content is masked, files outside the workspace are not edits, and a command
the static shell reader cannot parse never counts as a test run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from raidar.scoring.framework.atif import AtifStep, Trajectory
from raidar.scoring.process_checks.trace_events import Call, Classifier, call_view, classify

UNPARSED = "in-place or scripted write; target not parsed"


def _events(*calls: tuple[str, Any], message: str = "") -> list[tuple[str, str, str | None]]:
    step = AtifStep(
        "main",
        1,
        "agent",
        message,
        tuple(
            {"tool_call_id": f"c{i}", "function_name": name, "arguments": arguments}
            for i, (name, arguments) in enumerate(calls)
        ),
    )
    trajectory = Trajectory(Path("trajectory.json"), (step,))
    return [(e.kind.value, e.detail, e.path) for e in classify(trajectory, Classifier())]


def _bash(command: str) -> list[tuple[str, str, str | None]]:
    return _events(("bash", {"command": command}))


def test_a_test_file_written_by_heredoc_is_an_edit_not_a_test_run() -> None:
    command = "cat > a.py <<'EOF'\nimport pytest\nEOF\npytest -q"

    assert _bash(command) == [("edit", "a.py", "a.py"), ("test", "pytest -q", None)]


def test_a_heredoc_fed_to_an_interpreter_that_writes_files_is_an_ambiguous_edit() -> None:
    command = "python - <<EOF\nopen('x.py','w').write('a')\nEOF"
    assert _bash(command) == [("ambiguous_edit", UNPARSED, None)]


def test_an_unterminated_heredoc_masks_to_the_end_of_the_command() -> None:
    command = "python <<-X\nPath('a').write_text('1')\n"
    assert _bash(command) == [("ambiguous_edit", UNPARSED, None)]


def test_a_heredoc_fed_to_an_interpreter_that_writes_nothing_is_no_event() -> None:
    assert _bash("python <<EOF\nprint(1)\nEOF") == []


def test_a_heredoc_fed_to_a_shell_is_left_visible_but_its_commands_are_not_run_from_text() -> None:
    assert _bash("bash <<EOF\npytest\nEOF") == []


def test_an_inline_interpreter_script_that_writes_is_an_ambiguous_edit() -> None:
    assert _bash("python -c \"open('a.py','w')\"") == [("ambiguous_edit", UNPARSED, None)]


def test_a_redirect_outside_the_workspace_is_not_an_edit() -> None:
    assert _bash("echo hi > /tmp/x") == []


def test_a_redirect_in_a_parent_directory_is_not_an_edit() -> None:
    assert _bash("echo 1 > ../x") == []


def test_shell_scratch_output_is_not_an_edit_but_requirements_and_plan_files_are() -> None:
    command = "echo hi > PLAN.md; echo > run.log; echo > requirements.txt"

    assert _bash(command) == [
        ("plan", "plan file PLAN.md", "PLAN.md"),
        ("edit", "requirements.txt", "requirements.txt"),
    ]


def test_edit_tools_name_their_path_and_a_plan_file_is_a_plan() -> None:
    events = _events(
        ("Write", {"file_path": "/app/src/a.py", "content": "import pytest"}),
        ("Edit", {"path": "notes/todo.md"}),
        ("Edit", {"path": "/etc/x"}),
        ("Edit", {"path": 3}),
    )

    assert events == [
        ("edit", "src/a.py", "src/a.py"),
        ("plan", "plan file notes/todo.md", "notes/todo.md"),
    ]


def test_a_plan_tool_is_a_plan_and_its_arguments_are_not_read() -> None:
    events = _events(("update_plan", {"command": "pytest"}), ("TodoWrite", None))

    assert events == [
        ("plan", "plan tool update_plan", None),
        ("plan", "plan tool TodoWrite", None),
    ]


def test_a_javascript_exec_wrapper_reports_its_plan_call_before_its_command() -> None:
    script = 'tools.update_plan({a:1}); tools.exec_command({cmd: "pytest -q"})'

    assert _events(("exec", {"p": script})) == [
        ("plan", "plan tool exec", None),
        ("test", "pytest -q", None),
    ]


def test_an_apply_patch_header_is_an_edit_and_its_body_is_not_a_test_run() -> None:
    patch = "*** Begin Patch\n*** Add File: a.py\n+pytest\n*** End Patch"

    assert _events(("apply_patch", {"input": patch})) == [("edit", "a.py", "a.py")]


def test_an_in_place_sed_is_an_ambiguous_edit() -> None:
    assert _bash("sed -i s/a/b/ f") == [("ambiguous_edit", UNPARSED, None)]


@pytest.mark.parametrize("arguments", [{"cwd": 5}, ["pytest -q"], "pytest", {"command": ["a", 3]}])
def test_arguments_without_a_command_run_nothing_or_only_what_text_they_hold(
    arguments: Any,
) -> None:
    assert _events(("bash", arguments)) == []


def test_non_string_argument_values_are_ignored_when_reading_commands() -> None:
    view = call_view(Call("main", "bash", None, {"command": ["pytest -q", 3, {"k": ("a",)}]}))

    assert view.commands == ((0, "pytest -q\na"),)


def test_a_message_listing_steps_is_an_ambiguous_plan() -> None:
    assert _events(message="1. a\n2. b") == [("ambiguous_plan", "listed steps", None)]


def test_a_command_the_shell_reader_cannot_split_is_not_a_test_run() -> None:
    assert _bash("pytest $'a\\'b'") == []


def test_an_escaped_shell_operand_is_a_conditional_test_run() -> None:
    assert _bash('bash -c "pytest \\"x\\""') == [("ambiguous_test", "pytest x", None)]


def test_workspace_paths_reject_the_workdir_itself_and_escapes() -> None:
    classifier = Classifier()

    assert classifier.workspace_path("/app") == "."
    assert classifier.workspace_path("a/../b") is None
    assert classifier.workspace_path("~/a") is None
    assert classifier.workspace_path("/etc/x") is None
