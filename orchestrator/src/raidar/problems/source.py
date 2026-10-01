"""Pinned SCB dataset checkout and the SCB Harbor converter, reused as-is.

The converter is the pin's ``converter`` entry script (default
``scripts/scb_to_harbor.py``, which drives ``scripts/scb_to_harbor_lib``) at
the pinned commit, and is the only SCB problem parser Raidar uses. It runs in
a subprocess against the pinned checkout, through its own CLI ``main``; the
only thing replaced is the Harbor CLI version probe (``resolve_harbor_command``
in ``main``'s module, i.e. ``uvx harbor --version``), which only feeds the
generated README and would reach the network.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from raidar.problems.models import DatasetPin

CONVERTER_ORG = "scb"
CONVERTER_TIMEOUT_S = 600
# Runs in the pinned checkout. argv: converter entry script, problem, out dir.
_CONVERTER_DRIVER = """
import importlib.util
import sys
converter, problem, out = sys.argv[1:4]
spec = importlib.util.spec_from_file_location("raidar_pinned_converter", converter)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
main = module.main
sys.modules[main.__module__].resolve_harbor_command = lambda repo_root: ([], None)
sys.exit(main(["--org", "scb", "--out", out, "--no-build", "--force", problem]))
"""


class SourceError(RuntimeError):
    """The pinned source cannot be obtained, is not at its pin, or conversion failed."""


def _git(checkout: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(checkout), *args], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise SourceError(f"git {' '.join(args)} failed in {checkout}: {proc.stderr.strip()}")
    return proc.stdout.strip()


@dataclass(frozen=True)
class PinnedCheckout:
    """A dataset checkout verified to sit at its pinned commit."""

    root: Path
    pin: DatasetPin

    @property
    def converter_dir(self) -> str:
        """The checkout-relative directory holding the pinned converter entry script."""
        return converter_path(self.pin).parent.as_posix()

    def tree_id(self, path: str) -> str:
        return _git(self.root, "rev-parse", f"{self.pin.commit}:{path}")

    def optional_tree_id(self, path: str) -> str | None:
        try:
            return self.tree_id(path)
        except SourceError:
            return None

    def verify_clean(self, *paths: str) -> None:
        """Refuse local edits (tracked, untracked or ignored) under the paths an import reads.

        Ignored files count too: the converter copies problem trees wholesale, so a
        ``__pycache__`` or ``.DS_Store`` there would change the bundle digest while
        the git tree ids still match the pin.
        """
        dirty = _git(
            self.root, "status", "--porcelain", "--ignored", "--untracked-files=all", "--", *paths
        )
        if dirty:
            raise SourceError(
                f"{self.root} has local changes under {', '.join(paths)}:\n{dirty[:2000]}"
            )


def converter_path(pin: DatasetPin) -> PurePosixPath:
    """The pin's converter as a checkout-relative path; it may not leave the checkout."""
    path = PurePosixPath(pin.converter)
    if path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
        raise SourceError(
            f"converter {pin.converter!r} must be a script path inside the dataset checkout "
            "(e.g. scripts/scb_to_harbor.py)"
        )
    return path


def open_checkout(root: Path, pin: DatasetPin) -> PinnedCheckout:
    head = _git(root, "rev-parse", "HEAD")
    if head != pin.commit:
        raise SourceError(f"{root} is at {head}, expected pinned commit {pin.commit}")
    return PinnedCheckout(root=root, pin=pin)


def ensure_checkout(cache_dir: Path, name: str, pin: DatasetPin) -> PinnedCheckout:
    """Clone ``pin.url`` into the cache once and check out the pinned commit."""
    root = cache_dir / "sources" / f"{name}-{pin.version}"
    if not (root / ".git").exists():
        root.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["git", "clone", "--quiet", "--no-checkout", pin.url, str(root)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise SourceError(f"clone of {pin.url} failed: {proc.stderr.strip()}")
        _git(root, "checkout", "--quiet", "--detach", pin.commit)
    return open_checkout(root, pin)


def convert_problem(checkout: PinnedCheckout, problem: str, out_dir: Path) -> Path:
    """Run the pinned converter for one problem; return the converted task dir."""
    converter = checkout.root / converter_path(checkout.pin)
    if not converter.is_file():
        raise SourceError(f"pinned converter {checkout.pin.converter} not found in {checkout.root}")
    proc = subprocess.run(
        [sys.executable, "-B", "-c", _CONVERTER_DRIVER, str(converter), problem, str(out_dir)],
        capture_output=True,
        text=True,
        check=False,
        timeout=CONVERTER_TIMEOUT_S,
    )
    task_dir = out_dir / CONVERTER_ORG / problem
    if proc.returncode != 0 or not (task_dir / "task.toml").is_file():
        tail = (proc.stdout + proc.stderr)[-3000:]
        raise SourceError(f"converter failed for {problem} (exit {proc.returncode}):\n{tail}")
    return task_dir
