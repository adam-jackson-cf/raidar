"""Workspace seeding (design decision D2): ``/app`` gets its own project before checkpoint 1.

The grader runs the entry file as ``uv run --project /app``, resolving in the workspace's own
project and nowhere else, and gives the program the problem's declared dependencies only when
the submission declares them. An agent working in an empty ``/app`` sees whatever its session
has on the path, so a package it never declared imports while it works and fails only when
graded: that scores the absence of a ``uv add``, not the code (fault FCR-004). The problem
already says what it needs (its SCB ``test_dependencies``, pinned in the verifier bundle), so
the lab owes the solution the same environment it owes the tests (factory-x-loops
``evaluation/benchmark/scb.py`` ``write_workspace_project`` / ``sync_workspace_project``).

Before checkpoint 1 the lab therefore writes ``/app/pyproject.toml`` naming those dependencies
and installs their pinned closure into ``/app/.venv`` as the agent user, as a per-trial
provision at environment start (``raidar.engine.trial.provision``): outside every step, agent
and verifier duration; its time is the ``workspace_seed`` phase, taken out of
``environment_start``. The provision records what was seeded (dependencies, the file's sha256,
the interpreter, the cutoff, the lock digests) and the digest of the seeded ``/app``, which is
the parent of checkpoint 1's snapshot. Its check, run before every step's agent, reports the
file's current sha256; at checkpoint 1 it must equal the seeded one.

The seed obeys the problem pin's ``declared-wheels/1`` policy (``raidar.grading.submission_deps``).
It installs the pin's lock for the problem's ``test_dependencies`` only (never a reference
step's declared set, nor the grader's tools), for the container's architecture, with
``uv pip install --require-hashes --no-deps --only-binary :all:``: the agent's closure is the
one the grader installs, and no package code runs. The seeded ``pyproject.toml`` carries
``[tool.uv] exclude-newer`` and ``no-build = true``; the agent user's ``uv.toml`` and
``pip.conf`` carry the same cutoff, wheels-only and PyPI-only rules, so an ``uv add`` or
``pip install`` during work fails where grading would. pip enforces a cutoff only from
release 26.0 (``--uploaded-prior-to``); when the image's pip is older the install replaces it,
system-wide and by hash, with ``PIP_VERSION`` (a wheel, no build) and fails if the result still
lacks the option. The check reports ``pip_cutoff=enforced`` each step; ``unsupported`` is a
lab failure.
"""

from __future__ import annotations

import hashlib
import re
import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path

from raidar.contract.snapshot import APP_SOURCE
from raidar.contract.wheels import ARCHES
from raidar.engine.trial.provision import PayloadFile, TrialProvision, read_provision_record
from raidar.grading.dependency_pins import DependencyLocks, DependencyPins

SEED_SLUG = "workspace-project"
PROJECT_FILE = "pyproject.toml"
SHA_KEY = "pyproject_sha256"
ABSENT = "absent"
PIP_KEY = "pip_cutoff"
UV_FILE = "uv.toml"
PIP_FILE = "pip.conf"
PIP_PIN_FILE = "pip-pin.txt"
INDEX = "https://pypi.org/simple"
# Pip's cutoff option requires this hash-pinned release.
PIP_VERSION = "26.1.2"
PIP_WHEEL_SHA256 = "382ff9f685ee3bc25864f820aa50505825f10f5458ffff07e30a6d96e5715cab"
ARCH_UNAME = {"amd64": "x86_64", "arm64": "aarch64"}


def lock_file(arch: str) -> str:
    return f"pylock.{arch}.toml"


def seed_locks(pins: DependencyPins, locks: DependencyLocks) -> dict[str, str]:
    """The pin's lock text per architecture for the problem's ``test_dependencies`` only.

    Empty when the problem has none. Reads only ``pins.seed``: a reference step's set and the
    grader's environment (which holds the verifier tools) never reach the agent.
    """
    if pins.seed is None:
        return {}
    seed = pins.sets[pins.seed]
    return {arch: locks.read(pins.seed, arch, seed.lock(arch)) for arch in ARCHES}


def workspace_project(problem: str, dependencies: Sequence[str], python: str, cutoff: str) -> str:
    """The seeded ``pyproject.toml``: the problem's declared dependencies and the pin's policy.

    The project name is the problem reference reduced to a valid package name (letters,
    digits and ``-``), so ``scb/forge@38d627ecf668`` names ``scb-forge-38d627ecf668-workspace``.
    ``cutoff`` is the pin's ``exclude_newer`` (RFC 3339, UTC).
    """
    name = re.sub(r"[^A-Za-z0-9]+", "-", problem).strip("-").lower()
    listed = ", ".join(f'"{dependency}"' for dependency in dependencies)
    return (
        f'[project]\nname = "{name}-workspace"\nversion = "0"\n'
        f'requires-python = ">={python}"\ndependencies = [{listed}]\n\n'
        f'[tool.uv]\nexclude-newer = "{cutoff}"\nno-build = true\n'
    )


def uv_config(cutoff: str) -> str:
    """The agent user's ``uv.toml``: the policy's cutoff, wheels only, PyPI only."""
    return f'exclude-newer = "{cutoff}"\nno-build = true\nindex-url = "{INDEX}"\n'


def pip_config(cutoff: str) -> str:
    """The agent user's ``pip.conf``: the policy's cutoff, wheels only, PyPI only."""
    return f"[global]\nonly-binary = :all:\nindex-url = {INDEX}\nuploaded-prior-to = {cutoff}\n"


# The shell test for pip's cutoff option, shared by the install and the per-step check.
_PIP_ENFORCES = "pip install --help 2>/dev/null | grep -q -- --uploaded-prior-to"


def _install(python: str, arches: Sequence[str]) -> str:
    project = shlex.quote(f"{APP_SOURCE}/{PROJECT_FILE}")
    stage = '"$RAIDAR_PAYLOAD_DIR"'
    pin = f"{stage}/{PIP_PIN_FILE}"
    steps = [
        "set -eu",
        'user="${RAIDAR_AGENT_USER:?the task names no agent user to own the workspace}"',
        'group="$(id -gn "$user")"',
        f'install -m 0644 -o "$user" -g "$group" {stage}/{PROJECT_FILE} {project}',
        'home="$(getent passwd "$user" | cut -d: -f6)"',
        'install -d -m 0755 -o "$user" -g "$group" "$home/.config" "$home/.config/uv" '
        '"$home/.config/pip"',
        f'install -m 0644 {stage}/{UV_FILE} "$home/.config/uv/uv.toml"',
        f'install -m 0644 {stage}/{PIP_FILE} "$home/.config/pip/pip.conf"',
        f"if ! {_PIP_ENFORCES}; then",
        "  uv pip install --system --break-system-packages --no-config --require-hashes "
        '--no-deps --only-binary :all: --python "$(command -v python3)" '
        f"-r {pin} 2>&1",
        f'  {_PIP_ENFORCES} || {{ echo "pip {PIP_VERSION} lacks the cutoff option"; exit 1; }}',
        "fi",
        f'runuser -u "$user" -- env UV_PYTHON_DOWNLOADS=never sh -c '
        f"{shlex.quote(f'cd {APP_SOURCE} && uv venv -q --allow-existing --python {python}')} 2>&1",
    ]
    if arches:
        cases = "\n".join(
            f"  {ARCH_UNAME[arch]}) lock={stage}/{lock_file(arch)};;" for arch in arches
        )
        install = (
            f"cd {APP_SOURCE} && uv pip install --python .venv/bin/python --require-hashes "
            '--no-deps --only-binary :all: -r "$1"'
        )
        steps += [
            'case "$(uname -m)" in',
            cases,
            '  *) echo "no pinned lock for architecture $(uname -m)"; exit 1;;',
            "esac",
            'tmp="$(mktemp -d)"',
            "trap 'rm -rf \"$tmp\"' EXIT",
            'chmod 0755 "$tmp"',
            'install -m 0644 "$lock" "$tmp/pylock.toml"',
            f'runuser -u "$user" -- env UV_PYTHON_DOWNLOADS=never sh -c '
            f'{shlex.quote(install)} sh "$tmp/pylock.toml" 2>&1',
        ]
    return "\n".join(steps)


def _check() -> str:
    project = shlex.quote(f"{APP_SOURCE}/{PROJECT_FILE}")
    return (
        f"if [ -f {project} ]; then "
        f'echo "{SHA_KEY}=$(sha256sum {project} | cut -d " " -f1)"; '
        f"else echo {SHA_KEY}={ABSENT}; fi\n"
        f"if {_PIP_ENFORCES}; then echo {PIP_KEY}=enforced; else echo {PIP_KEY}=unsupported; fi"
    )


def workspace_seed(
    problem: str,
    dependencies: Sequence[str],
    python: str,
    cutoff: str,
    locks: Mapping[str, str],
) -> TrialProvision:
    """The provision that writes the workspace project and installs its pinned closure.

    ``locks`` maps an architecture (``amd64``, ``arm64``) to the pin's pylock text for the
    problem's ``test_dependencies``; it is empty exactly when there are none.
    """
    if bool(dependencies) != bool(locks):
        raise ValueError("a workspace seed with dependencies needs their locks, and only then")
    content = workspace_project(problem, dependencies, python, cutoff).encode()
    payload = {
        PROJECT_FILE: PayloadFile(content),
        UV_FILE: PayloadFile(uv_config(cutoff).encode()),
        PIP_FILE: PayloadFile(pip_config(cutoff).encode()),
        PIP_PIN_FILE: PayloadFile(
            f"pip=={PIP_VERSION} --hash=sha256:{PIP_WHEEL_SHA256}\n".encode()
        ),
    }
    for arch, text in locks.items():
        payload[lock_file(arch)] = PayloadFile(text.encode())
    return TrialProvision(
        slug=SEED_SLUG,
        payload=payload,
        install=_install(python, sorted(locks)),
        check=_check(),
        snapshot=APP_SOURCE,
        facts={
            "dependencies": list(dependencies),
            "python": python,
            "exclude_newer": cutoff,
            "lock_sha256": {
                arch: hashlib.sha256(text.encode()).hexdigest() for arch, text in locks.items()
            },
            SHA_KEY: hashlib.sha256(content).hexdigest(),
        },
    )


def seed_check(agent_dir: Path) -> tuple[bool, str]:
    """At checkpoint 1: the seeded ``pyproject.toml`` was in ``/app`` when the agent started."""
    record = read_provision_record(agent_dir, SEED_SLUG)
    if record is None:
        return False, f"{SEED_SLUG}: no provision record before the agent started"
    seeded = (record.get("facts") or {}).get(SHA_KEY)
    values = (record.get("check") or {}).get("values") or {}
    found = values.get(SHA_KEY)
    if not seeded or found != seeded:
        state = found or "unchecked (the install failed)"
        return False, f"{SEED_SLUG}: /app/{PROJECT_FILE} was {state} at the start, seeded {seeded}"
    if values.get(PIP_KEY) == "unsupported":
        return False, f"{SEED_SLUG}: the agent's pip cannot honour the dependency cutoff"
    return True, f"{SEED_SLUG}: seeded {PROJECT_FILE} present"


def seeded_digest(agent_dir: Path) -> str | None:
    """The snapshot digest of the seeded ``/app`` (checkpoint 1's parent), if recorded."""
    record = read_provision_record(agent_dir, SEED_SLUG)
    digest = ((record or {}).get("install") or {}).get("snapshot_digest")
    return digest if isinstance(digest, str) else None


def seed_duration(trial_dir: Path, steps: Sequence[str]) -> float | None:
    """The seed's install duration (the ``workspace_seed`` perf phase), from the first step's
    provision record that has one; every step's record repeats the same install."""
    for step in steps:
        record = read_provision_record(trial_dir / "steps" / step / "agent", SEED_SLUG)
        duration = ((record or {}).get("install") or {}).get("duration_s")
        if isinstance(duration, int | float) and not isinstance(duration, bool):
            return float(duration)
    return None
