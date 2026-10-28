"""The container transport of every primitive, and the authored ``CallRunner`` protocol.

This is the only module that executes, uploads or downloads through a Harbor environment.
Raw root transport (``trusted_exec``, uploads, ``tree_manifest``, ``snapshot_state``...) runs
only Raidar's own commands and its immutable stdlib payload (``payload/``, root-owned under
``TRUSTED_ROOT``, run with the pack's ``python -I -S -B``). Authored code runs only through
``CallRunner.run``: a root supervisor drops the child to UID/GID 1000 with an empty
environment, fresh ``HOME``/``TMPDIR``, no inherited descriptors and a seccomp filter that
denies the network, captures bounded output, enforces the deadline over the child's whole
process tree and verifies the installed tool trees before and after. A container transport
failure raises ``TransportError``; it is never folded into a child's exit status.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import posixpath
import shlex
import shutil
import stat
import tarfile
import tempfile
import uuid
import weakref
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from harbor.environments.base import ExecResult
from pydantic import JsonValue

from raidar.subject.primitive.payload.supervise import (
    BASE_PATH,
    MAX_TIMEOUT_S,
    STATE_LIMITS,
    TREE_LIMITS,
    build_spec,
)

TRUSTED_ROOT = "/opt/raidar/trusted"
CALLS_ROOT = "/var/lib/raidar-calls"
STAGES_ROOT = "/opt/raidar/stages"
TRUSTED_PYTHON = "/usr/local/bin/python3"
CHILD_UID = CHILD_GID = 1000

DEFAULT_TIMEOUT_S = 60
SUPERVISOR_SLACK_S = 90
ROOT_TIMEOUT_S = 120
_PAYLOAD = Path(__file__).with_name("payload")
_LAUNCHER = (
    "import os, sys\n"
    "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
    "from payload.main import main\n"
    "raise SystemExit(main(sys.argv[1:]))\n"
)
_HASH_SCRIPT = (
    "import hashlib, os, sys\n"
    "root = sys.argv[1]\n"
    "rows = []\n"
    "for directory, _, names in os.walk(root):\n"
    "    for name in names:\n"
    "        path = os.path.join(directory, name)\n"
    "        digest = hashlib.sha256(open(path, 'rb').read()).hexdigest()\n"
    "        rows.append(os.path.relpath(path, root) + '\\0' + digest)\n"
    "print(hashlib.sha256(''.join(row + '\\n' for row in sorted(rows)).encode()).hexdigest())\n"
)


class TransportError(RuntimeError):
    """The container (exec, upload, download or a trusted root helper) failed.

    Distinct from a child's exit status: this is an integrity failure of the instrument.
    """


class IntegrityError(RuntimeError):
    """Evidence is missing or installed tool bytes changed; the trial is invalid."""


@dataclass(frozen=True)
class ChildPolicy:
    """How a call's child is confined. The default is the contract; tests may disable it.

    ``uid``/``gid`` ``None`` skips the drop and ``seccomp=False`` skips the filter: only for
    tests that run the supervisor as an unprivileged host process, never for a container.
    """

    uid: int | None = CHILD_UID
    gid: int | None = CHILD_GID
    seccomp: bool = True
    owner: int | None = 0
    """Required owner uid of installed trees (``None`` skips the check, tests only)."""


# ----------------------------------------------------------------------------- raw transport
def _q(text: str | Path) -> str:
    return shlex.quote(str(text))


def _chown(owner: str, path: str, *, recursive: bool = False) -> str:
    """``chown`` that an unprivileged (test) environment may skip; root's failure still fails."""
    flag = "-R " if recursive else ""
    return f'(chown {flag}{owner} {_q(path)} || [ "$(id -u)" != 0 ])'


async def trusted_exec(
    environment: Any,
    command: str,
    *,
    user: str | int | None = None,
    timeout_sec: float | None = None,
    cwd: str | None = None,
    env_vars: Mapping[str, str] | None = None,
) -> ExecResult:
    """One command through the environment; a transport failure raises ``TransportError``."""
    try:
        return await environment.exec(
            command,
            cwd=cwd,
            env=dict(env_vars) if env_vars else None,
            timeout_sec=None if timeout_sec is None else int(timeout_sec),
            user=user,
        )
    except Exception as error:
        raise TransportError(f"exec failed: {type(error).__name__}: {error}") from error


async def _root(environment: Any, command: str, *, timeout_sec: float = ROOT_TIMEOUT_S) -> str:
    """A root command that must succeed; its stdout."""
    result = await trusted_exec(environment, command, user="root", timeout_sec=timeout_sec)
    if result.return_code != 0:
        detail = (result.stderr or result.stdout or "").strip()[-500:]
        raise TransportError(f"root command {command[:80]!r} exited {result.return_code}: {detail}")
    return result.stdout or ""


async def root_command(
    environment: Any, command: str, *, timeout_sec: float = ROOT_TIMEOUT_S
) -> str:
    """A Raidar-authored root command (never authored code) that must succeed; its stdout."""
    return await _root(environment, command, timeout_sec=timeout_sec)


async def _transfer(environment: Any, name: str, *args: Any) -> None:
    try:
        await getattr(environment, name)(*args)
    except Exception as error:
        raise TransportError(f"{name} failed: {type(error).__name__}: {error}") from error


async def upload_file(environment: Any, source: Path, target: str) -> None:
    await _transfer(environment, "upload_file", source, target)


async def upload_dir(environment: Any, source: Path, target: str) -> None:
    await _transfer(environment, "upload_dir", source, target)


async def download_file(environment: Any, source: str, target: Path) -> None:
    await _transfer(environment, "download_file", source, target)


async def download_dir(environment: Any, source: str, target: Path) -> None:
    await _transfer(environment, "download_dir", source, target)


async def download_dir_excluding(
    environment: Any, source: str, target: Path, exclude: Sequence[str]
) -> None:
    """Harbor's streamed directory download with exclusions (workspace snapshots, unbounded)."""
    try:
        await environment.download_dir_with_exclusions(
            source_dir=source, target_dir=target, exclude=list(exclude)
        )
    except Exception as error:
        raise TransportError(f"download failed: {type(error).__name__}: {error}") from error


async def _upload_bytes(environment: Any, data: bytes, target: str) -> None:
    with tempfile.TemporaryDirectory(prefix="raidar-upload-") as scratch:
        source = Path(scratch) / "payload"
        source.write_bytes(data)
        await upload_file(environment, source, target)


async def put_root_file(environment: Any, path: str, data: bytes, mode: int = 0o644) -> None:
    """Write a root-owned file (parents created root-owned 0755), e.g. a closed flag."""
    parent = os.path.dirname(path)
    await _root(environment, f"mkdir -p -m 0755 {_q(parent)}")
    await _upload_bytes(environment, data, path)
    await _root(environment, f"{_chown('0:0', path)} && chmod {mode:o} {_q(path)}")


async def root_file_exists(environment: Any, path: str) -> bool:
    result = await trusted_exec(environment, f"[ -e {_q(path)} ]", user="root", timeout_sec=30)
    if result.return_code not in (0, 1):
        raise TransportError(f"existence check of {path} exited {result.return_code}")
    return result.return_code == 0


def _regular_only(root: Path) -> None:
    for directory, names, files in os.walk(root):
        for name in (*names, *files):
            mode = os.lstat(Path(directory) / name).st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError(f"{Path(directory) / name} is not a regular file or directory")


async def upload_state(environment: Any, source: Path, target: str) -> None:
    """Copy a host directory (regular files only) to a new container directory owned by 1000."""
    _regular_only(source)
    parent = os.path.dirname(target)
    await _root(environment, f"mkdir -p -m 0755 {_q(parent)} && [ ! -e {_q(target)} ]")
    await _root(environment, f"mkdir -m 0755 {_q(target)}")
    await upload_dir(environment, source, target)
    await _root(environment, _chown(f"{CHILD_UID}:{CHILD_GID}", target, recursive=True))


async def make_state_dir(environment: Any, target: str) -> None:
    """An empty directory the child's user can write (parents root-owned 0755)."""
    parent = os.path.dirname(target)
    await _root(
        environment,
        f"mkdir -p -m 0755 {_q(parent)} && mkdir -m 0755 {_q(target)} "
        f"&& {_chown(f'{CHILD_UID}:{CHILD_GID}', target)}",
    )


# --------------------------------------------------------------------------- trusted payload
def payload_files() -> dict[str, bytes]:
    """The payload as uploaded: relative path to bytes (the launcher and ``payload/*.py``)."""
    files = {"run.py": _LAUNCHER.encode()}
    for source in sorted(_PAYLOAD.glob("*.py")):
        files[f"payload/{source.name}"] = source.read_bytes()
    return files


def payload_digest(files: Mapping[str, bytes] | None = None) -> str:
    chosen = files if files is not None else payload_files()
    rows = sorted(f"{name}\0{hashlib.sha256(data).hexdigest()}" for name, data in chosen.items())
    return hashlib.sha256("".join(f"{row}\n" for row in rows).encode()).hexdigest()


def _launcher_command(subcommand: str, spec_path: str) -> str:
    launcher = _q(f"{TRUSTED_ROOT}/run.py")
    return f"{_q(TRUSTED_PYTHON)} -I -S -B {launcher} {subcommand} {_q(spec_path)}"


_READY: weakref.WeakSet[Any] = weakref.WeakSet()


async def _installed_digest(environment: Any) -> str | None:
    command = f"{_q(TRUSTED_PYTHON)} -I -S -B -c {_q(_HASH_SCRIPT)} {_q(TRUSTED_ROOT)}"
    result = await trusted_exec(environment, command, user="root", timeout_sec=60)
    return (result.stdout or "").strip() if result.return_code == 0 else None


async def _install_payload(environment: Any, files: Mapping[str, bytes]) -> None:
    await _root(
        environment,
        f"rm -rf {_q(TRUSTED_ROOT)} && mkdir -p -m 0755 {_q(TRUSTED_ROOT)} {_q(STAGES_ROOT)} "
        f"&& mkdir -p -m 0755 {_q(CALLS_ROOT)}",
    )
    with tempfile.TemporaryDirectory(prefix="raidar-payload-") as scratch:
        for name, data in files.items():
            target = Path(scratch) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        await upload_dir(environment, Path(scratch), TRUSTED_ROOT)
    await _root(
        environment,
        f"{_chown('0:0', TRUSTED_ROOT, recursive=True)} && chmod -R go-w,a+rX {_q(TRUSTED_ROOT)}",
    )


def _is_ready(environment: Any) -> bool:
    try:
        return environment in _READY
    except TypeError:  # not hashable: verified on every use
        return False


def _mark_ready(environment: Any) -> None:
    with contextlib.suppress(TypeError):
        _READY.add(environment)


async def ensure_trusted(environment: Any) -> str:
    """Install and verify the immutable payload (idempotent per environment); its digest."""
    files = payload_files()
    expected = payload_digest(files)
    if _is_ready(environment):
        return expected
    if await _installed_digest(environment) != expected:
        await _install_payload(environment, files)
        if await _installed_digest(environment) != expected:
            raise TransportError("the trusted payload does not match after installation")
    _mark_ready(environment)
    return expected


async def run_payload(
    environment: Any,
    command: str,
    spec: Mapping[str, Any],
    *,
    scratch: str | None = None,
    timeout_sec: float = ROOT_TIMEOUT_S,
) -> dict[str, Any]:
    """Run one payload command as root with a JSON spec; its one JSON result document."""
    await ensure_trusted(environment)
    own = scratch is None
    operation = scratch or f"{CALLS_ROOT}/op-{uuid.uuid4().hex}"
    try:
        if own:
            await _root(environment, f"mkdir -m 0755 {_q(operation)}")
        await _upload_bytes(environment, json.dumps(spec).encode(), f"{operation}/spec.json")
        output = await _root(
            environment,
            _launcher_command(command, f"{operation}/spec.json"),
            timeout_sec=timeout_sec,
        )
    finally:
        if own:
            await trusted_exec(environment, f"rm -rf {_q(operation)}", user="root", timeout_sec=60)
    try:
        document = json.loads(output.strip().splitlines()[-1])
    except (ValueError, IndexError) as error:
        raise TransportError(f"payload {command} printed no result: {output[-200:]!r}") from error
    if not isinstance(document, dict):
        raise TransportError(f"payload {command} printed {type(document).__name__}")
    return document


# ------------------------------------------------------------------------------- manifests
@dataclass(frozen=True)
class ManifestEntry:
    path: str
    kind: str
    size: int
    sha256: str
    mode: int
    target: str | None = None


@dataclass(frozen=True)
class TreeManifest:
    """A no-follow listing of a container path; ``violation`` says what must not be read."""

    path: str
    exists: bool
    entries: tuple[ManifestEntry, ...]
    digest: str
    files: int
    bytes: int
    violation: str | None

    @property
    def ok(self) -> bool:
        return self.violation is None


def _entry_digest(rows: list[dict[str, Any]]) -> str:
    text = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def _manifest(document: Mapping[str, Any]) -> TreeManifest:
    rows = list(document["entries"])
    if rows and _entry_digest(rows) != document["digest"]:
        raise TransportError(f"manifest digest of {document['path']} does not match its entries")
    entries = tuple(
        ManifestEntry(r["path"], r["type"], r["size"], r["sha256"], r["mode"], r.get("target"))
        for r in rows
    )
    return TreeManifest(
        document["path"],
        bool(document["exists"]),
        entries,
        document["digest"],
        int(document["files"]),
        int(document["bytes"]),
        document["violation"],
    )


def _limits(bounds: bool, links: bool, owner: int | None) -> dict[str, Any]:
    if bounds:
        return dict(STATE_LIMITS)
    return {"links": links, "owner": owner}


async def tree_manifest(
    environment: Any,
    path: str,
    *,
    bounds: bool = True,
    links: bool = False,
    owner: int | None = None,
    entries: bool | None = None,
) -> TreeManifest:
    """No-follow manifest of ``path``.

    ``bounds`` applies the 64 MiB / 10k files / depth 32 caps and refuses links and special
    files; otherwise the tree is unbounded (root-owned trusted installs, links recorded).
    """
    spec = {
        "path": path,
        "limits": _limits(bounds, links, owner),
        "entries": bounds if entries is None else entries,
    }
    return _manifest(await run_payload(environment, "manifest", spec))


def record_manifest(document: Mapping[str, Any]) -> TreeManifest:
    """A manifest read from a record the authored side could have shaped.

    Unlike a trusted payload's direct answer, a malformed one is a violation, never an error.
    """
    try:
        return _manifest(document)
    except (KeyError, TypeError, ValueError, TransportError) as error:
        path = str(document.get("path", ""))
        return TreeManifest(path, False, (), "", 0, 0, f"malformed manifest: {error}")


def _unsafe_path(path: object) -> bool:
    """A name that is not a clean, relative, bounded path (it could leave the destination)."""
    if not isinstance(path, str) or not path or path.startswith("/"):
        return True
    parts = PurePosixPath(path).parts
    clean = posixpath.normpath(path) == path and ".." not in parts
    return not clean or len(parts) > STATE_LIMITS["max_depth"]


def _unsafe_file(entry: ManifestEntry) -> bool:
    """A file entry without a sha256 digest or with a size outside the state bounds."""
    digest, size = entry.sha256, entry.size
    if not isinstance(digest, str) or len(digest) != 64 or not isinstance(size, int):
        return True
    return not 0 <= size <= STATE_LIMITS["max_bytes"]


def _unsafe_manifest(manifest: TreeManifest) -> str | None:
    """Refuse escaping/repeated names, special entries and counts/sizes past state bounds."""
    if len(manifest.entries) > 2 * STATE_LIMITS["max_files"]:
        return "manifest lists too many entries"
    names = [entry.path for entry in manifest.entries]
    if len(set(names)) != len(names) or any(_unsafe_path(name) for name in names):
        return "manifest has an unsafe or repeated path"
    return _unsafe_files(manifest.entries)


def _unsafe_files(entries: Sequence[ManifestEntry]) -> str | None:
    """Refuse non-regular entries and file counts/sizes outside the snapshot bounds."""
    files = [entry for entry in entries if entry.kind != "dir"]
    if any(entry.kind != "file" or _unsafe_file(entry) for entry in files):
        return "manifest has an entry that is not a plain file"
    if len(files) > STATE_LIMITS["max_files"]:
        return "manifest is beyond the state bounds"
    if sum(entry.size for entry in files) > STATE_LIMITS["max_bytes"]:
        return "manifest is beyond the state bounds"
    return None


def _archive_limit(manifest: TreeManifest) -> int:
    """Bound data plus UTF-8/PAX path records, block padding and tar end blocks."""
    return (
        sum(
            entry.size + 4 * len(entry.path) + 4096
            for entry in manifest.entries
            if entry.kind == "file"
        )
        + 20480
    )


def _unpack(bundle: tarfile.TarFile, destination: Path, expected: dict[str, ManifestEntry]) -> str:
    """Write each member the manifest lists, its size and digest checked; a violation or ``""``."""
    seen: set[str] = set()
    for member in bundle:
        entry = expected.get(member.name)
        source = bundle.extractfile(member) if member.isreg() else None
        if entry is None or source is None or member.name in seen:
            return f"unexpected archive member {member.name!r}"
        data = source.read(entry.size + 1)  # bounded by the manifest, never the archive
        if member.size != entry.size or len(data) != entry.size:
            return f"{member.name} is not the size its manifest says"
        if hashlib.sha256(data).hexdigest() != entry.sha256:
            return f"{member.name} changed while it was copied"
        target = destination / member.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        seen.add(member.name)
    return "" if seen == set(expected) else "archive is missing files"


def _extract(archive: Path, destination: Path, manifest: TreeManifest) -> str | None:
    """Unpack regular members host-side against the (already validated) manifest."""
    expected = {e.path: e for e in manifest.entries if e.kind == "file"}
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive) as bundle:
            return _unpack(bundle, destination, expected) or None
    except (tarfile.TarError, EOFError, OSError) as error:
        return f"unreadable archive: {error}"


async def _archive_problem(environment: Any, manifest: TreeManifest, remote: str) -> str | None:
    """Why the container's archive is not worth downloading (missing, or heavier than allowed)."""
    sized = await trusted_exec(environment, f"wc -c < {_q(remote)}", user="root", timeout_sec=30)
    try:
        size = int((sized.stdout or "").strip())
    except ValueError:
        return "the archive is missing or unreadable"
    if size > _archive_limit(manifest):
        return "the archive is larger than its manifest allows"
    return None


async def copy_snapshot(
    environment: Any, manifest: TreeManifest, remote_archive: str, destination: Path
) -> TreeManifest:
    """Download the archive a trusted ``snapshot`` wrote and unpack it host-side, verified.

    ``manifest`` may come from a record: its names, sizes and counts are validated and the
    archive's size is bounded by them before it is downloaded. A violation (the manifest's own,
    or archive bytes that do not match it) is in the returned manifest and leaves nothing at
    ``destination``; only a failed container call raises ``TransportError``.
    """
    problem = manifest.violation or _unsafe_manifest(manifest)
    problem = problem or await _archive_problem(environment, manifest, remote_archive)
    if problem is None:
        with tempfile.TemporaryDirectory(prefix="raidar-snapshot-") as scratch:
            archive = Path(scratch) / "state.tar"
            await download_file(environment, remote_archive, archive)
            problem = await asyncio.to_thread(_extract, archive, destination, manifest)
    if problem is None or problem == manifest.violation:
        return manifest
    shutil.rmtree(destination, ignore_errors=True)
    return replace(manifest, violation=problem)


async def snapshot_state(environment: Any, path: str, destination: Path) -> TreeManifest:
    """Bounded no-follow copy of a directory or file to ``destination``, verified host-side.

    A violation (link, special file, caps, changed while copied) is in the returned manifest.
    """
    operation = f"{CALLS_ROOT}/op-{uuid.uuid4().hex}"
    await ensure_trusted(environment)
    await _root(environment, f"mkdir -m 0755 {_q(operation)}")
    try:
        remote_archive = f"{operation}/state.tar"
        spec = {"path": path, "tar": remote_archive, "limits": dict(STATE_LIMITS)}
        document = await run_payload(environment, "snapshot", spec, scratch=operation)
        return await copy_snapshot(environment, _manifest(document), remote_archive, destination)
    finally:
        await trusted_exec(environment, f"rm -rf {_q(operation)}", user="root", timeout_sec=60)


@dataclass(frozen=True)
class ResourceFloors:
    """What the container can still give: free bytes per path and available memory."""

    disk_free: Mapping[str, int | None]
    memory_available: int | None


async def resource_floors(environment: Any, paths: Sequence[str]) -> ResourceFloors:
    document = await run_payload(environment, "probe", {"paths": list(paths)})
    return ResourceFloors(document["disk_free"], document["memory_available"])


# --------------------------------------------------------------------------------- calls
class ToolTree(Protocol):
    """An installed, immutable tool tree (``InstalledPackage``)."""

    @property
    def reference(self) -> str: ...

    @property
    def root(self) -> str: ...

    @property
    def digest(self) -> str: ...

    @property
    def bin(self) -> str: ...


@dataclass(frozen=True)
class CallResult:
    """One authored call: what the child did and whether the evidence is trustworthy."""

    record: dict[str, Any]
    stdout: bytes
    stderr: bytes
    exit_code: int | None
    signal: int | None
    timed_out: bool
    overflow: bool
    integrity_error: str | None
    bounds_violation: str | None
    record_path: Path
    ran: bool = field(default=False)

    @property
    def usable(self) -> bool:
        """A clean ``exit 0`` with valid evidence: no timeout, overflow, signal or violation."""
        flaws = (self.timed_out, self.overflow, self.integrity_error, self.bounds_violation)
        return self.ran and self.exit_code == 0 and not any(flaws)


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "--" for c in name)


def _check_trees(result: Mapping[str, Any], key: str, when: str) -> list[str]:
    return [
        f"installed tree {tree['root']} changed {when} the call"
        for tree in result.get(key, [])
        if not tree["ok"]
    ]


def _verify_streams(result: Mapping[str, Any], stdout: bytes, stderr: bytes) -> list[str]:
    problems: list[str] = []
    if (
        result["stdout_bytes"] == len(stdout)
        and hashlib.sha256(stdout).hexdigest() != result["stdout_sha256"]
    ):
        problems.append("stdout digest does not match its bytes")
    if (
        result["stderr_bytes"] <= len(stderr)
        and hashlib.sha256(stderr).hexdigest() != result["stderr_sha256"]
    ):
        problems.append("stderr digest does not match its bytes")
    return problems


_REQUIRED = (
    "exit_code",
    "signal",
    "stdout_b64",
    "stdout_sha256",
    "stdout_bytes",
    "stderr_b64",
    "stderr_sha256",
    "stderr_bytes",
)


def _ran_problems(result: Mapping[str, Any], stdout: bytes, stderr: bytes) -> list[str]:
    missing = sorted(set(_REQUIRED) - set(result))
    if missing:
        return [f"missing call evidence: {name}" for name in missing]
    return _verify_streams(result, stdout, stderr)


def _problems(result: Mapping[str, Any] | None, stdout: bytes, stderr: bytes) -> list[str]:
    """Why the call's evidence cannot be trusted (empty when it can)."""
    if result is None:
        return ["missing call evidence: the supervisor printed no result"]
    problems = _check_trees(result, "tool_before", "before")
    problems += _check_trees(result, "tool_after", "during")
    if result.get("isolation_error"):
        problems.append(f"isolation could not be installed: {result['isolation_error']}")
    if result.get("ran"):
        problems += _ran_problems(result, stdout, stderr)
    elif not (problems or result.get("bounds_violation")):
        problems.append("missing call evidence: the child did not run")
    return problems


def _decoded(result: Mapping[str, Any] | None, name: str) -> bytes:
    text = (result or {}).get(f"{name}_b64", "")
    try:
        return base64.b64decode(text, validate=True)
    except ValueError:
        return b""


def _parse_result(output: str) -> dict[str, Any] | None:
    lines = [line for line in output.splitlines() if line.strip()]
    try:
        document = json.loads(lines[-1])
    except (ValueError, IndexError):
        return None
    ok = isinstance(document, dict) and document.get("schema") == "call-result/1"
    return document if ok else None


class CallRunner:
    """Runs authored commands in one environment and records each call host-side."""

    def __init__(
        self, environment: Any, logs_dir: Path, *, policy: ChildPolicy | None = None
    ) -> None:
        self.environment = environment
        self.logs_dir = Path(logs_dir)
        self.policy = policy or ChildPolicy()
        self._sequence = 0

    async def run(
        self,
        package: str,
        primitive: str,
        argv: Sequence[str],
        context: Mapping[str, JsonValue],
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        path: str | None = None,
        cwd: str = "/app",
        append_context: bool = True,
        installed: Sequence[ToolTree] = (),
        state: str | None = None,
    ) -> CallResult:
        """Run ``argv`` (never a shell) as the confined child and record the call.

        ``append_context`` adds the final ``--context <path>`` pair (setup/stop); gate checks
        pass False and read ``RAIDAR_STAGE_CONTEXT`` instead. ``installed`` trees are verified
        before and after; ``state`` is bounds-checked before the child may read it.
        """
        if not argv or not all(isinstance(item, str) for item in argv):
            raise ValueError("argv must be a nonempty list of strings")
        if not 0 < timeout_s <= MAX_TIMEOUT_S:
            raise ValueError(f"timeout_s must be in (0, {MAX_TIMEOUT_S}]")
        await ensure_trusted(self.environment)
        self._sequence += 1
        sequence = self._sequence
        call_dir = f"{CALLS_ROOT}/{uuid.uuid4().hex}"
        context_path = f"{call_dir}/context.json"
        command = [*argv, "--context", context_path] if append_context else list(argv)
        environ = {
            "PATH": path or ":".join([*(tree.bin for tree in installed), BASE_PATH]),
            "LANG": "C.UTF-8",
            "HOME": f"{call_dir}/home",
            "TMPDIR": f"{call_dir}/tmp",
            "RAIDAR_STAGE_CONTEXT": context_path,
        }
        context_text = json.dumps(dict(context), sort_keys=True, separators=(",", ":"))
        spec = self._spec(call_dir, command, environ, cwd, timeout_s, installed, state)
        output = await self._execute(call_dir, context_text, spec, timeout_s)
        return self._record(
            sequence, package, primitive, command, environ, context_text, spec, installed, output
        )

    def _spec(
        self,
        call_dir: str,
        command: list[str],
        environ: Mapping[str, str],
        cwd: str,
        timeout_s: float,
        installed: Sequence[ToolTree],
        state: str | None,
    ) -> dict[str, Any]:
        policy = self.policy
        trees = [
            {"root": t.root, "digest": t.digest, "limits": {**TREE_LIMITS, "owner": policy.owner}}
            for t in installed
        ]
        return build_spec(
            call_dir=call_dir,
            argv=command,
            env=dict(environ),
            cwd=cwd,
            timeout_s=timeout_s,
            uid=policy.uid,
            gid=policy.gid,
            seccomp=policy.seccomp,
            trees=trees,
            state=state,
            state_limits=STATE_LIMITS,
        )

    async def _execute(
        self, call_dir: str, context_text: str, spec: Mapping[str, Any], timeout_s: float
    ) -> ExecResult:
        environment = self.environment
        try:
            await _root(environment, f"mkdir -m 0755 {_q(call_dir)}")
            await _upload_bytes(environment, context_text.encode(), f"{call_dir}/context.json")
            await _upload_bytes(environment, json.dumps(spec).encode(), f"{call_dir}/spec.json")
            output = await trusted_exec(
                environment,
                _launcher_command("supervise", f"{call_dir}/spec.json"),
                user="root",
                timeout_sec=timeout_s + SUPERVISOR_SLACK_S,
            )
            if output.return_code != 0:
                cleanup = f"rm -rf {_q(call_dir)}"
                await trusted_exec(environment, cleanup, user="root", timeout_sec=60)
            return output
        except BaseException:
            await asyncio.shield(
                trusted_exec(environment, f"rm -rf {_q(call_dir)}", user="root", timeout_sec=60)
            )
            raise

    def _record(
        self,
        sequence: int,
        package: str,
        primitive: str,
        command: list[str],
        environ: Mapping[str, str],
        context_text: str,
        spec: Mapping[str, Any],
        installed: Sequence[ToolTree],
        output: ExecResult,
    ) -> CallResult:
        result = _parse_result(output.stdout or "")
        stdout, stderr = _decoded(result, "stdout"), _decoded(result, "stderr")
        problems = _problems(result, stdout, stderr)
        facts = result or {}
        bounds = facts.get("bounds_violation")
        record: dict[str, Any] = {
            "schema": "call/1",
            "package": package,
            "primitive": primitive,
            "sequence": sequence,
            "recorded_at": datetime.now(UTC).isoformat(),
            "argv": command,
            "user": f"{spec['uid']}:{spec['gid']}",
            "cwd": spec["cwd"],
            "timeout_s": spec["timeout_s"],
            "env_names": sorted(environ),
            "env_sha256": hashlib.sha256(json.dumps(environ, sort_keys=True).encode()).hexdigest(),
            "context_sha256": hashlib.sha256(context_text.encode()).hexdigest(),
            "installed": [{"reference": t.reference, "digest": t.digest} for t in installed],
            "state": spec["state"],
            "supervisor_exit": output.return_code,
            "ran": bool(facts.get("ran")),
            "exit_code": facts.get("exit_code"),
            "signal": facts.get("signal"),
            "timed_out": bool(facts.get("timed_out")),
            "overflow": bool(facts.get("overflow")),
            "duration_s": facts.get("duration_s"),
            "descendants_killed": facts.get("descendants_killed", 0),
            "stdout_sha256": facts.get("stdout_sha256"),
            "stdout_bytes": facts.get("stdout_bytes"),
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr_sha256": facts.get("stderr_sha256"),
            "stderr_bytes": facts.get("stderr_bytes"),
            "stderr_tail": stderr.decode("utf-8", errors="replace"),
            "tool_before": facts.get("tool_before", []),
            "tool_after": facts.get("tool_after", []),
            "bounds_violation": bounds,
            "integrity_error": "; ".join(problems) or None,
        }
        path = self.logs_dir / "calls" / f"{sequence:04d}-{_safe(package)}-{_safe(primitive)}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return CallResult(
            record=record,
            stdout=stdout,
            stderr=stderr,
            exit_code=record["exit_code"],
            signal=record["signal"],
            timed_out=record["timed_out"],
            overflow=record["overflow"],
            integrity_error=record["integrity_error"],
            bounds_violation=bounds,
            record_path=path,
            ran=record["ran"],
        )
