"""Build, tag and audit the Python pack agent image.

The image is tagged by a lock hash over the pack image spec (base digest,
harness pins, forbidden list) and the build context, so a changed input always
yields a new tag and an unchanged input never rebuilds.

The tag is only reused when the image under it carries the matching lock-hash
label and passes the audit. An image that fails its audit is untagged, so it can
never become the reusable warm image.

apt and npm resolve their transitive packages at build time, so the tag does
not lock the image contents. The audit records what a build produced instead:
the image ID plus digests of the dpkg and global npm package manifests.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from raidar.packs.python.harness import harbor_reported_version, harbor_version_command
from raidar.packs.python.models import PackSpec, ToolLock
from raidar.spec.canonical import IGNORED_TREE_NAMES

LOCK_HASH_LABEL = "dev.raidar.pack.lock-hash"
PACK_ID_LABEL = "dev.raidar.pack.id"
_TAG_HASH_CHARS = 16


class DockerRunner(Protocol):
    def __call__(
        self, args: Sequence[str], *, check: bool, capture: bool = True
    ) -> subprocess.CompletedProcess[str]:
        """Run ``docker <args>``; ``capture=False`` streams output to stderr."""
        ...


def run_docker(
    args: Sequence[str], *, check: bool, capture: bool = True
) -> subprocess.CompletedProcess[str]:
    """Default runner: the ``docker`` CLI on the caller's ``DOCKER_CONTEXT``.

    Uncaptured output goes to stderr so a command's JSON result on stdout stays
    parseable while a long build log stays visible.
    """
    if capture:
        return subprocess.run(  # noqa: S603 - fixed executable, argv list
            ["docker", *args],  # noqa: S607 - docker resolves from PATH
            capture_output=True,
            text=True,
            check=check,
        )
    return subprocess.run(  # noqa: S603 - fixed executable, argv list
        ["docker", *args],  # noqa: S607 - docker resolves from PATH
        stdout=sys.stderr,
        stderr=sys.stderr,
        text=True,
        check=check,
    )


def _tree_digest(root: Path) -> str:
    """Digest of the build context; editor and OS droppings are not recipe content."""
    digest = hashlib.sha256()
    files = (
        p
        for p in root.rglob("*")
        if p.is_file() and not IGNORED_TREE_NAMES.intersection(p.relative_to(root).parts)
    )
    for path in sorted(files):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def image_lock_hash(spec: PackSpec) -> str:
    payload = {
        "pack": spec.id,
        "version": spec.version,
        "python": spec.python,
        # The context location is not an input; its content is.
        "image": spec.image.model_dump(mode="json", exclude={"context"}),
        "context": _tree_digest(spec.context_dir),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def image_tag(spec: PackSpec) -> str:
    return f"{spec.image.repository}:{image_lock_hash(spec)[:_TAG_HASH_CHARS]}"


def _harness_arg_prefix(harness_id: str) -> str:
    return harness_id.upper().replace("-", "_")


def build_args(spec: PackSpec) -> dict[str, str]:
    args = {"BASE_IMAGE": spec.image.base_image, "AGENT_HOME": spec.image.agent_home}
    for harness_id, pin in spec.image.harnesses.items():
        prefix = _harness_arg_prefix(harness_id)
        args[f"{prefix}_NPM_PACKAGE"] = pin.npm_package
        args[f"{prefix}_VERSION"] = pin.version
    return args


@dataclass(frozen=True)
class ImageRecord:
    """What ``docker image inspect`` says about a local tag."""

    tag: str
    image_id: str
    labels: Mapping[str, str]


class ImageNotBuiltError(LookupError):
    """The pack image tag is not present in the local Docker image store."""


def inspect_image(tag: str, docker: DockerRunner | None = None) -> ImageRecord | None:
    run = docker or run_docker
    completed = run(["image", "inspect", tag], check=False)
    if completed.returncode != 0:
        return None
    (document,) = json.loads(completed.stdout)
    labels = (document.get("Config") or {}).get("Labels") or {}
    return ImageRecord(tag=tag, image_id=str(document["Id"]), labels=dict(labels))


def image_present(tag: str, docker: DockerRunner | None = None) -> bool:
    return inspect_image(tag, docker) is not None


@dataclass(frozen=True)
class ImageAudit:
    tag: str
    image_id: str
    harness_versions: dict[str, str]
    harness_mismatches: tuple[str, ...]
    default_python: str
    python_mismatch: bool
    python_interpreters: dict[str, str]
    interpreter_errors: tuple[str, ...]
    scorer_executables: tuple[str, ...]
    scorer_modules: tuple[str, ...]
    scorer_distributions: tuple[str, ...]
    browser_executables: tuple[str, ...]
    content: dict[str, str]

    @property
    def ok(self) -> bool:
        return not (
            self.harness_mismatches
            or self.python_mismatch
            or self.interpreter_errors
            or self.scorer_executables
            or self.scorer_modules
            or self.scorer_distributions
            or self.browser_executables
        )


@dataclass(frozen=True)
class BuildResult:
    tag: str
    lock_hash: str
    built: bool
    build_seconds: float
    audit: ImageAudit
    image_id: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "image_id", self.audit.image_id)


# Runs under one interpreter in the image; reports the scorer modules and
# distributions that interpreter can see.
_MODULE_PROBE = """
import importlib.metadata, importlib.util, json, platform, sys
req = json.loads(sys.argv[1])
def has_module(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False
def has_dist(name):
    try:
        importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True
print(json.dumps({
    "version": platform.python_version(),
    "modules": [m for m in req["modules"] if has_module(m)],
    "distributions": [d for d in req["distributions"] if has_dist(d)],
}))
"""

# Runs inside the image with the default python3; prints one JSON object.
# Every python3 interpreter in the image is probed, not only the default one,
# because apt can bring its own (npm -> node-gyp -> Debian python3).
_AUDIT_PROBE = r"""
import glob, hashlib, json, os, platform, re, shutil, subprocess, sys
req = json.loads(sys.argv[1])
def run(cmd):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, f"error: {error}"
    return out.returncode, (out.stdout or out.stderr).strip()
def run_merged(command):
    # Harbor execs its version check as `bash -c` with stderr merged into stdout,
    # and reinstalls on a non-zero exit before parsing, so both are reported.
    try:
        out = subprocess.run(["bash", "-c", command], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"exit_code": 127, "output": f"error: {error}"}
    return {"exit_code": out.returncode, "output": out.stdout}
def interpreters():
    dirs = os.environ.get("PATH", "").split(os.pathsep) + ["/usr/bin", "/usr/local/bin"]
    found = {}
    for directory in dirs:
        for path in sorted(glob.glob(os.path.join(directory, "python3*"))):
            name = os.path.basename(path)
            if re.fullmatch(r"python3(\.\d+)?", name) and os.access(path, os.X_OK):
                found.setdefault(os.path.realpath(path), path)
    return sorted(found.values())
scan = json.dumps({"modules": req["modules"], "distributions": req["distributions"]})
probed = {}
for path in interpreters():
    code, out = run([path, "-c", req["module_probe"], scan])
    try:
        probed[path] = json.loads(out) if code == 0 else {"error": out[-500:]}
    except ValueError:
        probed[path] = {"error": out[-500:]}
def manifest_digest(cmd):
    code, out = run(cmd)
    return hashlib.sha256(out.encode()).hexdigest() if code == 0 else f"error: {out[-200:]}"
dpkg = ["dpkg-query", "-W", "-f", "${Package}=${Version}\n"]
print(json.dumps({
    "harness": {h: run_merged(command) for h, command in req["harnesses"].items()},
    "default_python": platform.python_version(),
    "interpreters": probed,
    "executables": [e for e in req["executables"] if shutil.which(e)],
    "browsers": [b for b in req["browsers"] if shutil.which(b)],
    "content": {
        "dpkg_manifest_sha256": manifest_digest(dpkg),
        "npm_global_manifest_sha256": manifest_digest(["npm", "ls", "-g", "--all", "--json"]),
    },
}))
"""


def _python_matches(declared: str, actual: str) -> bool:
    parts = declared.split(".")
    return actual.split(".")[: len(parts)] == parts


def _union(probed: Mapping[str, Mapping[str, Any]], key: str) -> tuple[str, ...]:
    return tuple(sorted({name for found in probed.values() for name in found.get(key, ())}))


def audit_image(
    tag: str, spec: PackSpec, lock: ToolLock, docker: DockerRunner | None = None
) -> ImageAudit:
    """Assert the image carries the pinned harnesses and no scorer tools or browsers.

    Scorer tools are every executable, module and distribution named in the
    tool lock, both host-parse and eval-sandbox classes. Modules and
    distributions are checked under every python3 interpreter in the image,
    and the default ``python3`` must be the pack's declared Python.
    """
    run = docker or run_docker
    record = inspect_image(tag, run)
    if record is None:
        raise ImageNotBuiltError(f"pack image {tag} is not built; run `make pack-build`")
    request = {
        "harnesses": {h: harbor_version_command(pin) for h, pin in spec.image.harnesses.items()},
        "executables": list(lock.scorer_executables()),
        "modules": list(lock.scorer_modules()),
        "distributions": list(lock.scorer_distributions()),
        "browsers": list(spec.image.forbidden_executables),
        "module_probe": _MODULE_PROBE,
    }
    argv = ["run", "--rm", "--network", "none", "--entrypoint", "python3", record.image_id]
    completed = run([*argv, "-c", _AUDIT_PROBE, json.dumps(request)], check=True)
    return _audit_from_probe(record, spec, json.loads(completed.stdout))


def _audit_from_probe(record: ImageRecord, spec: PackSpec, found: dict[str, Any]) -> ImageAudit:
    checks: dict[str, dict[str, Any]] = found["harness"]
    # What Harbor's install() would conclude; anything but the exact pin reinstalls.
    versions = {
        h: harbor_reported_version(
            pin,
            str(checks.get(h, {}).get("output", "")),
            int(checks.get(h, {}).get("exit_code", 1)),
        )
        for h, pin in spec.image.harnesses.items()
    }
    probed: dict[str, dict[str, Any]] = found["interpreters"]
    mismatches = tuple(
        sorted(h for h, pin in spec.image.harnesses.items() if versions[h] != pin.version)
    )
    default_python = str(found["default_python"])
    return ImageAudit(
        tag=record.tag,
        image_id=record.image_id,
        harness_versions=versions,
        harness_mismatches=mismatches,
        default_python=default_python,
        python_mismatch=not _python_matches(spec.python, default_python),
        python_interpreters={p: str(r.get("version", r.get("error"))) for p, r in probed.items()},
        interpreter_errors=tuple(sorted(p for p, r in probed.items() if "error" in r)),
        scorer_executables=tuple(found["executables"]),
        scorer_modules=_union(probed, "modules"),
        scorer_distributions=_union(probed, "distributions"),
        browser_executables=tuple(found["browsers"]),
        content={k: str(v) for k, v in found["content"].items()},
    )


class ImageAuditError(RuntimeError):
    def __init__(self, audit: ImageAudit) -> None:
        self.audit = audit
        super().__init__(f"pack image {audit.tag} failed its audit and was untagged: {audit}")


def _audited(tag: str, spec: PackSpec, lock: ToolLock, run: DockerRunner) -> ImageAudit:
    audit = audit_image(tag, spec, lock, run)
    if not audit.ok:
        # Untag so the lock-hash tag can never be reused as the warm image.
        run(["image", "rm", "--force", tag], check=False)
        raise ImageAuditError(audit)
    return audit


def build_image(
    spec: PackSpec,
    lock: ToolLock,
    *,
    force: bool = False,
    docker: DockerRunner | None = None,
) -> BuildResult:
    """Build the pack image once per lock hash; audit it on every call.

    An image already under the lock-hash tag is reused without a build only
    when its lock-hash label matches; either way it is audited, and an image
    that fails the audit is untagged before ``ImageAuditError`` is raised.
    """
    run = docker or run_docker
    lock_hash = image_lock_hash(spec)
    tag = image_tag(spec)
    existing = inspect_image(tag, run)
    if existing and existing.labels.get(LOCK_HASH_LABEL) == lock_hash and not force:
        audit = _audited(tag, spec, lock, run)
        return BuildResult(
            tag=tag, lock_hash=lock_hash, built=False, build_seconds=0.0, audit=audit
        )
    argv = ["build", "--tag", tag, "--label", f"{PACK_ID_LABEL}={spec.id}"]
    argv += ["--label", f"{LOCK_HASH_LABEL}={lock_hash}"]
    for key, value in build_args(spec).items():
        argv += ["--build-arg", f"{key}={value}"]
    argv.append(str(spec.context_dir))
    started = time.perf_counter()
    run(argv, check=True, capture=False)
    seconds = time.perf_counter() - started
    audit = _audited(tag, spec, lock, run)
    return BuildResult(tag=tag, lock_hash=lock_hash, built=True, build_seconds=seconds, audit=audit)
