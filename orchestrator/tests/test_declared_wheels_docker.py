"""Declared-wheels security and fairness proofs on the real Docker target; no models."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import socket
import subprocess
import tarfile
import urllib.error
import uuid
import zipfile
from pathlib import Path

import pytest

from raidar.contract import wheel_cache
from raidar.design.problem.models import ProblemId
from raidar.design.problem.store import open_store
from raidar.engine.trial.workspace import seed_locks, workspace_seed
from raidar.grading.closure import build_context
from raidar.grading.dependency_pins import lock_sha256, set_sha256
from raidar.grading.grader import grade_step
from raidar.grading.grader_image import ensure_grader_image
from raidar.grading.provision import prepare_dependencies
from raidar.grading.resolver import UvResolver, pylock_packages
from raidar.grading.sandbox import DockerCopySandbox, SandboxRun, sandbox_env
from raidar.grading.submission_deps import SubmissionDependencies, python_path_entries
from raidar.subject.environment.python.image import image_tag
from raidar.subject.environment.python.pack_definition import load_pack_spec
from tests.docker_trial_support import docker_context

pytestmark = [pytest.mark.docker, pytest.mark.security]


@pytest.fixture
def ready_target() -> str:
    return docker_context()


async def test_grader_executes_hidden_tests_only_offline_without_privileges(
    tmp_path: Path, ready_target: str
) -> None:
    app, tests, out = (tmp_path / name for name in ("app", "tests", "out"))
    for directory in (app, tests, out):
        directory.mkdir()
    (tests / "hidden-sentinel.txt").write_text("only-the-offline-grader")
    probe = (
        "import json,os,pathlib,socket; "
        "status=dict(line.split(':',1) for line in pathlib.Path('/proc/self/status')"
        ".read_text().splitlines() if ':' in line); "
        "sock=socket.socket(); sock.settimeout(1); "
        "print(json.dumps(dict(uid=os.getuid(),gid=os.getgid(),"
        "capabilities=status['CapEff'].strip(),"
        "no_new_privileges=status['NoNewPrivs'].strip(),"
        "interfaces=sorted(p.name for p in pathlib.Path('/sys/class/net').iterdir()),"
        "hidden=pathlib.Path('/tests/hidden-sentinel.txt').read_text(),"
        "network_result=sock.connect_ex(('1.1.1.1',443)))))"
    )
    sandbox = DockerCopySandbox(image=image_tag(load_pack_spec()), docker_context=ready_target)
    result = await sandbox.run(
        SandboxRun(
            app_dir=app,
            tests_dir=tests,
            out_dir=out,
            argv=("/usr/local/bin/python3", "-P", "-c", probe),
            env={},
            timeout_s=30,
            cpus=1,
            memory_mb=256,
        )
    )
    assert result.exit_code == 0, result.output
    evidence = json.loads(next(line for line in result.output.splitlines() if line.startswith("{")))
    assert evidence["uid"] == evidence["gid"] == 1000
    assert int(evidence["capabilities"], 16) == 0
    assert evidence["no_new_privileges"] == "1"
    assert evidence["interfaces"] == ["lo"]
    assert evidence["network_result"] != 0
    assert evidence["hidden"] == "only-the-offline-grader"


def _canary_wheel(directory: Path) -> None:
    metadata = "raidar_canary-1.0.dist-info"
    with zipfile.ZipFile(directory / "raidar_canary-1.0-py3-none-any.whl", "w") as wheel:
        wheel.writestr(
            f"{metadata}/METADATA",
            "Metadata-Version: 2.3\nName: raidar-canary\nVersion: 1.0\n",
        )
        wheel.writestr(
            f"{metadata}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: raidar-security-fixture\nRoot-Is-Purelib: true\n"
            "Tag: py3-none-any\n",
        )
        wheel.writestr(f"{metadata}/RECORD", "")


async def test_planted_sdist_never_executes_during_resolution(tmp_path: Path) -> None:
    marker = tmp_path / "setup-executed"
    _canary_wheel(tmp_path)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(0.05)
        port = listener.getsockname()[1]
        setup = (
            "from pathlib import Path\nimport socket\n"
            f"Path({str(marker)!r}).write_text('executed')\n"
            f"socket.create_connection(('127.0.0.1', {port}), timeout=1)\n"
            "from setuptools import setup\n"
            "setup(name='raidar-planted-sdist', version='1.0')\n"
        )
        files = {
            "setup.py": setup,
            "PKG-INFO": "Metadata-Version: 2.3\nName: raidar-planted-sdist\nVersion: 1.0\n",
        }
        with tarfile.open(tmp_path / "raidar-planted-sdist-1.0.tar.gz", "w:gz") as archive:
            for name, content in files.items():
                payload = content.encode()
                member = tarfile.TarInfo(f"raidar-planted-sdist-1.0/{name}")
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
        resolver = UvResolver(
            compile_args=("--no-index", "--find-links", str(tmp_path)),
            canary="raidar-canary==1.0",
        )
        result = await asyncio.to_thread(
            resolver,
            ("raidar-planted-sdist==1.0",),
            arch="arm64",
            exclude_newer="2026-10-27T00:00:00Z",
        )
        assert [(item.requirement, item.reason) for item in result.unprovided] == [
            ("raidar-planted-sdist==1.0", "no-wheel")
        ]
        assert not marker.exists()
        with pytest.raises(TimeoutError):
            listener.accept()


def test_wheel_build_context_excludes_hidden_tests_and_submission_code(tmp_path: Path) -> None:
    acquisition = tmp_path / "acquisition"
    acquisition.mkdir()
    _canary_wheel(acquisition)
    wheel = acquisition / "raidar_canary-1.0-py3-none-any.whl"
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    lock = (
        'lock-version = "1.0"\n[[packages]]\nname = "raidar-canary"\nversion = "1.0"\n'
        f'[[packages.wheels]]\nurl = "{wheel.as_uri()}"\n'
        f'[packages.wheels.hashes]\nsha256 = "{digest}"\n'
    )
    (acquisition / "hidden_test.py").write_text("raise AssertionError('hidden')")
    (acquisition / "setup.py").write_text("raise AssertionError('submission')")
    (acquisition / "tests").mkdir()
    (acquisition / "tests" / "nested.py").write_text("hidden")
    context = tmp_path / "context"
    build_context(lock, acquisition, context)
    assert sorted(path.relative_to(context).as_posix() for path in context.rglob("*")) == [
        "requirements.txt",
        "wheels",
        "wheels/raidar_canary-1.0-py3-none-any.whl",
    ]
    assert (context / "wheels" / wheel.name).read_bytes() == wheel.read_bytes()


@pytest.mark.parametrize(
    ("problem_name", "package", "probe"),
    [
        ("datagate", "pandas", "import pandas; print(pandas.DataFrame({'x':[2,3]}).x.sum())"),
        ("datagate", "polars", "import polars; print(polars.DataFrame({'x':[2,3]}).sum().item())"),
        (
            "mocked_http",
            "protobuf",
            "from google.protobuf.struct_pb2 import Struct; "
            "value=Struct(); value.update({'x':5}); print(int(value['x']))",
        ),
    ],
)
async def test_alternative_declared_libraries_and_protobuf_work_offline(
    tmp_path: Path, ready_target: str, problem_name: str, package: str, probe: str
) -> None:
    problem = open_store().load(ProblemId.parse(f"scb/{problem_name}@38d627ecf668"))
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "requirements.txt").write_text(f"{package}\n")
    base = await ensure_grader_image(problem.bundle, docker_context=ready_target)
    sandbox = DockerCopySandbox(image=base.tag, docker_context=ready_target)
    dependencies = await prepare_dependencies(problem.bundle, snapshot, sandbox)
    assert dependencies.unprovided == ()
    assert any(pin.startswith(f"{package}==") for pin in dependencies.provided)
    assert dependencies.image_id.startswith("sha256:")
    tests, out = tmp_path / "tests", tmp_path / "out"
    tests.mkdir()
    out.mkdir()
    installed = DockerCopySandbox(image=dependencies.image_id, docker_context=ready_target)
    result = await installed.run(
        SandboxRun(
            app_dir=snapshot,
            tests_dir=tests,
            out_dir=out,
            argv=("/usr/local/bin/python3", "-P", "-c", probe),
            env=sandbox_env(
                problem.bundle,
                installed.layout,
                coverage=False,
                python_path=python_path_entries(dependencies),
            ),
            timeout_s=60,
            cpus=1,
            memory_mb=1024,
        )
    )
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == "5", result.output


async def test_regrade_with_empty_wheel_cache_and_blocked_pypi_is_infrastructure_failure(
    tmp_path: Path, ready_target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    problem = open_store().load(ProblemId.parse("scb/forge@38d627ecf668"))
    records = problem.bundle.dependencies
    assert records is not None
    assert records.pins.seed is not None
    base = await ensure_grader_image(problem.bundle, docker_context=ready_target)
    original = DockerCopySandbox(image=base.tag, docker_context=ready_target)
    arch = await original.target_arch()
    key = records.pins.seed
    requirements = records.pins.sets[key].requirements
    lock = records.locks.read(key, arch, records.pins.sets[key].lock(arch))
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "requirements.txt").write_text("\n".join(requirements) + "\n")
    recorded = SubmissionDependencies(
        declared=requirements,
        set_sha256=set_sha256(requirements),
        source="pin",
        lock_text=lock,
        lock_sha256=lock_sha256(lock),
        arch=arch,
    )
    empty_cache = tmp_path / "wiped-wheel-cache"
    empty_cache.mkdir()
    monkeypatch.setattr(wheel_cache, "default_cache_root", lambda: empty_cache)

    def blocked(url: str):
        raise urllib.error.URLError("PyPI blocked for outage acceptance")

    def never_resolve(*args, **kwargs):
        pytest.fail("a regrade must not resolve its recorded lock")

    monkeypatch.setattr(wheel_cache, "open_wheel", blocked)
    # A unique base alias ensures this proof cannot be satisfied by a cached image layer.
    alias = f"raidar-outage-proof:{uuid.uuid4().hex[:12]}"
    docker = ["docker", "--context", ready_target]
    subprocess.run([*docker, "tag", base.tag, alias], check=True, capture_output=True)
    try:
        outcome = await grade_step(
            problem.bundle,
            problem.manifest.steps[0].name,
            snapshot,
            DockerCopySandbox(image=alias, docker_context=ready_target),
            tmp_path / "regrade",
            coverage=False,
            resolver=never_resolve,
            recorded=recorded,
        )
        assert outcome.grade.infra_failure is not None
        assert outcome.grade.infra_failure.code == "dependency_resolver_unavailable"
        assert outcome.grade.cases == ()
    finally:
        subprocess.run([*docker, "image", "rm", alias], check=True, capture_output=True)


def _docker(context: str, *args: str, timeout: int = 240) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "--context", context, *args], text=True, capture_output=True, timeout=timeout
    )


def _docker_ok(context: str, *args: str) -> str:
    done = _docker(context, *args)
    assert done.returncode == 0, f"docker {args[0]} failed: {done.stdout}\n{done.stderr}"
    return done.stdout


def test_the_seed_installs_the_pinned_dependency_under_the_pinned_cutoff(
    tmp_path: Path, ready_target: str
) -> None:
    problem = open_store().load(ProblemId.parse("scb/forge@38d627ecf668"))
    records = problem.bundle.dependencies
    assert records is not None
    pins = records.pins
    assert pins.seed is not None
    locks = seed_locks(pins, records.locks)
    expected = dict(pylock_packages(locks["amd64"]))["pyyaml"]
    seed = workspace_seed(
        str(problem.manifest.ref),
        problem.bundle.manifest.grader.test_dependencies,
        load_pack_spec().python,
        pins.exclude_newer,
        locks,
    )
    payload = tmp_path / "seed"
    payload.mkdir()
    for filename, item in seed.payload.items():
        (payload / filename).write_bytes(item.content)
    name = f"raidar-seed-proof-{uuid.uuid4().hex[:12]}"
    image = image_tag(load_pack_spec())
    try:
        _docker_ok(ready_target, "create", "--name", name, image, "sleep", "infinity")
        _docker_ok(ready_target, "start", name)
        _docker_ok(ready_target, "cp", str(payload), f"{name}:/tmp/seed")
        _docker_ok(
            ready_target,
            "exec",
            "-e",
            "RAIDAR_AGENT_USER=agent",
            "-e",
            "RAIDAR_PAYLOAD_DIR=/tmp/seed",
            name,
            "sh",
            "-c",
            seed.install,
        )
        checked = _docker_ok(ready_target, "exec", name, "sh", "-c", seed.check)
        assert "pip_cutoff=enforced" in checked
        as_agent = ("exec", name, "runuser", "-u", "agent", "--")
        version = _docker_ok(
            ready_target,
            *as_agent,
            "sh",
            "-c",
            "cd /app && .venv/bin/python -c 'import yaml; print(yaml.__version__)'",
        )
        assert version.strip() == expected
        configured = _docker_ok(
            ready_target, *as_agent, "pip", "config", "get", "global.uploaded-prior-to"
        )
        assert configured.strip() == pins.exclude_newer
        _docker_ok(ready_target, *as_agent, "pip", "install", "--dry-run", "requests==2.32.5")
        # pip's own cutoff enforcement refuses a wheel released after the cutoff.
        refused = _docker(
            ready_target,
            *as_agent,
            "pip",
            "install",
            "--dry-run",
            "--uploaded-prior-to",
            "2020-01-01T00:00:00Z",
            "requests==2.32.5",
            timeout=120,
        )
        assert refused.returncode != 0
    finally:
        _docker(ready_target, "rm", "-f", name)
