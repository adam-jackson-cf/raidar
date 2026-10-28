"""Declared sets, closure images, regrades and outages under ``declared-wheels/1``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from raidar.contract import wheel_cache
from raidar.contract.wheels import DependencyInfraError
from raidar.grading import closure
from raidar.grading.bundle import (
    BundleError,
    BundleManifest,
    GraderSpec,
    VerifierBundle,
    VerifierStepSpec,
    load_bundle,
    write_bundle,
    write_bundle_dependencies,
)
from raidar.grading.closure import ClosureImage, closure_dockerfile, closure_tag
from raidar.grading.dependency_pins import lock_sha256, set_sha256
from raidar.grading.grader import grade_step
from raidar.grading.grader_image import ensure_grader_image, grader_dockerfile, grader_image_tag
from raidar.grading.provision import prepare_dependencies
from raidar.grading.resolver import resolve_pins
from raidar.grading.sandbox import RUNTIME_DIR, DockerCopySandbox, SandboxRun
from raidar.grading.submission_deps import (
    SubmissionDependencies,
    closure_dir,
    declared_requirements,
    python_path_entries,
    unreadable,
)
from tests.dependency_support import CUTOFF, FakeResolver, make_wheel, pinned_bundle
from tests.verifier_support import LocalProcessSandbox, make_bundle

TEST = """
import subprocess


def test_reads_config(entrypoint_argv):
    result = subprocess.run(entrypoint_argv, capture_output=True, text=True)
    assert result.stdout.strip() == "parsed", result.stderr
"""
CONFTEST = """
import shlex

import pytest


def pytest_addoption(parser):
    parser.addoption("--entrypoint", action="store", required=True)
    parser.addoption("--checkpoint", action="store", required=True)


@pytest.fixture(scope="session")
def entrypoint_argv(request):
    return shlex.split(request.config.getoption("--entrypoint"))
"""


@pytest.fixture(autouse=True)
def local_wheel_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wheel_cache, "WHEEL_URL_PREFIXES", ("file://",))


def _snapshot(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (root / name).write_text(text)
    return root


def _declaring(root: Path, *requirements: str) -> Path:
    listed = ", ".join(f'"{item}"' for item in requirements)
    return _snapshot(
        root,
        {
            "app.py": "print('parsed')\n",
            "pyproject.toml": f'[project]\nname = "app"\ndependencies = [{listed}]\n',
        },
    )


# ------------------------------------------------------------------ declaration
def test_declared_requirements_come_from_pyproject_and_requirements_txt(tmp_path: Path) -> None:
    snapshot = _snapshot(
        tmp_path / "app",
        {
            "pyproject.toml": '[project]\nname = "x"\ndependencies = ["PyYAML>=6.0", "requests"]\n',
            "requirements.txt": (
                "# pins\n--index-url https://evil.example/simple\n-i=https://evil.example\n"
                "pyyaml==5.1  # old\n-r other.txt\n-e .\nnot a requirement!!\n"
            ),
        },
    )
    assert declared_requirements(snapshot) == (
        "PyYAML>=6.0",
        "requests",
        "pyyaml==5.1",
        "-r other.txt",
        "-e .",
        "not a requirement!!",
    )


def test_a_declaration_that_cannot_be_read_is_never_an_empty_set(tmp_path: Path) -> None:
    bare = _snapshot(tmp_path / "bare", {"app.py": ""})
    assert declared_requirements(bare) == ()
    no_list = _snapshot(tmp_path / "nolist", {"pyproject.toml": '[project]\nname = "x"\n'})
    assert declared_requirements(no_list) == ()
    broken = _snapshot(tmp_path / "broken", {"pyproject.toml": "[project\n"})
    assert declared_requirements(broken) == (unreadable("pyproject.toml"),)
    not_a_list = _snapshot(
        tmp_path / "notalist", {"pyproject.toml": '[project]\nname = "x"\ndependencies = "flask"\n'}
    )
    assert declared_requirements(not_a_list) == (unreadable("pyproject.toml"),)
    odd_item = _snapshot(
        tmp_path / "odd", {"pyproject.toml": '[project]\nname = "x"\ndependencies = ["a", 3]\n'}
    )
    assert declared_requirements(odd_item) == ("a", unreadable("pyproject.toml"))
    undecodable = tmp_path / "undecodable"
    undecodable.mkdir()
    (undecodable / "requirements.txt").write_bytes(b"\xff\xfe")
    (undecodable / "pyproject.toml").write_bytes(b"\xff\xfe")
    assert declared_requirements(undecodable) == (
        unreadable("pyproject.toml"),
        unreadable("requirements.txt"),
    )


def test_the_closure_lives_in_a_directory_named_for_its_lock() -> None:
    sha = "ab" * 32
    assert closure_dir(sha) == "/opt/raidar-submission-deps/abababababab"
    none = SubmissionDependencies()
    assert python_path_entries(none) == ()
    assert python_path_entries(SubmissionDependencies(lock_text="x", lock_sha256=sha)) == (
        closure_dir(sha),
    )


# ------------------------------------------------------------------- provision
async def test_nothing_declared_resolves_nothing_and_builds_no_closure(tmp_path: Path) -> None:
    bundle, fake = pinned_bundle(tmp_path / "p")
    calls = len(fake.calls)
    sandbox = LocalProcessSandbox(tmp_path / "sb")
    deps = await prepare_dependencies(
        bundle, _snapshot(tmp_path / "app", {"app.py": ""}), sandbox, resolver=fake
    )
    assert len(fake.calls) == calls
    assert sandbox.closures == []
    assert deps.declared == ()
    assert deps.lock_text == ""
    assert deps.arch == "amd64"
    assert deps.set_sha256 == set_sha256(())
    assert deps.image_id.startswith("local:")


async def test_a_set_the_pin_resolved_reuses_the_pins_lock(tmp_path: Path) -> None:
    bundle, fake = pinned_bundle(
        tmp_path / "p", reference_sets={"checkpoint_1": ["PyYAML>=6"], "checkpoint_2": []}
    )
    calls = len(fake.calls)
    sandbox = LocalProcessSandbox(tmp_path / "sb")
    deps = await prepare_dependencies(
        bundle, _declaring(tmp_path / "app", "PyYAML>=6"), sandbox, resolver=fake
    )
    assert len(fake.calls) == calls  # no resolution: the pin's lock
    assert deps.source == "pin"
    assert deps.provided == ("pyyaml==1.0",)
    assert not deps.unprovided
    assert deps.lock_sha256 == lock_sha256(deps.lock_text)
    assert deps.arch == "amd64"
    assert deps.image_id == "local-closure"
    assert sandbox.closures == [deps.lock_text]
    assert deps.declared == ("PyYAML>=6",)
    assert deps.set_sha256 == set_sha256(["PyYAML>=6"])


async def test_a_new_set_is_resolved_under_the_pins_cutoff_for_both_architectures(
    tmp_path: Path,
) -> None:
    bundle, fake = pinned_bundle(tmp_path / "p")
    fake.calls.clear()
    deps = await prepare_dependencies(
        bundle,
        _declaring(tmp_path / "app", "flask", "pandas"),
        LocalProcessSandbox(tmp_path / "sb"),
        resolver=fake,
    )
    assert deps.source == "resolved"
    assert deps.provided == ("flask==1.0", "pandas==1.0")
    assert sorted(call[1] for call in fake.calls) == ["amd64", "arm64"]
    assert {call[2] for call in fake.calls} == {CUTOFF}
    assert deps.arch_divergent is False
    assert deps.resolve_s > 0


async def test_architectures_that_resolve_differently_are_recorded_divergent(
    tmp_path: Path,
) -> None:
    bundle, fake = pinned_bundle(tmp_path / "p")
    fake.arch_versions = {"arm64": "2.0"}
    deps = await prepare_dependencies(
        bundle,
        _declaring(tmp_path / "app", "flask"),
        LocalProcessSandbox(tmp_path / "sb"),
        resolver=fake,
    )
    assert deps.arch == "amd64"
    assert deps.provided == ("flask==1.0",)
    assert deps.arch_divergent is True


async def test_refused_requirements_are_unprovided_and_install_nothing(tmp_path: Path) -> None:
    bundle, fake = pinned_bundle(tmp_path / "p")
    fake.unprovided = {"solo": "no-wheel"}
    sandbox = LocalProcessSandbox(tmp_path / "sb")
    deps = await prepare_dependencies(
        bundle, _declaring(tmp_path / "app", "solo"), sandbox, resolver=fake
    )
    assert [(u.requirement, u.reason) for u in deps.unprovided] == [("solo", "no-wheel")]
    assert deps.lock_text == ""
    assert deps.provided == ()
    assert sandbox.closures == []
    assert python_path_entries(deps) == ()


async def test_a_regrade_reinstalls_the_recorded_lock_and_never_resolves(
    tmp_path: Path,
) -> None:
    bundle, fake = pinned_bundle(tmp_path / "p")
    snapshot = _declaring(tmp_path / "app", "flask")
    first = await prepare_dependencies(
        bundle, snapshot, LocalProcessSandbox(tmp_path / "sb"), resolver=fake
    )
    fake.error = DependencyInfraError("pypi is down")
    fake.calls.clear()
    sandbox = LocalProcessSandbox(tmp_path / "sb2")
    again = await prepare_dependencies(
        bundle, snapshot, sandbox, resolver=fake, recorded=first.model_copy(update={"image_id": ""})
    )
    assert fake.calls == []
    assert sandbox.closures == [first.lock_text]
    assert again.lock_text == first.lock_text
    assert again.provided == first.provided
    tampered = first.model_copy(update={"lock_sha256": "0" * 64})
    with pytest.raises(BundleError, match="recorded dependency lock"):
        await prepare_dependencies(bundle, snapshot, sandbox, recorded=tampered)


async def test_a_resolver_unlike_the_pinned_one_is_infrastructure_failure_never_a_score(
    tmp_path: Path,
) -> None:
    bundle, pinned = pinned_bundle(tmp_path / "p")
    drifted = FakeResolver(identity=f"{pinned.identity}-drifted", unprovided={"flask": "no-wheel"})
    sandbox = LocalProcessSandbox(tmp_path / "sb")
    with pytest.raises(DependencyInfraError):
        await prepare_dependencies(
            bundle, _declaring(tmp_path / "app", "flask"), sandbox, resolver=drifted
        )
    assert drifted.calls == []
    assert sandbox.closures == []


async def test_a_bundle_without_a_pin_cannot_provision_a_declared_set(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path / "plain")
    with pytest.raises(BundleError, match="no dependency pin"):
        await prepare_dependencies(
            bundle,
            _declaring(tmp_path / "app", "flask"),
            LocalProcessSandbox(tmp_path / "sb"),
            resolver=FakeResolver(),
        )


def _grading_bundle(root: Path, resolver: FakeResolver) -> VerifierBundle:
    tests = _snapshot(root / "tests", {"conftest.py": CONFTEST, "test_checkpoint_1.py": TEST})
    bundle = write_bundle(
        root / "bundle",
        BundleManifest(
            problem="cfg",
            source={"format": "test-fixture"},
            env={},
            grader=GraderSpec(base_image="local"),
            steps=(
                VerifierStepSpec(
                    name="checkpoint_1",
                    checkpoint="checkpoint_1",
                    test_files=("test_checkpoint_1.py",),
                    pytest_args=(
                        "{tests}/test_checkpoint_1.py",
                        "--checkpoint",
                        "checkpoint_1",
                        "--entrypoint",
                        f"{sys.executable} {{app}}/app.py",
                    ),
                ),
            ),
        ),
        tests,
    )
    pins, locks = resolve_pins({"checkpoint_1": []}, [], exclude_newer=CUTOFF, resolver=resolver)
    return write_bundle_dependencies(bundle.root, pins, locks)


async def test_the_graded_program_runs_in_the_closure_image_with_the_closure_on_its_path(
    tmp_path: Path,
) -> None:
    fake = FakeResolver()
    bundle = _grading_bundle(tmp_path / "problem", fake)
    sandbox = LocalProcessSandbox(tmp_path / "sandbox")
    result = await grade_step(
        bundle,
        "checkpoint_1",
        _declaring(tmp_path / "app", "flask"),
        sandbox,
        tmp_path / "work",
        coverage=False,
        resolver=fake,
    )
    assert result.grade.solved is True
    [run] = sandbox.runs
    deps = result.grading.dependencies
    assert deps is not None
    assert deps.provided == ("flask==1.0",)
    assert run.image == "local-closure"
    entry = closure_dir(deps.lock_sha256)
    assert run.env["RAIDAR_SUBMISSION_DEPS"] == entry
    assert run.env["PYTHONPATH"].endswith(f":{entry}")
    assert run.env["UV_OFFLINE"] == "1"


async def test_a_resolver_outage_is_an_infrastructure_failure_never_a_score(
    tmp_path: Path,
) -> None:
    fake = FakeResolver()
    bundle = _grading_bundle(tmp_path / "problem", fake)
    fake.error = DependencyInfraError("pypi unreachable")
    sandbox = LocalProcessSandbox(tmp_path / "sandbox")
    result = await grade_step(
        bundle,
        "checkpoint_1",
        _declaring(tmp_path / "app", "flask"),
        sandbox,
        tmp_path / "work",
        coverage=True,
        resolver=fake,
    )
    failure = result.grade.infra_failure
    assert failure is not None
    assert failure.code == "dependency_resolver_unavailable"
    assert "pypi unreachable" in str(failure.detail)
    assert result.grade.cases == ()
    assert sandbox.runs == []
    assert result.parity is not None
    assert result.coverage is None


async def test_a_coverage_run_reuses_the_grading_runs_lock(tmp_path: Path) -> None:
    fake = FakeResolver()
    bundle = _grading_bundle(tmp_path / "problem", fake)
    sandbox = LocalProcessSandbox(tmp_path / "sandbox")
    fake.calls.clear()
    result = await grade_step(
        bundle,
        "checkpoint_1",
        _declaring(tmp_path / "app", "flask"),
        sandbox,
        tmp_path / "work",
        coverage=True,
        resolver=fake,
    )
    assert len(fake.calls) == 2  # both architectures, once
    assert result.coverage is not None
    assert len(sandbox.runs) == 2
    assert {run.image for run in sandbox.runs} == {"local-closure"}


# ----------------------------------------------------------------- bundle pins
def test_the_bundle_carries_its_pin_and_locks_and_they_change_its_digest(tmp_path: Path) -> None:
    plain = make_bundle(tmp_path / "p")
    before = plain.digest()
    assert plain.dependencies is None
    bundle, _ = pinned_bundle(tmp_path / "p", test_dependencies=["pyyaml"])
    assert bundle.digest() != before
    assert bundle.dependencies is not None
    pins = bundle.dependencies.pins
    assert pins.seed is not None
    assert pins.grader is not None
    assert bundle.dependencies.locks.read_pins(pins)
    reloaded = load_bundle(bundle.root)
    assert reloaded.dependencies is not None
    assert reloaded.dependencies.pins == pins
    (bundle.root / "deps" / "dependencies.json").write_text("{not json")
    with pytest.raises(BundleError, match="invalid"):
        load_bundle(bundle.root)


# ---------------------------------------------------------------- grader image
def test_the_grader_environment_is_installed_from_its_pinned_lock(tmp_path: Path) -> None:
    bundle, _ = pinned_bundle(tmp_path / "p", test_dependencies=["pyyaml"])
    text = grader_dockerfile(bundle)
    assert "RUN --network=none --mount=type=bind,target=/raidar-ctx" in text
    assert "--require-hashes --no-deps --only-binary :all:" in text
    assert "--offline --no-index --find-links /raidar-ctx/wheels" in text
    assert "uv venv --python /usr/local/bin/python3" in text
    assert "# raidar-grader-lock" in text
    assert "pytest" not in text  # tools come from the lock, not from the Dockerfile
    other, _ = pinned_bundle(
        tmp_path / "q", test_dependencies=["pyyaml"], resolver=FakeResolver(version="2.0")
    )
    assert grader_image_tag(other) != grader_image_tag(bundle)
    assert grader_image_tag(bundle) == grader_image_tag(bundle)


def test_a_grader_without_a_pin_cannot_be_named(tmp_path: Path) -> None:
    with pytest.raises(BundleError, match="re-import the problem"):
        grader_image_tag(make_bundle(tmp_path / "plain"))


class FakeDocker:
    """Stands in for the docker CLI: image presence, daemon architecture, builds."""

    def __init__(self, arch: str = "aarch64") -> None:
        self.arch = arch
        self.images: dict[str, str] = {}
        self.builds: list[dict[str, object]] = []

    async def __call__(
        self,
        docker_bin: str,
        docker_context: str,
        *args: str,
        stdin: bytes | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str]:
        if args[:2] == ("image", "inspect"):
            tag = args[-1]
            return (0, self.images[tag] + "\n") if tag in self.images else (1, "No such image")
        if args[0] == "info":
            return 0, self.arch + "\n"
        assert args[0] == "build"
        tag, context = args[2], Path(args[-1])
        self.builds.append(
            {
                "tag": tag,
                "dockerfile": (stdin or b"").decode(),
                "files": sorted(p.relative_to(context).as_posix() for p in context.rglob("*")),
                "env": env,
                "requirements": (context / "requirements.txt").read_text(),
            }
        )
        self.images[tag] = f"sha256:{tag.rsplit(':', 1)[1]}"
        return 0, ""


def _wheeled(tmp_path: Path, *names: str) -> dict[str, Path]:
    return {name: make_wheel(tmp_path / "wheels", name) for name in names}


async def test_the_grader_image_is_built_offline_from_cached_wheels_and_then_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docker = FakeDocker()
    monkeypatch.setattr(closure, "run_docker", docker)
    wheels = _wheeled(tmp_path, "pytest", "pytest-timeout", "coverage", "pyyaml")
    bundle, _ = pinned_bundle(
        tmp_path / "p", test_dependencies=["pyyaml"], resolver=FakeResolver(wheels=wheels)
    )
    cache = tmp_path / "cache"
    first = await ensure_grader_image(bundle, docker_context="mbp", cache_root=cache)
    assert first.built
    assert first.tag == grader_image_tag(bundle)
    [build] = docker.builds
    assert build["env"] == {"DOCKER_BUILDKIT": "1"}
    assert build["files"] == [
        "requirements.txt",
        "wheels",
        *(f"wheels/{w.name}" for w in sorted(wheels.values(), key=lambda p: p.name)),
    ]
    assert "--hash=sha256:" in str(build["requirements"])
    assert not any("tests" in str(name) for name in build["files"])  # type: ignore[union-attr]

    def blocked(url: str) -> None:
        raise OSError("network blocked")

    monkeypatch.setattr(wheel_cache, "open_wheel", blocked)
    warm = await ensure_grader_image(bundle, docker_context="mbp", cache_root=tmp_path / "none")
    assert not warm.built
    assert warm.tag == first.tag
    assert len(docker.builds) == 1


async def test_a_failed_image_build_is_reported_with_its_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing(*args: str, **kwargs: object) -> tuple[int, str]:
        if args[2] == "info":
            return 0, "x86_64"
        if args[2:4] == ("image", "inspect"):
            return 1, ""
        return 1, "boom: no space left"

    monkeypatch.setattr(closure, "run_docker", failing)
    wheels = _wheeled(tmp_path, "pytest", "pytest-timeout", "coverage")
    bundle, _ = pinned_bundle(tmp_path / "p", resolver=FakeResolver(wheels=wheels))
    with pytest.raises(RuntimeError, match="boom: no space left"):
        await ensure_grader_image(bundle, docker_context="mbp", cache_root=tmp_path / "c")


async def test_an_unknown_daemon_architecture_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(closure, "run_docker", FakeDocker(arch="s390x"))
    with pytest.raises(RuntimeError, match="unsupported"):
        await closure.docker_arch("docker", "ctx")


# --------------------------------------------------------------- closure image
def test_the_closure_build_context_holds_only_the_requirements_and_wheels(
    tmp_path: Path,
) -> None:
    wheels = _wheeled(tmp_path, "pkg")
    lock = FakeResolver(wheels=wheels)(["pkg"], arch="amd64", exclude_newer=CUTOFF).lock_text
    fetched, context = tmp_path / "fetched", tmp_path / "ctx"
    wheel_cache.fetch_wheels(lock, fetched, cache_root=tmp_path / "cache")
    closure.build_context(lock, fetched, context)
    assert sorted(p.relative_to(context).as_posix() for p in context.rglob("*")) == [
        "requirements.txt",
        "wheels",
        "wheels/pkg-1.0-py3-none-any.whl",
    ]
    text = (context / "requirements.txt").read_text()
    assert text.startswith("pkg==1.0 \\\n    --hash=sha256:")
    assert text.endswith("\n")


def test_the_closure_layer_installs_hash_checked_wheels_without_network_or_dependencies() -> None:
    sha = "cd" * 32
    text = closure_dockerfile("grader:abc", sha, "/usr/local/bin/python3")
    assert text.startswith("FROM grader:abc\nUSER root\n")
    assert "RUN --network=none --mount=type=bind,target=/raidar-ctx " in text
    assert "--require-hashes --no-deps --only-binary :all:" in text
    assert f"--target {closure_dir(sha)}" in text
    assert "chmod -R a+rX" in text
    assert closure_tag("grader:abc", sha) != closure_tag("grader:abd", sha)
    assert closure_tag("grader:abc", sha) != closure_tag("grader:abc", "ef" * 32)
    assert closure.install_command(python="/py", target=None).count("--target") == 0


async def test_the_closure_image_is_built_once_per_base_and_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docker = FakeDocker()
    monkeypatch.setattr(closure, "run_docker", docker)
    wheels = _wheeled(tmp_path, "pkg")
    lock = FakeResolver(wheels=wheels)(["pkg"], arch="amd64", exclude_newer=CUTOFF).lock_text
    sandbox = DockerCopySandbox(image="grader:base", docker_context="mbp")
    options = {"cache_root": tmp_path / "cache"}
    first = await closure.ensure_closure_image(
        "grader:base", lock, lock_sha256(lock), docker_context="mbp", python="/py", **options
    )
    assert first.built
    assert first.image_id.startswith("sha256:")
    assert docker.builds[0]["files"] == [
        "requirements.txt",
        "wheels",
        "wheels/pkg-1.0-py3-none-any.whl",
    ]
    again = await closure.ensure_closure_image(
        "grader:base", lock, lock_sha256(lock), docker_context="mbp", python="/py", **options
    )
    assert not again.built
    assert again.tag == first.tag
    assert len(docker.builds) == 1
    assert await sandbox.target_arch() == "arm64"
    via_sandbox = await sandbox.closure_image(lock)
    assert isinstance(via_sandbox, ClosureImage)
    assert via_sandbox.tag == first.tag


async def test_a_closure_build_that_leaves_no_image_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def hollow(*args: str, **kwargs: object) -> tuple[int, str]:
        return (1, "") if args[2:4] == ("image", "inspect") else (0, "")

    monkeypatch.setattr(closure, "run_docker", hollow)
    lock = FakeResolver(wheels=_wheeled(tmp_path, "pkg"))(
        ["pkg"], arch="amd64", exclude_newer=CUTOFF
    ).lock_text
    with pytest.raises(RuntimeError, match="missing after its build"):
        await closure.ensure_closure_image(
            "base",
            lock,
            lock_sha256(lock),
            docker_context="mbp",
            python="/py",
            cache_root=tmp_path / "cache",
        )


async def test_run_docker_returns_the_exit_code_and_output(tmp_path: Path) -> None:
    script = tmp_path / "docker"
    script.write_text(
        f"#!{sys.executable}\nimport sys\nprint(' '.join(sys.argv[1:]))\nsys.exit(3)\n"
    )
    script.chmod(0o755)
    code, output = await closure.run_docker(str(script), "ctx", "ps", env={"X": "1"})
    assert (code, output.strip()) == (3, "--context ctx ps")
    code, output = await closure.run_docker(str(script), "ctx", "build", stdin=b"FROM x\n")
    assert code == 3


async def test_a_wedged_docker_command_is_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "docker"
    script.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
    script.chmod(0o755)
    monkeypatch.setattr(closure, "BUILD_TIMEOUT_S", 0.5)
    with pytest.raises(TimeoutError):
        await closure.run_docker(str(script), "ctx", "ps")


def test_the_sandbox_runs_in_the_closure_image_when_one_is_named() -> None:
    sandbox = DockerCopySandbox(image="grader:base", docker_context="mbp")
    common = {
        "app_dir": Path("a"),
        "tests_dir": Path("t"),
        "out_dir": Path("o"),
        "argv": ("x",),
        "env": {},
        "timeout_s": 1.0,
        "cpus": 1.0,
        "memory_mb": 64,
    }
    plain = sandbox.create_argv(SandboxRun(**common), "n")  # type: ignore[arg-type]
    layered = sandbox.create_argv(SandboxRun(**common, image="closure:1"), "n")  # type: ignore[arg-type]
    assert plain[-2:] == ["grader:base", "x"]
    assert layered[-2:] == ["closure:1", "x"]
    assert layered[layered.index("--network") + 1] == "none"
    assert "ALL" in layered
    assert "no-new-privileges" in layered


# ---------------------------------------------------- .pth and namespace packages
def test_pth_files_and_namespace_packages_of_the_closure_are_honoured(tmp_path: Path) -> None:
    """A split namespace and a .pth path remain usable in the offline closure."""
    deps = tmp_path / "deps"
    (deps / "rns" / "protobuf").mkdir(parents=True)
    (deps / "rns" / "protobuf" / "__init__.py").write_text("NAME = 'protobuf'\n")
    (deps / "extra" / "viaPth").mkdir(parents=True)
    (deps / "extra" / "viaPth" / "__init__.py").write_text("OK = True\n")
    (deps / "thing.pth").write_text("extra\n")
    other = tmp_path / "other" / "rns" / "cloud"
    other.mkdir(parents=True)
    (other / "__init__.py").write_text("NAME = 'cloud'\n")
    program = (
        "import rns.protobuf, rns.cloud, viaPth; "
        "print(rns.protobuf.NAME, rns.cloud.NAME, viaPth.OK)"
    )
    environment = {
        "PYTHONPATH": f"{RUNTIME_DIR}:{tmp_path / 'other'}:{deps}",
        "RAIDAR_SUBMISSION_DEPS": str(deps),
        "PATH": "",
    }
    done = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert done.stdout.strip() == "protobuf cloud True", done.stderr
    bare = {**environment, "RAIDAR_SUBMISSION_DEPS": ""}
    missing = subprocess.run(
        [sys.executable, "-c", "import viaPth"],
        capture_output=True,
        text=True,
        env=bare,
        check=False,
    )
    assert missing.returncode != 0
    assert "ModuleNotFoundError" in missing.stderr
