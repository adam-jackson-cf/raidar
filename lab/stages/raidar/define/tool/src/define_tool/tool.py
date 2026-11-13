"""``define``: the command the intake builds its feature definition with.

The builder runs it in its shell (``define`` on its ``PATH``, in the trial's container); the
stage owns what it reads and writes. Each ``define put``
takes a block of lines (``block``) on standard input or from a file, applies
every line it can read to the intake's draft (``draft``) and prints what is
wrong, so a fix is one small ``put`` of the item, never a resend of the whole::

    define put <<'EOF'
    title: ...
    EOF
    define put FILE     the block in a file instead of standard input
    define show         the draft, in the same grammar
    define check        what is wrong with it now
    define drop <ref>   remove F2, F2.AC1, ``decision 3``, ``named <name>``,
                        ``question F2 <kind>``, ... (one that names nothing
                        lists the refs the draft holds)
    define done ready   seal it: the definition is complete
    define done ask     seal it: questions only, the intake asks

The checks are the engine's own (``intakecheck.check_reply``, over the same
numbered units of the issue), worded in the block's own lines. ``done`` refuses
while a blocking finding stands; it is a signal only, because the engine reads
the draft itself when the stage ends and checks it again.

Calls may overlap (a shell can run several at once): each holds the state's lock
(``LOCK_FILE``) while it reads, changes and writes the draft. A state that cannot
be read or written is the tool failing, not a finding: it exits ``BROKEN`` and
records an ``ERROR_CALL``, which the engine's health checks read as an instrument
fault, never as the model's.

The state is a directory outside the workspace, ``/app/.raidar/define`` unless
``RAIDAR_DEFINE_STATE`` names another. The stage makes it empty at the start of each
checkpoint and puts the ``Context`` (the issue, the earlier answers, the open question
ids, the workspace) in it; the tool keeps the draft (``draft.json``), a record of every
call (``define-calls.jsonl``: its command and the findings it printed, in order) and
beside it the trace of the same calls (``define-events.jsonl``: when, the arguments and
input, and the digests of the context and of the state each call left).
``define done ready`` that seals writes the rendered definition (``definition_path``).
"""

from __future__ import annotations

import contextlib
import enum
import fcntl
import hashlib
import json
import os
import shutil
import sys
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import block, compact, evidence
from .aspects import Finding
from .core import PROBES_OWED_KEY, UNIT_RULES_KEY, JsonObject, strings, text
from .draft import SEALS, Draft, DraftError
from .featuredef import FILE
from .intakecheck import (
    ReplyChecked,
    check_reply,
    with_probes,
)
from .owed import Owed
from .unitrules import VERSIONS

STATE_ENV = "RAIDAR_DEFINE_STATE"
# Where a sealed ``done ready`` writes the rendered definition, instead of under the context's
# workdir.
OUTPUT_ENV = "RAIDAR_DEFINE_OUTPUT"
DEFAULT_STATE = "/app/.raidar/define"
CONTEXT_FILE = "context.json"
STATE_FILE = "draft.json"
EVENTS_FILE = "define-events.jsonl"
ROUNDS_DIR = "rounds"
CALLS_FILE = "define-calls.jsonl"
# Held while a command reads, changes and writes the state, so calls that run
# at once (a shell can run several) apply one after the other.
LOCK_FILE = "state.lock"
# The command a call records when the tool itself could not work (its state
# could not be read or written), as against a model's mistake (``usage``).
ERROR_CALL = "error"
USAGE = (
    "usage: define put [FILE] (the block on standard input) | show | check | "
    "drop <ref> | done ready|ask"
)
# The exit code of a tool that could not run, as the gate's is.
BROKEN = 126
FAULT = 1
USAGE_ERROR = 2


@dataclass(frozen=True)
class Context:
    """What the checks of one intake call read: the engine's, never the intake's."""

    issue: str
    said: tuple[str, ...]
    prior: str
    open: tuple[str, ...]
    workdir: str
    # The workflow's unit rules version (``unitrules``): how the issue is cut
    # into units and whether an uncovered required unit blocks ``done``.
    unit_rules: int
    # What the issue obliges the definition to give examples for, when the
    # workflow derives acceptance probes from them (``owed``); ``None`` otherwise.
    owed: Owed | None = None

    def record(self) -> JsonObject:
        return {
            "issue": self.issue,
            "said": list(self.said),
            "prior": self.prior,
            "open": list(self.open),
            "workdir": self.workdir,
            UNIT_RULES_KEY: self.unit_rules,
            PROBES_OWED_KEY: None if self.owed is None else self.owed.record(),
        }

    @classmethod
    def read(cls, record: JsonObject) -> Context:
        return cls(
            text(record, "issue"),
            tuple(strings(record, "said")),
            text(record, "prior"),
            tuple(strings(record, "open")),
            text(record, "workdir"),
            _rules_version(record),
            _owed(record),
        )


def _owed(record: JsonObject) -> Owed | None:
    """Return the probe obligations a context carries, ``None`` when it carries none."""
    found = record.get(PROBES_OWED_KEY)
    return Owed.read(found) if isinstance(found, dict) else None


def _rules_version(record: JsonObject) -> int:
    """Return the unit rules version a context names; raise ``ValueError`` for one that is none."""
    found = record.get(UNIT_RULES_KEY)
    if isinstance(found, bool) or not isinstance(found, int) or found not in VERSIONS:
        raise ValueError(f"unit_rules {found!r} names no unit rule set")
    return found


@dataclass(frozen=True)
class Call:
    """One ``define`` call as recorded: its command and the findings it printed, in order."""

    command: str
    texts: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    advisory: tuple[bool, ...] = ()

    @property
    def blocking(self) -> tuple[str, ...]:
        return tuple(t for t, soft in zip(self.texts, self.advisory, strict=True) if not soft)

    def record(self) -> JsonObject:
        return {
            "command": self.command,
            "texts": list(self.texts),
            "kinds": list(self.kinds),
            "advisory": list(self.advisory),
        }


@dataclass(frozen=True)
class Outcome:
    """What a command printed and exited with, and the call it records."""

    code: int
    output: str
    call: Call


def start(directory: Path, context: Context) -> None:
    """Make ``directory`` the empty state of one intake call, holding ``context``."""
    if directory.is_symlink() or directory.is_file():
        directory.unlink()
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    (directory / CONTEXT_FILE).write_text(json.dumps(context.record()), encoding="utf-8")


def read_calls(directory: Path) -> list[Call]:
    """Return the calls recorded in ``directory``, in order; none when it has none."""
    path = directory / CALLS_FILE
    if not path.is_file():
        return []
    return parse_calls(path.read_text(encoding="utf-8", errors="replace"))


def parse_calls(recorded: str) -> list[Call]:
    """Return the calls a call log holds (``CALLS_FILE``), in order; a line that is no call is
    skipped."""
    calls = []
    for line in recorded.splitlines():
        try:
            found = json.loads(line)
        except ValueError:
            continue
        if isinstance(found, dict):
            calls.append(
                Call(
                    text(found, "command"),
                    tuple(strings(found, "texts")),
                    tuple(strings(found, "kinds")),
                    tuple(item is True for item in _list(found, "advisory")),
                )
            )
    return calls


def _list(record: JsonObject, key: str) -> list[object]:
    found = record.get(key)
    return found if isinstance(found, list) else []


def read_draft(directory: Path) -> Draft | None:
    """Return the draft ``directory`` holds, ``None`` when none was written; raise
    ``DraftError``."""
    path = directory / STATE_FILE
    if not path.is_file():
        return None
    try:
        return Draft.read(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        raise DraftError(f"the state cannot be read ({error})") from error


def _write_draft(directory: Path, draft: Draft) -> None:
    scratch = directory / f"{STATE_FILE}.new"
    scratch.write_text(json.dumps(draft.record()), encoding="utf-8")
    os.replace(scratch, directory / STATE_FILE)


def _record_call(directory: Path, call: Call) -> None:
    with (directory / CALLS_FILE).open("a", encoding="utf-8") as log:
        log.write(json.dumps(call.record()) + "\n")


class _Workdir(enum.Enum):
    """The default of ``check``'s ``workdir``: the one the context names."""

    CONTEXT = enum.auto()


def _sha256(path: Path) -> str | None:
    """Return the digest of the file at ``path``, ``None`` when it is not there."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _record_event(
    directory: Path,
    argv: Sequence[str],
    said: str | None,
    call: Call,
    sealed: str | None,
) -> None:
    """Append the evidence of one call to ``EVENTS_FILE``, line for line with ``CALLS_FILE``.

    The call log stays as the engine wrote it; this is the trace beside it: when the
    call ran, what it was given, which context it ran under and the state it left.
    """
    event = {
        "at": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "argv": list(argv),
        "input": said,
        "command": call.command,
        "context_sha256": _sha256(directory / CONTEXT_FILE),
        "state_sha256": _sha256(directory / STATE_FILE),
        "sealed": sealed,
    }
    with (directory / EVENTS_FILE).open("a", encoding="utf-8") as log:
        log.write(json.dumps(event) + "\n")


def definition_path(context: Context, output: Path | None = None) -> Path | None:
    """Return where a sealed definition is written: ``output``, else under the context's workdir."""
    if output is not None:
        return output
    return Path(context.workdir) / FILE if context.workdir else None


def write_definition(path: Path | None, rendered: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(f"{path.name}.new")
    scratch.write_text(rendered, encoding="utf-8")
    os.replace(scratch, path)


def check(
    draft: Draft,
    context: Context,
    status: str,
    workdir: Path | None | _Workdir = _Workdir.CONTEXT,
) -> ReplyChecked:
    """Check ``draft`` as a reply with ``status``: the engine's own check, on the context's inputs.

    ``workdir`` is where a `path:line` evidence is read; ``None`` skips those files (a check
    run away from the trial's workspace).
    """
    units = compact.units(context.issue, list(context.said), VERSIONS[context.unit_rules])
    source = evidence.source_text(context.issue, context.prior)
    reply = draft.reply(status)
    where = Path(context.workdir) if workdir is _Workdir.CONTEXT else workdir
    checked = check_reply(reply, units, source, where, context.open)
    return with_probes(checked, reply, context.owed)


def recheck(draft: Draft, context: Context, status: str) -> ReplyChecked:
    """Check ``draft`` again without the trial's workspace (``check`` with no ``workdir``)."""
    return check(draft, context, status, None)


def definition_text(draft: Draft, context: Context) -> str:
    """Return the exact definition file text a ``ready`` draft renders to."""
    units = compact.units(context.issue, list(context.said), VERSIONS[context.unit_rules])
    definition = draft.reply(SEALS["ready"]).get("definition")
    return compact.render(definition if isinstance(definition, dict) else {}, units)


def next_round(directory: Path, context: Context) -> int:
    """Start the next question round in ``directory``; return the round just archived.

    The draft and the context of the round that ended are kept under
    ``rounds/<n>/``; the call and event logs run on; the draft is empty and
    ``context`` is the one the new round checks against.
    """
    archive = directory / ROUNDS_DIR
    n = len(list(archive.iterdir())) + 1 if archive.is_dir() else 1
    kept = archive / str(n)
    kept.mkdir(parents=True)
    for name in (CONTEXT_FILE, STATE_FILE):
        if (directory / name).is_file():
            shutil.move(directory / name, kept / name)
    (directory / CONTEXT_FILE).write_text(json.dumps(context.record()), encoding="utf-8")
    return n


def _findings_call(command: str, found: Sequence[Finding]) -> Call:
    return Call(
        command,
        tuple(item.text for item in found),
        tuple(item.kind for item in found),
        tuple(item.advisory for item in found),
    )


def _listing(found: Sequence[Finding]) -> str:
    return "".join(f"- {'(advisory) ' if item.advisory else ''}{item.text}\n" for item in found)


def _result(command: str, headline: str, found: Sequence[Finding]) -> Outcome:
    blocking = [item for item in found if not item.advisory]
    tail = (
        f"{len(blocking)} to fix\n"
        if blocking
        else "nothing to fix; run `define done ready` (or `ask`) when the draft is finished\n"
    )
    return Outcome(
        FAULT if blocking else 0,
        f"{headline}\n{_listing(found)}{tail}",
        _findings_call(command, found),
    )


# How a kind of item is named when counted, singular and plural.
NAMES = {
    block.TITLE: ("title", "titles"),
    block.OBJECTIVE: ("objective", "objectives"),
    block.NAMED: ("named feature", "named features"),
    block.FEATURE: ("feature", "features"),
    block.AC: ("criterion", "criteria"),
    block.DECISION: ("decision", "decisions"),
    block.RETIRE: ("retirement", "retirements"),
    block.QUESTION: ("question", "questions"),
    block.REPLY: ("reply", "replies"),
}


def _counts(items: Sequence[block.Item]) -> str:
    """Return how many items of each kind there are, as ``2 features, 5 criteria``."""
    kinds = Counter(item.kind for item in items)
    return ", ".join(
        f"{kinds[kind]} {names[kinds[kind] != 1]}" for kind, names in NAMES.items() if kinds[kind]
    )


def _put(draft: Draft, context: Context, source: str) -> Outcome:
    parsed = block.parse(source)
    done = draft.put(parsed.items)
    found = [
        *parsed.findings,
        *done.refused,
        *check(draft, context, draft.presumed()).findings,
    ]
    held = len(done.applied)
    headline = (
        f"put: applied {held} {'item' if held == 1 else 'items'} ({_counts(done.applied)})"
        if held
        else "put: applied nothing"
    )
    return _result("put", headline, found)


def _drop(draft: Draft, context: Context, reference: str) -> Outcome:
    if not draft.drop(reference):
        held = ", ".join(f"`{ref}`" for ref in draft.refs()) or "nothing yet"
        message = f"drop: nothing is named `{reference}`; the draft holds {held}"
        return Outcome(FAULT, message + "\n", Call("drop"))
    found = check(draft, context, draft.presumed()).findings
    return _result("drop", f"drop: removed `{reference}`", found)


def _done(draft: Draft, context: Context, word: str, output: Path | None) -> Outcome:
    checked = check(draft, context, SEALS[word])
    found = checked.findings
    blocking = [item for item in found if not item.advisory]
    if blocking:
        return _result("done", f"done {word}: not sealed", found)
    if word == "ready" and checked.file is not None:
        write_definition(definition_path(context, output), checked.file.rendered)
    draft.sealed = word
    return Outcome(0, f"done {word}: sealed\n{_listing(found)}", _findings_call("done", found))


def _show(draft: Draft) -> Outcome:
    return Outcome(0, block.show(draft.ordered()), Call("show"))


def _usage(message: str = "") -> Outcome:
    return Outcome(USAGE_ERROR, f"{message}{USAGE}\n", Call("usage"))


def _command(
    draft: Draft,
    context: Context,
    args: Sequence[str],
    stdin: Callable[[], str],
    output: Path | None = None,
) -> Outcome:
    name, rest = (args[0], list(args[1:])) if args else ("", [])
    if name == "put" and len(rest) <= 1:
        return _put(draft, context, _source(rest, stdin))
    if name == "drop" and rest:
        return _drop(draft, context, " ".join(rest))
    if name == "done" and len(rest) == 1 and rest[0] in SEALS:
        return _done(draft, context, rest[0], output)
    return _look(draft, context, name, rest)


def _look(draft: Draft, context: Context, name: str, rest: Sequence[str]) -> Outcome:
    """Run ``show`` or ``check`` (neither changes the draft), else print the usage."""
    if name == "show" and not rest:
        return _show(draft)
    if name == "check" and not rest:
        return _result("check", "check:", check(draft, context, draft.presumed()).findings)
    return _usage()


def _source(rest: Sequence[str], stdin: Callable[[], str]) -> str:
    if rest:
        return Path(rest[0]).read_text(encoding="utf-8", errors="replace")
    return stdin()


@contextlib.contextmanager
def _exclusive(directory: Path) -> Iterator[None]:
    """Hold the state's lock: one command at a time reads, changes and writes it."""
    with (directory / LOCK_FILE).open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _broken(directory: Path, why: str, argv: Sequence[str], said: str | None) -> Outcome:
    """Return the outcome of a tool that could not work, and mark it in the call log when it can."""
    outcome = Outcome(BROKEN, f"define: ERROR, {why}\n", Call(ERROR_CALL))
    with contextlib.suppress(OSError):
        _record_call(directory, outcome.call)
        _record_event(directory, argv, said, outcome.call, None)
    return outcome


def _locked_run(
    directory: Path, args: Sequence[str], said: str | None, output: Path | None
) -> Outcome:
    try:
        context = Context.read(json.loads((directory / CONTEXT_FILE).read_text("utf-8")))
        draft = read_draft(directory) or Draft()
    except (OSError, ValueError, DraftError) as error:
        return _broken(directory, f"the state cannot be used ({error})", args, said)
    try:
        outcome = _command(draft, context, args, lambda: said or "", output)
    except OSError as error:
        outcome = Outcome(USAGE_ERROR, f"define: {error}\n", Call("usage"))
    else:
        _write_draft(directory, draft)
    _record_call(directory, outcome.call)
    _record_event(directory, args, said, outcome.call, draft.sealed)
    return outcome


def run(
    directory: Path,
    args: Sequence[str],
    stdin: Callable[[], str],
    output: Path | None = None,
) -> Outcome:
    """Run one ``define`` command against the state in ``directory``.

    Standard input is read before the state is locked, so a command waiting on
    it holds nothing up. A state that cannot be read or written is the tool
    failing (``BROKEN``, recorded as an ``ERROR_CALL``), never a finding.
    ``output`` is where a sealed ``done ready`` writes the rendered definition;
    ``None`` is ``definition_path`` of the context.
    """
    said = stdin() if list(args) == ["put"] else None
    try:
        with _exclusive(directory):
            return _locked_run(directory, args, said, output)
    except OSError as error:
        return _broken(directory, f"the state cannot be used ({error})", args, said)


def _read_stdin() -> str:
    """Return standard input as text, whatever bytes it holds."""
    return sys.stdin.buffer.read().decode("utf-8", errors="replace")


def _write_stdout(text: str) -> None:
    """Print ``text`` as UTF-8 whatever the locale is (a finding may hold ``…``)."""
    sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def main(
    args: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    stdin: Callable[[], str] | None = None,
    out: Callable[[str], object] | None = None,
) -> int:
    """Run ``define`` as the shell command: print its output, return its exit code."""
    given = sys.argv[1:] if args is None else args
    env = os.environ if environ is None else environ
    printer = _write_stdout if out is None else out
    where = env.get(STATE_ENV) or DEFAULT_STATE
    override = env.get(OUTPUT_ENV)
    outcome = run(Path(where), given, stdin or _read_stdin, Path(override) if override else None)
    printer(outcome.output)
    return outcome.code
