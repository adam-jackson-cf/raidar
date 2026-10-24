"""Classify an ATIF trajectory's tool calls into process events.

The classifier reads what a call did from its arguments, the same way for
every harness, but only from the parts of the arguments that act:

- the **command** strings of a call: a shell tool's ``command``/``cmd``, or
  each ``cmd`` of Codex's JavaScript ``exec`` wrapper
  (``tools.exec_command({cmd: ...})``);
- the **patch headers** of ``apply_patch`` bodies;
- the **path** of an edit tool (``Edit``, ``Write`` ...).

What a call writes is never read as what it runs: edit-tool content, patch
bodies, heredoc bodies fed to ``cat``/``tee`` and inline interpreter scripts
(``python -c "..."``) are masked before looking for test runs and shell
redirects, so a test file containing ``import pytest`` is an edit, not a test
run. Events keep their order inside one call (``pytest -q; cat > a.py`` is a
test run, then an edit). An event is **definite** when the command names what
happened and **ambiguous** when it shows something may have happened but not
what:

- ``edit``: a patch header, an edit tool's path, a shell redirect or ``tee``
  into a workspace file. Files outside the workspace (``/tmp``,
  ``/dev/null``), and scratch output a shell redirect writes (``*.log``,
  ``*.txt`` other than requirements files ...), are not source edits; plan
  files are plans.
- ``ambiguous_edit``: an in-place rewrite whose target is not parsed
  (``sed -i``, ``perl -i``) or an interpreter script that writes files.
- ``test``: a command segment that starts with a test runner (``pytest``,
  ``python -m unittest``, ``make test`` ...), after launcher prefixes such as
  ``uv run``, ``timeout 60`` or ``FOO=1`` are stripped. ``cat pytest.ini`` or
  ``pip show pytest`` do not start with a runner, so they are not runs.
- ``plan``: a plan tool (``update_plan``, ``TodoWrite``, ``write_todos``) or a
  write to a plan file (``PLAN.md``, ``todo.md``, ``plans/...``).
- ``ambiguous_plan``: an agent message that lays out a numbered or bulleted
  list of steps without a plan tool or file.

Patterns are the defaults below, extended per assessment through the scorer
config; test patterns match at the start of a stripped command segment.
Everything is a pure function of the trajectory, so replays are identical.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from raidar.scoring.resource_usage.atif import AGENT_SOURCE, AtifStep, Trajectory

DEFAULT_WORKDIR = "/app"
DEFAULT_TEST_PATTERNS = (
    r"py\.?test(?![\w.-])",
    r"python[0-9.]*\s+-m\s+(?:pytest|unittest)\b",
    r"(?:make|npm|pnpm|yarn|bun|cargo|go|deno)\s+(?:run\s+)?test\b",
    r"(?:tox|nox)\b",
)
DEFAULT_PLAN_FILE_PATTERNS = (
    r"(?i)(?:^|/)(?:plans?|todos?|implementation[-_]plan)[\w.-]*\.(?:md|txt)$",
    r"(?i)(?:^|/)plans/[^/]+$",
)
PLAN_TOOLS = frozenset({"update_plan", "todowrite", "todo_write", "write_todos"})
EDIT_TOOLS = frozenset(
    {
        "edit",
        "write",
        "multiedit",
        "notebookedit",
        "write_file",
        "replace",
        "str_replace_based_edit_tool",
        "create_file",
        "edit_file",
    }
)
COMMAND_KEYS = ("command", "cmd", "commands", "script")
_PATH_KEYS = ("file_path", "path", "notebook_path", "target_file")
_PATCH_HEADER = re.compile(r"\*\*\* (?:Add|Update|Delete) File: ([^\s\\\"']+)")
_PATCH_BLOCK = re.compile(r"\*\*\* Begin Patch.*?(?:\*\*\* End Patch|\Z)", re.DOTALL)
_JS_TOOL = re.compile(r"\btools\.\w+\s*\(")
_JS_CMD = re.compile(
    r"""\bcmd\s*:\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|`((?:[^`\\]|\\.)*)`)""",
    re.DOTALL,
)
_JS_ESCAPE = re.compile(r"\\(.)", re.DOTALL)
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n")
_INTERPRETER = r"(?<![\w./-])(?:python[0-9.]*|node|ruby|perl|(?P<shell>(?:ba|z)?sh))(?![\w.-])"
_FEEDS_INTERPRETER = re.compile(_INTERPRETER + r"[^\n|;&]*$")
_INLINE_SCRIPT = re.compile(
    r"(?<![\w./-])(?:python[0-9.]*|node|ruby|perl)\s+(?:-\w+\s+)*-[ce]\s+"
    r"""("(?:[^"\\]|\\.)*"|'[^']*')"""
)
_REDIRECT = re.compile(r"(?<![\d&>=<-])>>?\s*(?!&)([\w./~-]*\w[\w./~-]*)")
_TEE = re.compile(r"\btee\s+(?:-a\s+)?([\w./~-]*\w[\w./~-]*)")
_IN_PLACE = re.compile(r"\b(?:sed|perl)\s+(?:-\w+\s+)*-i")
_SCRIPT_WRITE = re.compile(r"\bopen\([^)]*['\"][wa]\+?['\"]|\.write_text\(|\.write_bytes\(")
_PLAN_CALL = re.compile(r"\bupdate_plan\s*\(")
_SCRATCH_OUTPUT = re.compile(r"(?i)\.(?:log|out|err|tmp|txt)$")
_KEPT_TEXT = re.compile(r"(?i)(?:^|/)(?:requirements|constraints)[^/]*\.txt$")
_SEGMENT = re.compile(r"\n|;|&&|\|\||\|")
LAUNCHER = re.compile(
    r"""(?:[\s('"{]+"""
    r"|\w+=\S*\s+"
    r"|(?:sudo|env|exec|time|command|nohup|npx)\s+"
    r"|timeout\s+(?:-\S+\s+)*\S+\s+"
    r"|(?:uv|poetry|pipenv|pdm|hatch|rye)\s+run\s+"
    r"(?:--(?:project|directory|with|python|package|group|extra)[= ]\S+\s+|--?[\w-]+(?:=\S+)?\s+)*"
    r"|(?:ba|z)?sh\s+-\w*c\s+)*"
)
_LIST_LINE = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+\S")


class EventKind(StrEnum):
    EDIT = "edit"
    AMBIGUOUS_EDIT = "ambiguous_edit"
    TEST = "test"
    PLAN = "plan"
    AMBIGUOUS_PLAN = "ambiguous_plan"


@dataclass(frozen=True)
class TraceEvent:
    """One classified action, at ``index`` in the step's execution order."""

    index: int
    kind: EventKind
    session: str
    tool: str
    call_id: str | None
    detail: str
    path: str | None = None


@dataclass(frozen=True)
class Classifier:
    """Assessor-owned patterns for one assessment."""

    workdir: str = DEFAULT_WORKDIR
    test_patterns: tuple[str, ...] = DEFAULT_TEST_PATTERNS
    plan_file_patterns: tuple[str, ...] = DEFAULT_PLAN_FILE_PATTERNS

    def workspace_path(self, raw: str) -> str | None:
        """``raw`` relative to the workspace, or None when it lies outside it."""
        root = PurePosixPath(self.workdir)
        path = PurePosixPath(raw)
        if path.is_absolute():
            return path.relative_to(root).as_posix() if path.is_relative_to(root) else None
        return None if raw.startswith("~") or ".." in path.parts else path.as_posix()

    def is_plan_file(self, relative: str) -> bool:
        return any(re.search(pattern, relative) for pattern in self.plan_file_patterns)

    def runs_tests(self, segment: str) -> str | None:
        """The segment, stripped of launcher prefixes, when it starts with a test runner."""
        launcher = LAUNCHER.match(segment)
        command = segment[launcher.end() :] if launcher else segment
        if any(re.match(pattern, command) for pattern in self.test_patterns):
            return segment.strip()
        return None


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value.replace("\\n", "\n")
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)


def _joined(value: Any) -> str:
    return "\n".join(_strings(value))


@dataclass(frozen=True)
class Call:
    """One tool call of one session."""

    session: str
    tool: str
    call_id: str | None
    arguments: Any

    @property
    def name(self) -> str:
        return self.tool.lower()


@dataclass(frozen=True)
class CallView:
    """The acting parts of a call: text holding patch headers, and its commands.

    Each command carries its offset in ``text`` so events keep call order.
    """

    text: str = ""
    commands: tuple[tuple[int, str], ...] = ()


def _js_commands(text: str) -> tuple[tuple[int, str], ...]:
    commands = []
    for match in _JS_CMD.finditer(text):
        raw = next(group for group in match.groups() if group is not None)
        commands.append((match.start(), _JS_ESCAPE.sub(r"\1", raw)))
    return tuple(commands)


def call_view(call: Call) -> CallView:
    """What a call runs and patches; edit and plan tools only name a path."""
    if call.name in EDIT_TOOLS or call.name in PLAN_TOOLS:
        return CallView()
    text = _joined(call.arguments)
    if _JS_TOOL.search(text):
        return CallView(text, _js_commands(text))
    if isinstance(call.arguments, Mapping):
        commands = [call.arguments[key] for key in COMMAND_KEYS if key in call.arguments]
        if commands:
            command = _joined(commands)
            return CallView(command, ((0, command),))
    return CallView(text)


def _blank(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        for position in range(start, end):
            if chars[position] != "\n":
                chars[position] = " "
    return "".join(chars)


@dataclass(frozen=True)
class _Views:
    """A command with written content masked, and the scripts it runs."""

    shell: str
    scripts: tuple[tuple[int, str], ...]


def _heredocs(command: str) -> Iterator[tuple[int, int, bool, bool]]:
    """(body start, body end, feeds an interpreter, feeds a shell) per heredoc."""
    position = 0
    while match := _HEREDOC.search(command, position):
        end_re = re.compile(rf"^\t*{re.escape(match.group(2))}[ \t]*$", re.MULTILINE)
        end = end_re.search(command, match.end())
        body_end = end.start() if end else len(command)
        line_start = command.rfind("\n", 0, match.start()) + 1
        feeder = _FEEDS_INTERPRETER.search(command[line_start : match.start()])
        yield match.end(), body_end, feeder is not None, bool(feeder and feeder.group("shell"))
        position = end.end() if end else len(command)


def _views(command: str) -> _Views:
    masked: list[tuple[int, int]] = []
    scripts: list[tuple[int, str]] = []
    for start, end, interpreted, shell in _heredocs(command):
        if shell:
            continue
        masked.append((start, end))
        if interpreted:
            scripts.append((start, command[start:end]))
    masked.extend(match.span() for match in _PATCH_BLOCK.finditer(command))
    for match in _INLINE_SCRIPT.finditer(command):
        masked.append(match.span(1))
        scripts.append((match.start(1), match.group(1)))
    return _Views(_blank(command, masked), tuple(scripts))


def _segments(text: str) -> Iterator[tuple[int, str]]:
    position = 0
    for separator in _SEGMENT.finditer(text):
        yield position, text[position : separator.start()]
        position = separator.end()
    yield position, text[position:]


_Found = tuple[int, EventKind, str, str | None]
_UNPARSED = "in-place or scripted write; target not parsed"


def _write(offset: int, raw: str, classifier: Classifier, *, shell: bool) -> list[_Found]:
    relative = classifier.workspace_path(raw)
    if relative is None:
        return []
    if classifier.is_plan_file(relative):
        return [(offset, EventKind.PLAN, f"plan file {relative}", relative)]
    if shell and _SCRATCH_OUTPUT.search(relative) and not _KEPT_TEXT.search(relative):
        return []
    return [(offset, EventKind.EDIT, relative, relative)]


def _command_events(offset: int, command: str, classifier: Classifier) -> list[_Found]:
    views = _views(command)
    found: list[_Found] = []
    for pattern in (_REDIRECT, _TEE):
        for match in pattern.finditer(views.shell):
            found += _write(offset + match.start(), match.group(1), classifier, shell=True)
    ambiguous = [match.start() for match in _IN_PLACE.finditer(views.shell)]
    for start, script in views.scripts:
        ambiguous += [start + match.start() for match in _SCRIPT_WRITE.finditer(script)]
    found += [(offset + start, EventKind.AMBIGUOUS_EDIT, _UNPARSED, None) for start in ambiguous]
    for start, segment in _segments(views.shell):
        run = classifier.runs_tests(segment)
        if run is not None:
            found.append((offset + start, EventKind.TEST, run[:200], None))
    return found


def _classify_call(call: Call, classifier: Classifier) -> list[tuple[EventKind, str, str | None]]:
    """(kind, detail, workspace path) for everything one call did, in order."""
    view = call_view(call)
    found: list[_Found] = []
    if call.name in PLAN_TOOLS:
        found.append((-1, EventKind.PLAN, f"plan tool {call.tool}", None))
    found += [
        (match.start(), EventKind.PLAN, f"plan tool {call.tool}", None)
        for match in _PLAN_CALL.finditer(view.text)
    ][:1]
    if call.name in EDIT_TOOLS and isinstance(call.arguments, Mapping):
        for key in _PATH_KEYS:
            if isinstance(call.arguments.get(key), str):
                found += _write(0, call.arguments[key], classifier, shell=False)
    for match in _PATCH_HEADER.finditer(view.text):
        found += _write(match.start(), match.group(1), classifier, shell=False)
    for offset, command in view.commands:
        found += _command_events(offset, command, classifier)
    found.sort(key=lambda item: item[0])
    return [(kind, detail, path) for _, kind, detail, path in found]


def _message_plan(step: AtifStep) -> bool:
    return step.source == AGENT_SOURCE and (
        sum(1 for line in step.message.splitlines() if _LIST_LINE.match(line)) >= 2
    )


def step_calls(step: AtifStep) -> Iterator[Call]:
    for call in step.tool_calls:
        call_id = call.get("tool_call_id")
        yield Call(
            session=step.session,
            tool=str(call.get("function_name") or ""),
            call_id=call_id if isinstance(call_id, str) else None,
            arguments=call.get("arguments"),
        )


def classify(trajectory: Trajectory, classifier: Classifier) -> tuple[TraceEvent, ...]:
    """Every process event of a step's trajectory, in execution order."""
    events: list[TraceEvent] = []
    index = 0
    for step in trajectory.steps:
        if _message_plan(step):
            events.append(
                TraceEvent(index, EventKind.AMBIGUOUS_PLAN, step.session, "", None, "listed steps")
            )
            index += 1
        for call in step_calls(step):
            for kind, detail, path in _classify_call(call, classifier):
                events.append(
                    TraceEvent(index, kind, call.session, call.tool, call.call_id, detail, path)
                )
            index += 1
    return tuple(events)
