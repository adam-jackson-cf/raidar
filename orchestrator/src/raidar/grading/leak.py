"""Assert that no hidden oracle test is present in the agent's environment.

The verifier never uploads tests (the grading sandbox is a separate
container), so this is an assertion, not a sanitiser. It runs as root in the
agent environment and reports a leak when:

- ``/tests`` exists and is non-empty; or
- any file whose name matches a hidden test file has the same sha256 as that
  hidden file (content match, so an agent's own ``conftest.py`` or
  ``test_checkpoint_1.py`` is not a false positive).

The scan walks the container's whole filesystem tree, including ``/logs/*``;
only ``/proc``, ``/sys`` and ``/dev`` are
pruned. Empty hidden files (e.g. ``__init__.py``) are not fingerprints.

It runs twice per step (``raidar.grading.guard``): before the agent starts,
which is the only point where ``/tests`` is observable in Harbor's shared
multi-step mode (Harbor empties ``/tests`` right before ``verify()``), and in
``verify()``, which catches copies the agent made. A leaked step never reports
a pass.
"""

from __future__ import annotations

import shlex
import time
from pathlib import PurePosixPath
from typing import Any

from raidar.grading.bundle import VerifierBundle
from raidar.grading.models import VerifierModel

PRUNED_PATHS: tuple[str, ...] = ("/proc", "/sys", "/dev")
TESTS_DIR = "/tests"
SCAN_TIMEOUT_S = 120
_TESTS_TAG = "TESTS\t"
_DONE = "RAIDAR_LEAK_SCAN_DONE"
_UNAVAILABLE = "RAIDAR_LEAK_SCAN_UNAVAILABLE"


class LeakScanError(RuntimeError):
    """The assertion could not run, so the environment is not known to be clean."""


class LeakReport(VerifierModel):
    clean: bool
    tests_dir_entries: tuple[str, ...] = ()
    hidden_copies: tuple[str, ...] = ()
    fingerprints_checked: int = 0
    seconds: float = 0.0


def leak_scan_command(names: list[str]) -> str:
    name_expr = " -o ".join(f"-name {shlex.quote(name)}" for name in sorted(set(names)))
    pruned = " -o ".join(f"-path {path}" for path in PRUNED_PATHS)
    return (
        "{ command -v find && command -v sha256sum; } >/dev/null 2>&1"
        f" || {{ echo {_UNAVAILABLE}; exit 0; }}; "
        f"if [ -d {TESTS_DIR} ]; then find {TESTS_DIR} -mindepth 1 -maxdepth 2 2>/dev/null"
        f" | head -n 20 | sed 's/^/{_TESTS_TAG}/'; fi; "
        # No -xdev: a leaked copy may sit on any filesystem in the container.
        f"find / \\( {pruned} \\) -prune -o -type f"
        f" \\( {name_expr} \\) -print0 2>/dev/null"
        " | xargs -0 -r sha256sum 2>/dev/null | sort -u; "
        f"echo {_DONE}"
    )


def parse_leak_output(stdout: str, hashes: dict[str, str], seconds: float) -> LeakReport:
    lines = stdout.splitlines()
    if _UNAVAILABLE in lines or _DONE not in lines:
        raise LeakScanError(f"leak scan did not complete: {stdout[-500:]!r}")
    tests_entries: list[str] = []
    copies: set[str] = set()
    for line in lines:
        if line.startswith(_TESTS_TAG):
            tests_entries.append(line.removeprefix(_TESTS_TAG))
            continue
        digest, _, path = line.partition("  ")
        # sha256sum escapes a path holding a backslash or newline and prefixes the line with "\".
        digest = digest.removeprefix("\\")
        if digest in hashes and path:
            copies.add(f"{path} == {hashes[digest]}")
    return LeakReport(
        clean=not tests_entries and not copies,
        tests_dir_entries=tuple(tests_entries),
        hidden_copies=tuple(sorted(copies)),
        fingerprints_checked=len(hashes),
        seconds=seconds,
    )


async def scan_agent_environment(environment: Any, bundle: VerifierBundle) -> LeakReport:
    """Run the leak assertion in the live agent environment (Harbor ``BaseEnvironment``)."""
    hashes = bundle.hidden_file_hashes()
    names = [PurePosixPath(relative).name for relative in hashes.values()]
    started = time.perf_counter()
    result = await environment.exec(
        leak_scan_command(names), user="root", timeout_sec=SCAN_TIMEOUT_S
    )
    return parse_leak_output(result.stdout or "", hashes, time.perf_counter() - started)
