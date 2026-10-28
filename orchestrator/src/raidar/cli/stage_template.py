"""The ``stage-new`` template: a complete tier-2 command package with its gate fixtures.

Tier 2 is a package that wraps an existing, pinned third-party command and writes no tool
code of its own: here ``codespell`` as a gate check that refuses a commit whose text files hold
common misspellings. The package has a strict manifest, prompt, instructions, a skill, a
dependency-only ``tool/pyproject.toml`` with the lock uv generated for it (embedded unchanged;
wheels only) and gate argv. Its fixtures are the mandatory ``pass`` and ``refuse`` cases plus
adversarial workspaces (nested misspelling, zero-byte, binary and non-ASCII files). Raidar runs
each through the gate twice. Nothing here imports Raidar.
"""

from __future__ import annotations

import json
import re

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CODESPELL = "codespell==2.4.1"
MODULE = "codespell_lib"
GATE_ARGS = ("--quiet-level=2", "--skip=.git", ".")

CLEAN = "# Notes\n\nThe receive path is covered by tests.\n"
MISSPELT = "# Notes\n\nThe recieve path is covered by teh tests.\n"
UNICODE_CLEAN = "# Café notes\n\nA naïve parser accepts ✓ and 日本語 text.\n"
BINARY = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x01\x00recieve\x00"

TEMPLATES = ("docs-check",)
DEFAULT_TEMPLATE = "docs-check"

PYPROJECT = f"""\
[project]
name = "stage-tool"
version = "0"
requires-python = ">=3.12"
dependencies = ["{CODESPELL}"]
"""

# Exactly what ``uv lock --no-build`` generated for PYPROJECT, with uv's sdist metadata removed
# (wheels only; the removal is ``generated_wheels_only_lock``'s). The tool project's name is
# fixed, so the lock is copied unchanged into every package made from this template.
LOCK = (
    """\
version = 1
revision = 3
requires-python = ">=3.12"

[[package]]
name = "codespell"
version = "2.4.1"
source = { registry = "https://pypi.org/simple" }
wheels = [
"""
    '    { url = "https://files.pythonhosted.org/packages/20/01/'
    "b394922252051e97aab231d416c86da3d8a6d781eeadcdca1082867de64e/"
    'codespell-2.4.1-py3-none-any.whl", hash = "sha256:'
    '3dadafa67df7e4a3dbf51e0d7315061b80d265f9552ebd699b3dd6834b47e425", '
    'size = 344501, upload-time = "2025-01-28T18:52:37.057Z" },\n'
    """\
]

[[package]]
name = "stage-tool"
version = "0"
source = { virtual = "." }
dependencies = [
    { name = "codespell" },
]

[package.metadata]
requires-dist = [{ name = "codespell", specifier = "==2.4.1" }]
"""
)


def _context(name: str) -> str:
    document = {
        "task": "fixture",
        "params": {},
        "round": 0,
        "carry": {},
        "state": f"/app/.raidar/{name}",
        "outputs": {},
    }
    return json.dumps(document, separators=(",", ":")) + "\n"


def _manifest(name: str) -> str:
    argv = ", ".join((name, *GATE_ARGS))
    return f"""\
contract: stage/1
name: {name}
summary: Refuse a commit whose text files contain common misspellings (codespell).
requires: {{gate: gate/1}}
tool:
  project: tool
  commands: {{{name}: [python, -I, -B, -m, {MODULE}]}}
gate:
  - name: spelling
    run: [{argv}]
    timeout_s: 60
skills: [{name}]
"""


PROMPT = """\
Your commits are checked for common misspellings in every text file. Fix a reported word
before you commit again.
"""


def _instructions(name: str) -> str:
    return f"""\
# {name}

Every commit runs a spelling check over the whole tree. A refused commit lists the misspelt
words: correct them in place and commit again.
"""


def _skill(name: str) -> str:
    return f"""\
---
name: {name}
description: Fix the misspellings the {name} gate reports.
---

# Fixing reported words

1. Read the reported file, line and word.
2. Correct the word in the file; do not edit the gate or add skip rules.
3. Commit again.
"""


def _expect(status: str) -> str:
    return f"status: {status}\noutputs: {{}}\n"


def render_template(name: str, template: str = DEFAULT_TEMPLATE) -> dict[str, bytes]:
    """Every file of the new package from ``template`` (only ``docs-check`` exists)."""
    if template not in TEMPLATES:
        raise ValueError(f"no stage template {template!r}; available: {', '.join(TEMPLATES)}")
    leaf = name.rsplit("/", 1)[-1]
    if not NAME_RE.fullmatch(leaf):
        raise ValueError(f"{name!r} is not a valid package name (lowercase letters, digits, '-')")
    files: dict[str, bytes] = {
        "stage.yaml": _manifest(leaf).encode(),
        "prompt.md": PROMPT.encode(),
        "instructions.md": _instructions(leaf).encode(),
        f"skills/{leaf}/SKILL.md": _skill(leaf).encode(),
        "tool/pyproject.toml": PYPROJECT.encode(),
        "tool/uv.lock": LOCK.encode(),
    }
    cases: dict[str, tuple[str, dict[str, bytes]]] = {
        "pass": ("pass", {"README.md": CLEAN.encode()}),
        "refuse": ("refuse", {"README.md": MISSPELT.encode()}),
        "nested-misspelling": ("refuse", {"docs/guide/intro.md": MISSPELT.encode()}),
        "zero-byte-file": ("pass", {"README.md": b""}),
        "binary-file": ("pass", {"assets/image.bin": BINARY}),
        "non-ascii-text": ("pass", {"README.md": UNICODE_CLEAN.encode()}),
    }
    for case, (status, state) in cases.items():
        base = f"fixtures/{case}"
        files[f"{base}/context.json"] = _context(leaf).encode()
        files[f"{base}/expect.yaml"] = _expect(status).encode()
        for filename, data in state.items():
            files[f"{base}/state/{filename}"] = data
    return files
