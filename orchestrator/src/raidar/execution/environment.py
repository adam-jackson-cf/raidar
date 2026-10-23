"""Harbor's Docker environment with a run-scoped Compose project name and a fast exec path.

**Run scope.** Harbor names a trial's Compose project ``<trial_name>__env``.
Raidar's trial names are plan trial ids (``b00-p0-baseline``), which repeat
across jobs and experiments, so two runs on one Docker host (two pilot legs,
or a replacement run beside the original) shared a Compose project: the second
``compose up`` recreated the first job's container, which exited 143 and was
recorded as an infra error. The scope suffix makes the project unique per
orchestrator host and run directory (the hash covers the hostname and the
resolved trials path) while the trial directory stays ``trials/<trial id>``
and no plan hash changes.

**Engine fast path (N1).** Harbor routes every main-container ``exec`` and
``cp`` through ``docker compose``, which loads the project's compose files on
every call before the command even starts. The budgeted gaps of each step make
about a dozen such calls: the workdir upload, ``pwd``, ``setup.sh``, the leak
scan, and a four-call artifact collection (``test -d``, ``tar czf`` to a temp file,
``compose cp``, ``rm``).
Once the main container is known, this class sends the same ``exec``/``cp``
to ``docker`` directly (same flags, user, workdir, env, output and exit
status), and collects an excluded directory with one streamed ``tar cf -``
(no gzip, no archive left in the container), extracted with Harbor's exclude
patterns and ``tarfile`` ``data`` filter. Anything it cannot translate, or a
main container it cannot resolve, takes Harbor's compose path unchanged.

**Data path.** Nothing is bind-mounted: ``capabilities.mounted`` is False, the
mounts compose file declares no volumes, and Harbor copies ``/logs/agent`` and
``/logs/verifier`` out of the container after each phase. A Docker daemon on
another host therefore sees the same flow as a local one.

**Host edits survive the log download.** A component may download a stage's logs
mid-run and then rewrite them on the host (a root ``trajectory.json``, a usage
update). Harbor's final download of ``/logs/agent`` would overwrite those files
with the container's older copies, which a bind mount never did (host and
container were one file). The final download therefore keeps every host file
that is not byte-for-byte what an earlier download left there (``_synced``).

**Per-trial installs at start.** ``add_post_start_hook`` registers work that
must run once per trial in the started container before any step (a
component's tool install, ``raidar.components.runtime.provision_v2``). It runs inside
``start``, so its time is the trial's ``environment_start`` (a per-trial
cost), never a step's ``per_step_reset``. A hook records its own failure; it
must not raise.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib
import shutil
import socket
import sys
import tarfile
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from harbor.constants import MAIN_SERVICE_NAME
from harbor.environments.base import ExecResult
from harbor.environments.capabilities import EnvironmentCapabilities
from harbor.environments.docker import write_mounts_compose_file
from harbor.environments.docker.docker import (
    DockerEnvironment,
    _sanitize_docker_compose_project_name,
)
from harbor.models.trial.paths import EnvironmentPaths

RUN_SCOPED_DOCKER_IMPORT_PATH = "raidar.execution.environment:RunScopedDockerEnvironment"
SCOPE_KWARG = "run_scope"
# Harbor's own bound for packing a transfer archive (base._TRANSFER_PACK_TIMEOUT_SEC).
TRANSFER_TIMEOUT_S = 600
_EXEC_VALUE_FLAGS = frozenset({"-w", "-e", "-u"})
_MAIN_PREFIX = f"{MAIN_SERVICE_NAME}:"
# Imported lazily by Harbor's installed agents (codex, claude-code) to price a
# step, i.e. inside the first step's post-agent gap (about 1.6-2 s, once per
# process). Imported while the environment starts instead.
LAZY_AGENT_IMPORTS: tuple[str, ...] = ("litellm",)


def run_scope(trials_dir: Path) -> str:
    """A short, stable token for one job directory on this host.

    The hostname is part of the hash: two orchestrator hosts sharing one Docker
    daemon can run jobs with the same trials path without sharing a project.
    """
    identity = f"{socket.gethostname()}:{trials_dir.resolve()}"
    return hashlib.sha256(identity.encode()).hexdigest()[:10]


def trial_project_name(trial_id: str, trials_dir: Path) -> str:
    """The Compose project Harbor names for a trial run under ``trials_dir``."""
    return _sanitize_docker_compose_project_name(f"{trial_id}__env-{run_scope(trials_dir)}")


def _engine_cp(rest: Sequence[str], container: str) -> list[str] | None:
    ends = [arg.startswith(_MAIN_PREFIX) for arg in rest]
    if len(rest) != 2 or sum(ends) != 1:
        return None
    paths = [
        f"{container}:{arg.removeprefix(_MAIN_PREFIX)}" if end else arg
        for arg, end in zip(rest, ends, strict=True)
    ]
    return ["cp", *paths]


def _engine_exec(rest: Sequence[str], container: str, *, stdin: bool) -> list[str] | None:
    flags: list[str] = ["-i"] if stdin else []
    index = 0
    while index < len(rest):
        arg = rest[index]
        if arg == "-T":
            index += 1
        elif arg in _EXEC_VALUE_FLAGS and index + 1 < len(rest):
            flags += [arg, rest[index + 1]]
            index += 2
        elif arg == MAIN_SERVICE_NAME and index + 1 < len(rest):
            return ["exec", *flags, container, *rest[index + 1 :]]
        else:
            return None
    return None


def engine_command(compose_args: Sequence[str], container: str, *, stdin: bool) -> list[str] | None:
    """The ``docker`` argv equivalent to a main-service compose ``exec``/``cp``, or None.

    Only the shapes Harbor emits for the main service are translated: ``exec``
    with ``-T``/``-w``/``-e``/``-u`` then ``main``, and ``cp`` with exactly one
    ``main:`` endpoint. No TTY is the engine default (compose's ``-T``); ``-i``
    is added only when the caller streams stdin, which compose attaches.
    """
    if not compose_args:
        return None
    verb, rest = compose_args[0], compose_args[1:]
    if verb == "cp":
        return _engine_cp(rest, container)
    if verb == "exec":
        return _engine_exec(rest, container, stdin=stdin)
    return None


def _signature(path: Path) -> tuple[int, int]:
    """(mtime_ns, size) of ``path`` itself, a symlink not followed."""
    info = path.lstat()
    return info.st_mtime_ns, info.st_size


async def warm_agent_imports() -> None:
    """Import the agents' lazy modules off the event loop (once per process)."""
    for name in LAZY_AGENT_IMPORTS:
        if name not in sys.modules:
            with contextlib.suppress(ImportError):
                await asyncio.to_thread(importlib.import_module, name)


PostStartHook = Callable[[], Awaitable[None]]


class RunScopedDockerEnvironment(DockerEnvironment):
    def __init__(self, *args: Any, session_id: str, run_scope: str, **kwargs: Any) -> None:
        super().__init__(*args, session_id=f"{session_id}-{run_scope}", **kwargs)
        self.run_scope = run_scope
        self._synced: dict[Path, tuple[int, int]] = {}
        self._main_container: str | None = None
        self._post_start: list[PostStartHook] = []

    @property
    def capabilities(self) -> EnvironmentCapabilities:
        """Not mounted: Harbor then downloads ``/logs/agent`` and ``/logs/verifier`` itself."""
        return super().capabilities.model_copy(update={"mounted": False})

    def _write_mounts_compose_file(self) -> Path:
        """Declare no volumes; Harbor's ``ensure_dirs`` creates the in-container log dirs."""
        self._cleanup_mounts_compose_file()
        self._mounts_compose_temp_dir = tempfile.TemporaryDirectory()
        path = Path(self._mounts_compose_temp_dir.name) / "docker-compose-mounts.json"
        return write_mounts_compose_file(path, [])

    def add_post_start_hook(self, hook: PostStartHook) -> None:
        """Run ``hook`` once the container has started, inside ``start`` (module doc)."""
        self._post_start.append(hook)

    async def _resolve_main_container(self) -> str | None:
        """The running main container's id for this project (cached), or None."""
        if self._main_container is not None:
            return self._main_container
        if self._is_windows_container:
            return None
        project = _sanitize_docker_compose_project_name(self.session_id)
        process = await asyncio.create_subprocess_exec(
            *self._engine_cmd(
                "ps",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--filter",
                f"label=com.docker.compose.service={MAIN_SERVICE_NAME}",
            ),
            env=self._compose_env_vars(include_os_env=True),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await process.communicate()
        ids = stdout.decode(errors="replace").split()
        if process.returncode != 0 or len(ids) != 1:
            return None
        self._main_container = ids[0]
        return self._main_container

    async def start(self, force_build: bool) -> None:
        """Start, importing an installed agent's lazy modules alongside ``compose up``.

        An installed agent first imports LiteLLM inside the first step's artifact
        gap; it is done here, concurrently with ``compose up``.
        """
        self._main_container = None
        await asyncio.gather(super().start(force_build), warm_agent_imports())
        for hook in self._post_start:
            await hook()

    async def stop(self, delete: bool) -> None:
        try:
            await super().stop(delete)
        finally:
            self._main_container = None

    async def stop_service(self, service: str) -> None:
        if service == MAIN_SERVICE_NAME:
            self._main_container = None
        await super().stop_service(service)

    async def _run_docker_compose_command(
        self,
        command: list[str],
        check: bool = True,
        timeout_sec: int | None = None,
        stdin_data: bytes | None = None,
        on_output: Any = None,
    ) -> ExecResult:
        translated = None
        if command and command[0] in ("exec", "cp"):
            container = await self._resolve_main_container()
            if container is not None:
                translated = engine_command(command, container, stdin=stdin_data is not None)
        if translated is None:
            return await super()._run_docker_compose_command(
                command,
                check=check,
                timeout_sec=timeout_sec,
                stdin_data=stdin_data,
                on_output=on_output,
            )
        return await self._run_engine_command(
            translated,
            check=check,
            timeout_sec=timeout_sec,
            stdin_data=stdin_data,
            on_output=on_output,
        )

    async def _run_engine_command(
        self,
        argv: list[str],
        *,
        check: bool,
        timeout_sec: int | None,
        stdin_data: bytes | None,
        on_output: Any,
    ) -> ExecResult:
        """``docker <argv>`` with Harbor's output handling and failure contract."""
        full_command = self._engine_cmd(*argv)
        process = await asyncio.create_subprocess_exec(
            *full_command,
            env=self._compose_env_vars(include_os_env=True),
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        if on_output is not None:
            result = await self._collect_streamed_output(
                process, timeout_sec=timeout_sec, stdin_data=stdin_data, on_output=on_output
            )
        else:
            result = await self._collect_buffered_output(
                process, timeout_sec=timeout_sec, stdin_data=stdin_data
            )
        if check and result.return_code != 0:
            raise RuntimeError(
                f"Docker command failed for environment {self.environment_name}. "
                f"Command: {' '.join(full_command)}. "
                f"Return code: {result.return_code}. "
                f"Stdout: {result.stdout}. "
                f"Stderr: {result.stderr}. "
            )
        return result

    async def download_dir_with_exclusions(
        self, *, source_dir: str, target_dir: Path | str, exclude: list[str]
    ) -> None:
        """One streamed ``tar cf -`` from the main container, extracted with Harbor's filter."""
        container = await self._resolve_main_container()
        if container is None:
            await super().download_dir_with_exclusions(
                source_dir=source_dir, target_dir=target_dir, exclude=exclude
            )
            return
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        argv = self._engine_cmd(
            "exec",
            "-u",
            "root",
            container,
            "tar",
            "cf",
            "-",
            *(f"--exclude={pattern}" for pattern in exclude),
            "-C",
            source_dir,
            ".",
        )
        with tempfile.TemporaryDirectory() as host_tmp:
            archive = Path(host_tmp) / "transfer.tar"
            with archive.open("wb") as sink:
                process = await asyncio.create_subprocess_exec(
                    *argv,
                    env=self._compose_env_vars(include_os_env=True),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=sink,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    _, stderr = await asyncio.wait_for(
                        process.communicate(), timeout=TRANSFER_TIMEOUT_S
                    )
                except TimeoutError:
                    await self._terminate_process(process)
                    raise RuntimeError(
                        f"Transfer of {source_dir!r} timed out after {TRANSFER_TIMEOUT_S} s"
                    ) from None
            if process.returncode != 0:
                output = stderr.decode(errors="replace") if stderr else "no output"
                raise RuntimeError(
                    f"Failed to stream {source_dir!r} with code {process.returncode}: {output}"
                )
            with tarfile.open(archive, "r:") as bundle:
                bundle.extractall(path=target, filter="data")

    async def download_dir(self, source_dir: str, target_dir: Path | str) -> None:
        """Harbor's directory download; the whole agent log directory keeps host edits."""
        target = Path(target_dir)
        if PurePosixPath(source_dir) == EnvironmentPaths.agent_dir:
            with tempfile.TemporaryDirectory() as staging:
                await super().download_dir(source_dir, staging)
                await asyncio.to_thread(self._merge_keeping_host_edits, Path(staging), target)
            return
        await super().download_dir(source_dir, target)
        await asyncio.to_thread(self._record_synced, target)

    def _merge_keeping_host_edits(self, staged: Path, target: Path) -> None:
        for source in sorted(staged.rglob("*")):
            destination = target / source.relative_to(staged)
            if source.is_dir() and not source.is_symlink():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            if self._edited_on_host(destination):
                continue
            if destination.is_symlink() or destination.exists():
                destination.unlink()
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(source, destination)
            self._record_synced(destination)

    def _edited_on_host(self, path: Path) -> bool:
        if not (path.exists() or path.is_symlink()):
            return False
        return self._synced.get(path) != _signature(path)

    def _record_synced(self, target: Path) -> None:
        files = [target] if target.is_file() or target.is_symlink() else target.rglob("*")
        for path in files:
            if path.is_file() or path.is_symlink():
                self._synced[path] = _signature(path)

    async def prepare_logs_for_host(self) -> None:
        """No-op: nothing is bind-mounted, so there is no host-owned log directory to fix."""
