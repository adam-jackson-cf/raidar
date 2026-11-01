"""Call-transport decisions that start no process: a scripted container drives the real runner.

The container is an in-memory stand-in (``exec`` routed by command, uploads kept as bytes), so
success, non-zero, signalled, timed-out, overflowing and malformed supervisor outcomes, the
trusted-payload install, payload result parsing and the host-side snapshot verification are all
decided here on the real ``calls`` code. ``test_primitive_calls.py`` keeps the seams that need
a real payload process.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import shlex
import struct
import tarfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from raidar.subject.primitive import calls, install
from raidar.subject.primitive.payload import isolate, supervise

CONTEXT = {"task": "write a note", "round": 0}
CALL_ARGV = ["tool", "go"]


def _exec_result(stdout: str = "", return_code: int = 0, stderr: str = "") -> Any:
    return SimpleNamespace(stdout=stdout, stderr=stderr, return_code=return_code)


class Container:
    """An in-memory container: commands answered by shape, uploads kept, downloads served."""

    def __init__(
        self,
        *,
        supervisor: Callable[[dict[str, Any]], Any] | None = None,
        payloads: Mapping[str, Callable[[dict[str, Any]], Any]] | None = None,
        digest: str | None = None,
        after_upload: str | None = None,
    ) -> None:
        self.supervisor = supervisor or (lambda spec: _evidence())
        self.payloads = dict(payloads or {})
        self.digest = calls.payload_digest() if digest is None else digest
        self.after_upload = calls.payload_digest() if after_upload is None else after_upload
        self.commands: list[str] = []
        self.files: dict[str, bytes] = {}
        self.specs: list[dict[str, Any]] = []
        self.downloads = 0
        self.fail: dict[str, BaseException] = {}
        self.fail_transfer: Exception | None = None
        self.codes: dict[str, int] = {}
        self.answers = {"uname -m": "x86_64\n", "command -v uv": "/usr/local/bin/uv\n"}

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> Any:
        self.commands.append(command)
        for needle, error in self.fail.items():
            if needle in command:
                raise error
        for needle, code in self.codes.items():
            if needle in command:
                return _exec_result("", code)
        for prefix, out in self.answers.items():
            if command.startswith(prefix):
                return _exec_result(out)
        if "-B -c" in command:
            return _exec_result(self.digest + "\n")
        if "/run.py " in command:
            return self._launcher(shlex.split(command))
        if command.startswith("wc -c <"):
            data = self.files.get(shlex.split(command)[-1])
            return _exec_result("" if data is None else f"{len(data)}\n", int(data is None))
        return _exec_result()

    def _launcher(self, words: list[str]) -> Any:
        name, spec = words[-2], json.loads(self.files[words[-1]])
        if name == "supervise":
            self.specs.append(spec)
            reply = self.supervisor(spec)
        else:
            reply = self.payloads[name](spec)
        if isinstance(reply, dict):
            reply = json.dumps(reply)
        return _exec_result(reply) if isinstance(reply, str) else reply

    async def upload_file(self, source: Path | str, target: str) -> None:
        if self.fail_transfer:
            raise self.fail_transfer
        self.files[target] = Path(source).read_bytes()

    async def upload_dir(self, source: Path | str, target: str) -> None:
        if self.fail_transfer:
            raise self.fail_transfer
        for path in sorted(Path(source).rglob("*")):
            if path.is_file():
                self.files[f"{target}/{path.relative_to(source).as_posix()}"] = path.read_bytes()
        self.digest = self.after_upload

    async def download_file(self, source: str, target: Path | str) -> None:
        if self.fail_transfer:
            raise self.fail_transfer
        self.downloads += 1
        Path(target).write_bytes(self.files[source])

    def cleaned(self, fragment: str) -> bool:
        return any(c.startswith("rm -rf") and fragment in c for c in self.commands)


def _evidence(
    stdout: bytes = b"",
    stderr: bytes = b"",
    *,
    stdout_cap: int = supervise.STDOUT_CAP,
    stderr_cap: int = supervise.STDERR_TAIL,
    **over: Any,
) -> dict[str, Any]:
    """A supervisor result built with the payload's own capture, so digests are real."""
    out = supervise.Capture(stdout_cap, tail=False)
    err = supervise.Capture(stderr_cap, tail=True)
    out.feed(stdout)
    err.feed(stderr)
    document: dict[str, Any] = {
        "schema": "call-result/1",
        "ran": True,
        "isolation_error": None,
        "tool_before": [],
        "tool_after": [],
        "bounds_violation": None,
        "exit_code": 0,
        "signal": None,
        "timed_out": False,
        "overflow": out.overflow,
        "duration_s": 0.01,
        "descendants_killed": 0,
        **out.record("stdout"),
        **err.record("stderr"),
    }
    return {**document, **over}


async def _run(
    container: Container, tmp_path: Path, runner: calls.CallRunner | None = None, **options: Any
) -> calls.CallResult:
    runner = runner or calls.CallRunner(container, tmp_path / "logs")
    return await runner.run("ns/pkg", "setup/1", CALL_ARGV, CONTEXT, **options)


@pytest.fixture
def container() -> Container:
    return Container()


# --------------------------------------------------------------------------- the call
async def test_a_call_runs_confined_in_a_clean_environment_and_is_recorded(
    container: Container, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIDAR_TEST_SECRET", "s3cret")
    container.supervisor = lambda spec: _evidence(b"hello", b"warn")
    deadlines: list[float] = []
    execute = container.exec

    async def timed_exec(command: str, **options: Any) -> Any:
        if "supervise" in command:
            deadlines.append(options["timeout_sec"])
        return await execute(command, **options)

    monkeypatch.setattr(container, "exec", timed_exec)
    runner = calls.CallRunner(container, tmp_path / "logs")

    first = await _run(container, tmp_path, runner, timeout_s=5)
    second = await _run(container, tmp_path, runner)

    spec = container.specs[0]
    call_dir = spec["call_dir"]
    assert (first.usable, first.exit_code, first.stdout, first.stderr) == (
        True,
        0,
        b"hello",
        b"warn",
    )
    assert spec["argv"] == [*CALL_ARGV, "--context", f"{call_dir}/context.json"]
    assert set(spec["env"]) == {"PATH", "LANG", "HOME", "TMPDIR", "RAIDAR_STAGE_CONTEXT"}
    assert "s3cret" not in json.dumps(spec)
    assert spec["env"]["RAIDAR_STAGE_CONTEXT"] == f"{call_dir}/context.json"
    assert spec["env"]["HOME"] != container.specs[1]["env"]["HOME"]
    assert json.loads(container.files[f"{call_dir}/context.json"]) == CONTEXT
    assert (spec["uid"], spec["gid"], spec["seccomp"]) == (1000, 1000, True)
    assert deadlines[0] == 5 + calls.SUPERVISOR_SLACK_S
    record = json.loads(first.record_path.read_text())
    assert first.record_path.name == "0001-ns--pkg-setup--1.json"
    assert second.record_path.name == "0002-ns--pkg-setup--1.json"
    assert (record["package"], record["primitive"], record["user"]) == (
        "ns/pkg",
        "setup/1",
        "1000:1000",
    )
    assert record["env_names"] == sorted(spec["env"])
    assert record["stdout"] == "hello"
    assert record["integrity_error"] is None
    assert not container.cleaned(call_dir)  # the supervisor removes a finished call's directory


async def test_a_gate_call_keeps_its_argv_and_gets_the_context_only_through_the_environment(
    container: Container, tmp_path: Path
) -> None:
    await _run(container, tmp_path, append_context=False, cwd="/work")

    spec = container.specs[0]
    assert spec["argv"] == CALL_ARGV
    assert spec["cwd"] == "/work"
    assert spec["env"]["RAIDAR_STAGE_CONTEXT"].endswith("/context.json")


async def test_installed_trees_lead_the_path_and_are_verified_by_the_supervisor(
    container: Container, tmp_path: Path
) -> None:
    tree = SimpleNamespace(reference="ns/pkg", root="/opt/t", digest="d" * 64, bin="/opt/t/bin")

    result = await _run(container, tmp_path, installed=[tree], state="/watched")
    custom = await _run(container, tmp_path, path="/only/this")

    spec = container.specs[0]
    assert spec["env"]["PATH"] == f"/opt/t/bin:{supervise.BASE_PATH}"
    assert [(t["root"], t["digest"]) for t in spec["verify"]] == [("/opt/t", "d" * 64)]
    assert spec["verify"][0]["limits"]["owner"] == calls.ChildPolicy().owner
    assert spec["state"] == "/watched"
    assert result.record["installed"] == [{"reference": "ns/pkg", "digest": "d" * 64}]
    assert container.specs[1]["env"]["PATH"] == "/only/this"
    assert custom.usable


@pytest.mark.parametrize(
    ("over", "exit_code", "signal"),
    [
        ({"exit_code": 3}, 3, None),
        ({"exit_code": None, "signal": 9}, None, 9),
        ({"exit_code": None, "signal": 9, "timed_out": True}, None, 9),
        ({"overflow": True}, 0, None),
    ],
)
async def test_child_outcomes_that_are_not_a_clean_exit_are_unusable_but_not_integrity_failures(
    container: Container,
    tmp_path: Path,
    over: dict[str, Any],
    exit_code: int | None,
    signal: int | None,
) -> None:
    container.supervisor = lambda spec: _evidence(**over)

    result = await _run(container, tmp_path)

    assert (result.exit_code, result.signal) == (exit_code, signal)
    assert (result.timed_out, result.overflow) == (
        over.get("timed_out", False),
        over.get("overflow", False),
    )
    assert result.ran
    assert not result.usable
    assert result.integrity_error is None


async def test_a_bounded_head_and_tail_with_full_digests_are_still_trusted_evidence(
    container: Container, tmp_path: Path
) -> None:
    container.supervisor = lambda spec: _evidence(
        b"0123456789", b"abcdefghij", stdout_cap=4, stderr_cap=4
    )

    result = await _run(container, tmp_path)

    assert (result.stdout, result.stderr) == (b"0123", b"ghij")
    assert result.record["stdout_bytes"] == result.record["stderr_bytes"] == 10
    assert result.integrity_error is None


@pytest.mark.parametrize(
    ("reply", "fragment"),
    [
        ("", "the supervisor printed no result"),
        ("not json\n", "the supervisor printed no result"),
        ("[1]\n", "the supervisor printed no result"),
        (_evidence() | {"schema": "other/1"}, "the supervisor printed no result"),
        (
            {k: v for k, v in _evidence().items() if k != "stdout_sha256"},
            "missing call evidence: stdout_sha256",
        ),
        (_evidence(b"abc", stdout_sha256="0" * 64), "stdout digest does not match its bytes"),
        (
            _evidence(stderr=b"abc", stderr_sha256="0" * 64),
            "stderr digest does not match its bytes",
        ),
        (_evidence(isolation_error="no seccomp"), "isolation could not be installed: no seccomp"),
        (
            _evidence(ran=False, tool_before=[{"root": "/t", "ok": False, "digest": "d"}]),
            "installed tree /t changed before the call",
        ),
        (
            _evidence(tool_after=[{"root": "/t", "ok": False, "digest": "d"}]),
            "installed tree /t changed during the call",
        ),
        (_evidence(ran=False), "missing call evidence: the child did not run"),
    ],
)
async def test_missing_or_inconsistent_supervisor_evidence_is_an_integrity_failure(
    container: Container, tmp_path: Path, reply: Any, fragment: str
) -> None:
    container.supervisor = lambda spec: reply

    result = await _run(container, tmp_path)

    assert result.integrity_error
    assert fragment in result.integrity_error
    assert not result.usable


async def test_a_bounds_violation_refuses_the_run_without_an_integrity_failure(
    container: Container, tmp_path: Path
) -> None:
    container.supervisor = lambda spec: _evidence(ran=False, bounds_violation="symlink: l")

    result = await _run(container, tmp_path, state="/watched")

    assert (result.ran, result.usable) == (False, False)
    assert result.bounds_violation == "symlink: l"
    assert result.integrity_error is None


async def test_undecodable_captured_bytes_are_empty_not_an_error(
    container: Container, tmp_path: Path
) -> None:
    container.supervisor = lambda spec: _evidence(stdout_b64="!!not base64!!")

    result = await _run(container, tmp_path)

    assert result.stdout == b""


async def test_a_supervisor_that_exits_non_zero_leaves_no_call_directory_behind(
    container: Container, tmp_path: Path
) -> None:
    container.supervisor = lambda spec: _exec_result("", 137, "oom")

    result = await _run(container, tmp_path)

    assert result.record["supervisor_exit"] == 137
    assert result.integrity_error
    assert container.cleaned(container.specs[0]["call_dir"])


async def test_a_container_transport_failure_raises_and_cleans_up(
    container: Container, tmp_path: Path
) -> None:
    container.fail["supervise"] = ConnectionError("docker daemon gone")

    with pytest.raises(calls.TransportError, match="docker daemon gone"):
        await _run(container, tmp_path)

    assert container.cleaned(f"{calls.CALLS_ROOT}/")


async def test_a_cancelled_call_cleans_up_its_call_directory(
    container: Container, tmp_path: Path
) -> None:
    container.fail["supervise"] = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await _run(container, tmp_path)

    assert container.cleaned(f"{calls.CALLS_ROOT}/")


@pytest.mark.parametrize("timeout", [0, -1, calls.MAX_TIMEOUT_S + 1])
async def test_timeouts_outside_the_contract_are_refused_before_the_container_is_touched(
    container: Container, tmp_path: Path, timeout: float
) -> None:
    with pytest.raises(ValueError, match="timeout_s"):
        await _run(container, tmp_path, timeout_s=timeout)

    assert container.commands == []


@pytest.mark.parametrize("argv", [[], [1], ["tool", None]])
async def test_an_argv_that_is_not_a_list_of_strings_is_refused(
    container: Container, tmp_path: Path, argv: list[Any]
) -> None:
    with pytest.raises(ValueError, match="argv"):
        await calls.CallRunner(container, tmp_path / "logs").run("ns/pkg", "setup/1", argv, CONTEXT)

    assert container.commands == []


# ------------------------------------------------------------------- the trusted payload
async def test_the_trusted_payload_is_verified_once_per_environment(
    container: Container, tmp_path: Path
) -> None:
    runner = calls.CallRunner(container, tmp_path / "logs")
    await _run(container, tmp_path, runner)
    await _run(container, tmp_path, runner)

    hashes = [c for c in container.commands if "-B -c" in c]
    assert len(hashes) == 1
    assert not any(c.startswith(f"rm -rf {calls.TRUSTED_ROOT}") for c in container.commands)


async def test_an_environment_that_cannot_be_remembered_is_verified_on_every_use(
    tmp_path: Path,
) -> None:
    class Unhashable(Container):
        def __eq__(self, other: object) -> bool:
            return self is other

        __hash__ = None  # type: ignore[assignment]

    container = Unhashable()
    runner = calls.CallRunner(container, tmp_path / "logs")
    await _run(container, tmp_path, runner)
    await _run(container, tmp_path, runner)

    assert len([c for c in container.commands if "-B -c" in c]) == 2


async def test_a_stale_trusted_payload_is_replaced_and_checked_again(tmp_path: Path) -> None:
    container = Container(digest="stale")

    assert await calls.ensure_trusted(container) == calls.payload_digest()

    assert container.digest == calls.payload_digest()
    assert set(calls.payload_files()) <= {
        name.removeprefix(f"{calls.TRUSTED_ROOT}/") for name in container.files
    }
    assert any(c.startswith(f"rm -rf {calls.TRUSTED_ROOT}") for c in container.commands)


async def test_a_payload_that_still_differs_after_installation_is_a_transport_error() -> None:
    container = Container(digest="stale", after_upload="still stale")

    with pytest.raises(calls.TransportError, match="does not match after installation"):
        await calls.ensure_trusted(container)


def test_the_payload_digest_depends_on_every_file_name_and_byte() -> None:
    base = {"a": b"1", "b": b"2"}

    assert calls.payload_digest(base) == calls.payload_digest(dict(reversed(base.items())))
    assert calls.payload_digest(base) != calls.payload_digest({"a": b"1", "b": b"3"})
    assert calls.payload_digest(base) != calls.payload_digest({"a": b"1", "c": b"2"})


# ---------------------------------------------------------------------- payload commands
async def test_a_payload_command_runs_a_json_spec_and_removes_its_scratch(
    container: Container,
) -> None:
    container.payloads["probe"] = lambda spec: "noise\n" + json.dumps({"seen": spec})

    document = await calls.run_payload(container, "probe", {"paths": ["/x"]})

    assert document == {"seen": {"paths": ["/x"]}}
    operation = next(f for f in container.files if f.endswith("/spec.json")).removesuffix(
        "/spec.json"
    )
    assert container.cleaned(operation)


async def test_a_payload_command_in_the_callers_scratch_leaves_it_to_the_caller(
    container: Container,
) -> None:
    container.payloads["probe"] = lambda spec: {"ok": True}

    await calls.run_payload(container, "probe", {}, scratch="/var/mine")

    assert "/var/mine/spec.json" in container.files
    assert not any("mkdir" in c or c.startswith("rm -rf") for c in container.commands)


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        ("", "printed no result"),
        ("   \n", "printed no result"),
        ("garbage", "printed no result"),
        ("[1]", "printed list"),
    ],
)
async def test_a_payload_that_prints_no_result_document_is_a_transport_error(
    container: Container, reply: str, message: str
) -> None:
    container.payloads["probe"] = lambda spec: reply

    with pytest.raises(calls.TransportError, match=message):
        await calls.run_payload(container, "probe", {})

    assert any(c.startswith("rm -rf") for c in container.commands)


async def test_a_failed_root_command_is_a_transport_error_with_its_stderr_tail(
    container: Container,
) -> None:
    container.payloads["probe"] = lambda spec: _exec_result("", 2, "x" * 900 + "disk full")

    with pytest.raises(calls.TransportError, match=r"exited 2: x+disk full") as caught:
        await calls.run_payload(container, "probe", {})

    assert len(str(caught.value)) < 800


async def test_resource_floors_report_what_the_payload_probes(container: Container) -> None:
    container.payloads["probe"] = lambda spec: {
        "disk_free": dict.fromkeys(spec["paths"], 7),
        "memory_available": None,
    }

    floors = await calls.resource_floors(container, ["/a", "/b"])

    assert floors == calls.ResourceFloors({"/a": 7, "/b": 7}, None)


# ----------------------------------------------------------------------------- manifests
def _row(path: str, data: bytes = b"x", **fields: Any) -> dict[str, Any]:
    row = {
        "path": path, "type": "file", "size": len(data), "mode": 420,
        "sha256": hashlib.sha256(data).hexdigest(),
    }  # fmt: skip
    return {**row, **fields}


def _document(rows: list[dict[str, Any]], **over: Any) -> dict[str, Any]:
    document = {
        "path": "x", "exists": True, "violation": None, "files": len(rows),
        "bytes": sum(row["size"] for row in rows), "entries": rows,
        "digest": calls._entry_digest(rows),
    }  # fmt: skip
    return {**document, **over}


def _record(rows: list[dict[str, Any]]) -> calls.TreeManifest:
    return calls.record_manifest(_document(rows))


def test_a_malformed_record_manifest_is_a_violation_not_a_raise() -> None:
    for document in ({"path": "x"}, {"path": "x", "entries": 5}, {"entries": [{"path": 1}]}):
        assert calls.record_manifest(document).violation.startswith("malformed manifest")
    wrong = _document([_row("a")], digest="0" * 64)
    assert "does not match" in calls.record_manifest(wrong).violation


def test_a_well_formed_record_manifest_is_the_trusted_shape() -> None:
    manifest = _record([_row("a"), _row("l", type="link", target="a", sha256=None)])

    assert manifest.ok
    assert [(e.path, e.kind, e.target) for e in manifest.entries] == [
        ("a", "file", None),
        ("l", "link", "a"),
    ]


@pytest.mark.parametrize(
    ("kwargs", "limits", "entries"),
    [
        ({}, calls.STATE_LIMITS, True),
        (
            {"bounds": False, "links": True, "owner": 0, "entries": False},
            {"links": True, "owner": 0},
            False,
        ),
        ({"bounds": False}, {"links": False, "owner": None}, False),
        ({"bounds": False, "entries": True}, {"links": False, "owner": None}, True),
    ],
)
async def test_a_manifest_request_is_bounded_unless_the_tree_is_a_trusted_install(
    container: Container, kwargs: dict[str, Any], limits: dict[str, Any], entries: bool
) -> None:
    seen: list[dict[str, Any]] = []

    def manifest(spec: dict[str, Any]) -> dict[str, Any]:
        seen.append(spec)
        return _document([_row("a")], path=spec["path"])

    container.payloads["manifest"] = manifest

    found = await calls.tree_manifest(container, "/watch", **kwargs)

    assert seen == [{"path": "/watch", "limits": limits, "entries": entries}]
    assert found.ok
    assert found.path == "/watch"


async def test_a_trusted_manifest_that_contradicts_its_own_digest_raises(
    container: Container,
) -> None:
    container.payloads["manifest"] = lambda spec: _document([_row("a")], digest="0" * 64)

    with pytest.raises(calls.TransportError, match="does not match its entries"):
        await calls.tree_manifest(container, "/watch")


async def test_an_absent_path_and_an_empty_tree_are_manifests_not_errors(
    container: Container,
) -> None:
    container.payloads["manifest"] = lambda spec: _document(
        [], exists=False, violation="missing", digest="anything"
    )

    found = await calls.tree_manifest(container, "/nope")

    assert (found.exists, found.violation, found.entries) == (False, "missing", ())
    assert not found.ok


# -------------------------------------------------------------------- host-side snapshots
def _tar(
    members: Mapping[str, bytes] | list[tuple[str, bytes]], *, directories: tuple[str, ...] = ()
) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as bundle:
        for name in directories:
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            bundle.addfile(info)
        for name, data in dict(members).items() if isinstance(members, Mapping) else members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _with_archive(container: Container, data: bytes) -> str:
    container.files["/r/state.tar"] = data
    return "/r/state.tar"


@pytest.mark.parametrize(
    "name", ["../../escaped", "/tmp/escaped", "a/../../escaped", "a//b", "./a", "a/", ""]
)
async def test_a_manifest_name_that_leaves_the_destination_copies_nothing(
    container: Container, tmp_path: Path, name: str
) -> None:
    archive = _with_archive(container, _tar({name.lstrip("/") or "x": b"x"}))
    destination = tmp_path / "copy"

    found = await calls.copy_snapshot(container, _record([_row(name)]), archive, destination)

    assert found.violation
    assert container.downloads == 0
    assert not destination.exists()
    assert not (tmp_path / "escaped").exists()


@pytest.mark.parametrize(
    "rows",
    [
        [_row("a"), _row("a")],
        [_row("l", type="link", target="a")],
        [_row("s", type="special", sha256=None)],
        [_row("a", size=calls.STATE_LIMITS["max_bytes"] + 1)],
        [_row("a", size=-1)],
        [_row("a", sha256="not-a-digest")],
        [_row("a", sha256=None)],
        [_row("/".join(["d"] * 40))],
    ],
)
async def test_a_manifest_with_repeats_links_or_numbers_past_the_bounds_is_refused_unfetched(
    container: Container, tmp_path: Path, rows: list[dict[str, Any]]
) -> None:
    archive = _with_archive(container, _tar({"a": b"x"}))

    found = await calls.copy_snapshot(container, _record(rows), archive, tmp_path / "copy")

    assert found.violation
    assert container.downloads == 0
    assert not (tmp_path / "copy").exists()


@pytest.mark.parametrize(
    ("limits", "rows", "message"),
    [
        ({"max_files": 1}, [_row("a"), _row("b"), _row("c")], "too many entries"),
        ({"max_files": 1}, [_row("a"), _row("b")], "beyond the state bounds"),
        ({"max_bytes": 8}, [_row("a", b"12345"), _row("b", b"12345")], "beyond the state bounds"),
    ],
)
async def test_counts_and_total_sizes_past_the_state_bounds_are_refused(
    container: Container,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limits: dict[str, int],
    rows: list[dict[str, Any]],
    message: str,
) -> None:
    monkeypatch.setattr(calls, "STATE_LIMITS", {**calls.STATE_LIMITS, **limits})

    found = await calls.copy_snapshot(container, _record(rows), "/r/none", tmp_path / "copy")

    assert found.violation
    assert message in found.violation
    assert not (tmp_path / "copy").exists()


async def test_verified_members_are_written_host_side_with_their_directories(
    container: Container, tmp_path: Path
) -> None:
    members = {"a.txt": b"alpha", "sub/b.bin": b"\x00\xff", "empty": b""}
    rows = [_row(n, d) for n, d in members.items()]
    rows.append(_row("sub", type="dir", size=0, sha256=None, mode=493))
    archive = _with_archive(container, _tar(members))

    found = await calls.copy_snapshot(container, _record(rows), archive, tmp_path / "copy")

    assert found.ok
    assert {
        p.relative_to(tmp_path / "copy").as_posix(): p.read_bytes()
        for p in (tmp_path / "copy").rglob("*")
        if p.is_file()
    } == members


async def test_long_unicode_paths_fit_the_snapshot_archive_bound(
    container: Container, tmp_path: Path
) -> None:
    folder = "/".join(["é" * 60] * 3)
    members = {f"{folder}/{number}.txt": b"x" for number in range(100)}
    archive = _with_archive(container, _tar(members))
    manifest = _record([_row(name, data) for name, data in members.items()])

    found = await calls.copy_snapshot(container, manifest, archive, tmp_path / "copy")

    assert found.violation is None
    assert len(list((tmp_path / "copy").rglob("*.txt"))) == 100


async def test_an_archive_bigger_than_its_manifest_is_never_downloaded(
    container: Container, tmp_path: Path
) -> None:
    archive = _with_archive(container, _tar({"a": b"x" * 1_000_000}))

    found = await calls.copy_snapshot(container, _record([_row("a")]), archive, tmp_path / "c")

    assert found.violation == "the archive is larger than its manifest allows"
    assert container.downloads == 0


async def test_an_archive_that_is_missing_or_has_no_size_is_a_violation(
    container: Container, tmp_path: Path
) -> None:
    found = await calls.copy_snapshot(
        container, _record([_row("a")]), "/r/gone.tar", tmp_path / "c"
    )

    assert found.violation == "the archive is missing or unreadable"
    assert container.downloads == 0


@pytest.mark.parametrize(
    ("members", "message"),
    [
        ({"a": b"yy"}, "not the size its manifest says"),
        ({"a": b"y"}, "changed while it was copied"),
        ({"a": b"x", "extra": b"x"}, "unexpected archive member 'extra'"),
        ([("a", b"x"), ("a", b"x")], "unexpected archive member 'a'"),
        ({}, "archive is missing files"),
    ],
)
async def test_a_member_that_is_not_what_its_manifest_says_leaves_nothing(
    container: Container,
    tmp_path: Path,
    members: Mapping[str, bytes] | list[tuple[str, bytes]],
    message: str,
) -> None:
    archive = _with_archive(container, _tar(members))

    found = await calls.copy_snapshot(container, _record([_row("a")]), archive, tmp_path / "copy")

    assert found.violation
    assert message in found.violation
    assert not (tmp_path / "copy").exists()


async def test_a_non_regular_member_is_never_unpacked(container: Container, tmp_path: Path) -> None:
    archive = _with_archive(container, _tar({"a": b"x"}, directories=("a",)))

    found = await calls.copy_snapshot(container, _record([_row("a")]), archive, tmp_path / "copy")

    assert found.violation
    assert "unexpected archive member" in found.violation
    assert not (tmp_path / "copy").exists()


async def test_garbage_where_an_archive_should_be_is_a_violation_not_an_error(
    container: Container, tmp_path: Path
) -> None:
    archive = _with_archive(container, b"\x00not a tar" * 100)

    found = await calls.copy_snapshot(container, _record([_row("a")]), archive, tmp_path / "c")

    assert found.violation
    assert found.violation.startswith("unreadable archive")
    assert not (tmp_path / "c").exists()


async def test_a_failed_archive_download_is_a_transport_error(
    container: Container, tmp_path: Path
) -> None:
    archive = _with_archive(container, _tar({"a": b"x"}))
    container.fail_transfer = OSError("link down")

    with pytest.raises(calls.TransportError, match="download_file failed: OSError: link down"):
        await calls.copy_snapshot(container, _record([_row("a")]), archive, tmp_path / "c")


def _snapshot_payload(
    container: Container, members: Mapping[str, bytes], **over: Any
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def snapshot(spec: dict[str, Any]) -> dict[str, Any]:
        assert spec["limits"] == calls.STATE_LIMITS
        container.files[spec["tar"]] = _tar(members)
        return _document([_row(n, d) for n, d in members.items()], path=spec["path"], **over)

    return snapshot


async def test_a_snapshot_copies_what_the_payload_archived_and_removes_its_operation(
    container: Container, tmp_path: Path
) -> None:
    container.payloads["snapshot"] = _snapshot_payload(container, {"a.txt": b"alpha"})

    found = await calls.snapshot_state(container, "/state", tmp_path / "copy")

    assert found.ok
    assert (tmp_path / "copy" / "a.txt").read_text() == "alpha"
    operation = next(c for c in container.commands if c.startswith("mkdir -m 0755")).split()[-1]
    assert container.cleaned(operation)


async def test_a_snapshot_the_payload_refuses_copies_nothing(
    container: Container, tmp_path: Path
) -> None:
    container.payloads["snapshot"] = _snapshot_payload(
        container, {"a.txt": b"alpha"}, violation="symlink: l"
    )

    found = await calls.snapshot_state(container, "/state", tmp_path / "copy")

    assert found.violation == "symlink: l"
    assert container.downloads == 0
    assert not (tmp_path / "copy").exists()


# --------------------------------------------------------------------- root helpers, transfers
async def test_a_root_file_is_written_root_owned_with_its_mode(container: Container) -> None:
    await calls.put_root_file(container, "/var/calls/closed/pkg", b"closed", 0o640)

    assert container.files["/var/calls/closed/pkg"] == b"closed"
    assert "mkdir -p -m 0755 /var/calls/closed" in container.commands[0]
    assert "chown 0:0 /var/calls/closed/pkg" in container.commands[-1]
    assert "chmod 640 /var/calls/closed/pkg" in container.commands[-1]


@pytest.mark.parametrize(("code", "exists"), [(0, True), (1, False)])
async def test_a_root_file_exists_when_the_test_succeeds(
    container: Container, code: int, exists: bool
) -> None:
    container.codes["[ -e"] = code

    assert await calls.root_file_exists(container, "/x") is exists


async def test_an_existence_check_that_cannot_run_is_a_transport_error(
    container: Container,
) -> None:
    container.codes["[ -e"] = 127

    with pytest.raises(calls.TransportError, match="exited 127"):
        await calls.root_file_exists(container, "/x")


async def test_state_is_uploaded_regular_only_into_a_new_child_owned_directory(
    container: Container, tmp_path: Path
) -> None:
    source = tmp_path / "fixture"
    (source / "d").mkdir(parents=True)
    (source / "d" / "a.txt").write_text("alpha")

    await calls.upload_state(container, source, "/var/calls/canary/state")

    assert container.files["/var/calls/canary/state/d/a.txt"] == b"alpha"
    assert "[ ! -e /var/calls/canary/state ]" in container.commands[0]
    assert "chown -R 1000:1000 /var/calls/canary/state" in container.commands[-1]


async def test_state_is_never_uploaded_over_an_existing_tree(
    container: Container, tmp_path: Path
) -> None:
    container.codes["[ ! -e"] = 1

    with pytest.raises(calls.TransportError):
        await calls.upload_state(container, tmp_path, "/var/calls/state")

    assert container.files == {}


async def test_state_with_a_link_is_refused_before_the_container_is_touched(
    container: Container, tmp_path: Path
) -> None:
    (tmp_path / "link").symlink_to(tmp_path)

    with pytest.raises(ValueError, match="regular"):
        await calls.upload_state(container, tmp_path, "/var/calls/state")

    assert container.commands == []


@pytest.mark.parametrize("name", ["upload_file", "upload_dir", "download_file", "download_dir"])
async def test_a_failed_transfer_is_a_transport_error_naming_it(tmp_path: Path, name: str) -> None:
    async def broken(*args: Any) -> None:
        raise OSError("disk full")

    environment = SimpleNamespace(**{name: broken})

    with pytest.raises(calls.TransportError, match=f"{name} failed: OSError: disk full"):
        await getattr(calls, name)(environment, tmp_path, "/x")


async def test_a_failed_excluding_download_is_a_transport_error(tmp_path: Path) -> None:
    async def broken(**kwargs: Any) -> None:
        raise OSError("stream cut")

    environment = SimpleNamespace(download_dir_with_exclusions=broken)

    with pytest.raises(calls.TransportError, match="download failed: OSError: stream cut"):
        await calls.download_dir_excluding(environment, "/w", tmp_path, ["x"])


async def test_a_command_reaches_the_environment_as_given_with_whole_second_timeouts() -> None:
    seen: dict[str, Any] = {}

    async def exec_(command: str, **kwargs: Any) -> Any:
        seen.update(command=command, **kwargs)
        return _exec_result()

    await calls.trusted_exec(
        SimpleNamespace(exec=exec_),
        "true",
        user="root",
        timeout_sec=2.7,
        cwd="/w",
        env_vars={"A": "1"},
    )

    assert seen == {
        "command": "true",
        "cwd": "/w",
        "env": {"A": "1"},
        "timeout_sec": 2,
        "user": "root",
    }


# ------------------------------------------------------------------------------ seccomp
@pytest.mark.parametrize("arch", ["x86_64", "aarch64"])
def test_the_seccomp_program_is_well_formed_and_names_the_denied_calls(arch: str) -> None:
    program = isolate.build_filter(arch)
    instructions = list(struct.iter_unpack("HBBI", program))

    for position, (code, jump_true, _, _) in enumerate(instructions):
        if code in (0x15, 0x35):
            assert position + 1 + jump_true < len(instructions)
    assert instructions[-1][0] == 0x06
    numbers = {k for code, _, _, k in instructions if code == 0x15}
    assert numbers >= {41 if arch == "x86_64" else 198, 425, 426, 427, 438}


# ------------------------------------------------------------------------- install/1
INDEX = "https://pypi.org/simple"
SDIST = (
    'sdist = { url = "https://files.pythonhosted.org/d.tar.gz", '
    f'hash = "sha256:{"c" * 64}", size = 1 }}\n'
)
PROJECT = '[[package]]\nname = "tool"\nversion = "0.1.0"\nsource = { virtual = "." }\n'
EMPTY_LOCK = "version = 1\n\n" + PROJECT


@dataclass(frozen=True)
class Shipped:
    path: str
    data: bytes
    executable: bool = False

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def _package(extra: str = "", wheel: str = "demo-1.0-py3-none-any.whl", source: str = "") -> str:
    url = f"https://files.pythonhosted.org/packages/{wheel}"
    source = source or f'{{ registry = "{INDEX}" }}'
    return (
        f'version = 1\n\n[[package]]\nname = "demo"\nversion = "1.0"\nsource = {source}\n'
        f'{extra}wheels = [{{ url = "{url}", hash = "sha256:{"b" * 64}", size = 1 }}]\n\n{PROJECT}'
    )


def _shipped(lock: str | None = EMPTY_LOCK) -> list[Shipped]:
    files = [
        Shipped("prompt.md", b"prompt"),
        Shipped("tool/pyproject.toml", b"[project]\n"),
        Shipped("tool/src/mod.py", b"VALUE = 1\n", executable=True),
    ]
    if lock is not None:
        files.append(Shipped("tool/uv.lock", lock.encode()))
    return files


def _installed_tree(**over: Any) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return lambda spec: _document(
        [], path=spec["path"], files=4, bytes=40, digest="tree-digest", **over
    )


def _installer(container: Container, outcome: Any = None) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def install_(spec: dict[str, Any]) -> Any:
        seen.append(spec)
        return {"ok": True} if outcome is None else outcome

    container.payloads["install"] = install_
    container.payloads["manifest"] = _installed_tree()
    return seen


async def _install(
    container: Container, tmp_path: Path, files: list[Shipped] | None = None, **options: Any
) -> install.InstalledPackage:
    return await install.install_package(
        container,
        tmp_path / "logs",
        reference="ns/pkg",
        package_digest="d" * 64,
        files=_shipped() if files is None else files,
        **options,
    )


async def test_a_package_is_installed_from_its_tool_files_and_recorded(
    container: Container, tmp_path: Path
) -> None:
    seen = _installer(container)

    package = await _install(container, tmp_path, commands={"tool": ["python", "-m", "mod"]})

    spec = seen[0]
    assert package.root == f"{calls.STAGES_ROOT}/ns/pkg"
    assert (package.digest, package.package_digest) == ("tree-digest", "d" * 64)
    assert package.commands == {"tool": ("python", "-m", "mod")}
    assert package.wheels == ()
    assert package.bin == f"{package.root}/bin"
    assert package.path == f"{package.root}/bin:{calls.BASE_PATH}"
    assert package.python == f"{package.root}/venv/bin/python"
    assert (spec["root"], spec["venv"], spec["uv"]) == (package.root, True, "/usr/local/bin/uv")
    assert spec["python"] == calls.TRUSTED_PYTHON
    assert spec["links"] == install.LINKS_DIR
    assert spec["commands"] == {"tool": ["python", "-m", "mod"]}
    assert [(f["path"], f["exec"]) for f in spec["files"]] == [
        ("tool/pyproject.toml", False),
        ("tool/src/mod.py", True),
        ("tool/uv.lock", False),
    ]
    assert container.files[f"{spec['stage']}/files/tool/src/mod.py"] == b"VALUE = 1\n"
    assert f"{spec['stage']}/files/prompt.md" not in container.files  # only the tool is shipped
    assert container.cleaned(spec["stage"])
    record = json.loads((tmp_path / "logs" / "install" / "ns--pkg.json").read_text())
    assert record == package.record
    assert (record["schema"], record["arch"], record["tree_digest"]) == (
        "install/1",
        "amd64",
        "tree-digest",
    )
    assert (record["tree_files"], record["tree_bytes"]) == (4, 40)
    assert record["commands"] == {"tool": ["python", "-m", "mod"]}


async def test_a_package_with_no_tool_files_installs_without_a_venv(
    container: Container, tmp_path: Path
) -> None:
    seen = _installer(container)
    container.answers["uname -m"] = "aarch64\n"

    package = await _install(container, tmp_path, [Shipped("prompt.md", b"prompt")])

    assert seen[0]["venv"] is False
    assert seen[0]["files"] == []
    assert package.record["arch"] == "arm64"


@pytest.mark.parametrize(("owner", "expected"), [(0, 0), (None, None)])
async def test_the_installed_tree_is_measured_no_follow_with_the_required_owner(
    container: Container, tmp_path: Path, owner: int | None, expected: int | None
) -> None:
    _installer(container)
    measured: list[dict[str, Any]] = []

    def measure(spec: dict[str, Any]) -> dict[str, Any]:
        measured.append(spec)
        return _installed_tree()(spec)

    container.payloads["manifest"] = measure

    package = await _install(container, tmp_path, owner=owner)

    assert measured == [
        {"path": package.root, "limits": {"links": True, "owner": expected}, "entries": False}
    ]
    assert package.owner == expected


async def test_a_failed_root_install_is_an_install_error_and_nothing_is_measured(
    container: Container, tmp_path: Path
) -> None:
    _installer(container, {"ok": False, "error": "uv exploded"})
    measured: list[Any] = []

    def measure(spec: Any) -> dict[str, Any]:
        measured.append(spec)
        return {}

    container.payloads["manifest"] = measure

    with pytest.raises(install.InstallError, match="uv exploded"):
        await _install(container, tmp_path)

    assert measured == []
    assert any(c.startswith("rm -rf") and "/install-" in c for c in container.commands)
    assert not (tmp_path / "logs").exists()


async def test_a_root_install_that_gives_no_reason_still_fails_with_one(
    container: Container, tmp_path: Path
) -> None:
    _installer(container, {"ok": False})

    with pytest.raises(install.InstallError, match="the root install failed"):
        await _install(container, tmp_path)


async def test_an_installed_tree_with_a_violation_is_refused(
    container: Container, tmp_path: Path
) -> None:
    _installer(container)
    container.payloads["manifest"] = _installed_tree(violation="owner: bin/tool")

    with pytest.raises(install.InstallError, match="owner: bin/tool"):
        await _install(container, tmp_path)

    assert not (tmp_path / "logs").exists()


@pytest.mark.parametrize(
    ("lock", "message"),
    [
        (None, "no tool/uv.lock"),
        (_package(SDIST), "sdist"),
        ("version = 1\n" + PROJECT + SDIST, "sdist"),
        (_package(wheel="demo-1.0-cp311-cp311-win_amd64.whl"), "no (arm64|amd64) CPython"),
        (_package(source='{ git = "https://x/y" }'), "not an index package"),
        (_package(source='{ path = "../demo" }'), "not an index package"),
    ],
)
async def test_source_builds_and_unlocked_dependencies_are_refused_before_anything_runs(
    container: Container, tmp_path: Path, lock: str | None, message: str
) -> None:
    _installer(container)

    with pytest.raises(install.InstallError, match=message):
        await _install(container, tmp_path, _shipped(lock))

    assert not any("command -v uv" in c or "/install-" in c for c in container.commands)
    assert not any(f.startswith(calls.STAGES_ROOT) for f in container.files)


async def test_a_shipped_path_that_leaves_the_tool_tree_is_refused(
    container: Container, tmp_path: Path
) -> None:
    _installer(container)

    with pytest.raises(install.InstallError, match="unsafe shipped path"):
        await _install(container, tmp_path, [*_shipped(), Shipped("tool/../evil", b"x")])


async def test_an_unsupported_container_architecture_is_refused(
    container: Container, tmp_path: Path
) -> None:
    _installer(container)
    container.answers["uname -m"] = "riscv64\n"

    with pytest.raises(install.InstallError, match="unsupported container architecture 'riscv64'"):
        await _install(container, tmp_path)


@pytest.mark.parametrize("reference", ["../x", "Ns/Pkg", "a/b/c", ""])
async def test_a_bad_reference_is_refused_before_the_container_is_touched(
    container: Container, tmp_path: Path, reference: str
) -> None:
    with pytest.raises(install.InstallError, match="reference"):
        await install.install_package(
            container, tmp_path, reference=reference, package_digest="d", files=[]
        )
    with pytest.raises(install.InstallError, match="reference"):
        await install.reuse_installed(container, tmp_path, reference=reference, package_digest="d")

    assert container.commands == []


async def test_nothing_is_reused_when_nothing_was_installed(
    container: Container, tmp_path: Path
) -> None:
    container.codes["[ -e"] = 1

    reused = await install.reuse_installed(
        container, tmp_path / "logs", reference="ns/pkg", package_digest="d" * 64
    )

    assert reused is None
    assert not any("uname" in c for c in container.commands)
    assert not (tmp_path / "logs").exists()


async def test_a_later_checkpoint_measures_the_tree_as_found_and_repairs_nothing(
    container: Container, tmp_path: Path
) -> None:
    _installer(container)
    first = await _install(container, tmp_path)
    container.payloads["manifest"] = lambda spec: _document(
        [], path=spec["path"], digest="changed", files=5, bytes=50
    )
    container.commands.clear()

    again = await install.reuse_installed(
        container,
        tmp_path / "logs-2",
        reference="ns/pkg",
        package_digest="d" * 64,
        files=_shipped(),
    )

    assert again is not None
    assert again.digest == "changed" != first.digest
    assert again.record["tree_files"] == 5
    assert again.wheels == ()
    assert (tmp_path / "logs-2" / "install" / "ns--pkg.json").is_file()
    assert not any("rm -rf" in c and calls.STAGES_ROOT in c for c in container.commands)


async def test_a_checkpoint_without_shipped_files_records_no_wheels(
    container: Container, tmp_path: Path
) -> None:
    _installer(container)

    again = await install.reuse_installed(
        container, tmp_path / "logs", reference="ns/pkg", package_digest="d" * 64
    )

    assert again is not None
    assert again.record["wheels"] == []


async def test_an_installed_tree_that_is_not_root_owned_or_holds_a_special_file_is_refused(
    container: Container, tmp_path: Path
) -> None:
    _installer(container)
    container.payloads["manifest"] = _installed_tree(violation="special file: pipe")

    with pytest.raises(install.InstallError, match="special file"):
        await install.reuse_installed(
            container, tmp_path / "logs", reference="ns/pkg", package_digest="d" * 64
        )


@pytest.mark.parametrize(
    ("digest", "violation", "ok"),
    [("tree-digest", None, True), ("planted", None, False), ("tree-digest", "symlink: l", False)],
)
async def test_an_installed_tree_verifies_only_when_unchanged_and_clean(
    container: Container, tmp_path: Path, digest: str, violation: str | None, ok: bool
) -> None:
    _installer(container)
    package = await _install(container, tmp_path)
    container.payloads["manifest"] = lambda spec: _document(
        [], path=spec["path"], digest=digest, violation=violation
    )

    check = await package.verify()

    assert check.ok is ok
    assert (check.digest, check.expected, check.violation) == (digest, "tree-digest", violation)
