#!/usr/bin/env python3
"""Refuse added Ruff suppression comments and configuration entries."""

from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
import tokenize
import tomllib
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

# Snapshot of RUFF.IGNORES per-file contract; non-catalogue additions remain forbidden.
CATALOGUE_PER_FILE = {
    "**/__init__.py": {"F401", "F403", "D104"},
    "__init__.py": {"F401", "F403", "D104"},
    "test_*.py": {"D", "S101", "PLR2004"},
    "**/tests/**": {"D", "S101", "PLR2004"},
    "conftest.py": {"D"},
    "setup.py": {"D"},
}
ROOT = Path(__file__).resolve().parents[2]
SUPPRESSION = re.compile(r"#\s*(?:ruff:\s*)?noqa\b", re.IGNORECASE)


def _comments(source: str) -> list[tuple[str, int]]:
    comments = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT and SUPPRESSION.match(token.string):
                comments.append((token.string.strip(), token.start[0]))
    except (tokenize.TokenError, IndentationError):
        # Tokens already emitted retain their lexical identity even in incomplete files.
        pass
    return comments


def _per_file_entries(patterns: Mapping[str, Sequence[str]]) -> list[tuple[str, str]]:
    return [
        (f"per-file-ignores:{pattern}:{code}", code)
        for pattern, codes in patterns.items()
        for code in codes
    ]


def _config_entries(path: str, source: str) -> list[tuple[str, int]]:
    if Path(path).name not in {"pyproject.toml", "ruff.toml", ".ruff.toml"}:
        return []
    document = tomllib.loads(source)
    config = (
        document.get("tool", {}).get("ruff", {})
        if Path(path).name == "pyproject.toml"
        else document
    )
    entries = []
    for section in (config, config.get("lint", {})):
        for code in section.get("extend-ignore", []):
            entries.append((f"extend-ignore:{code}", code))
        entries.extend(_per_file_entries(section.get("per-file-ignores", {})))
    # TOML strings retain their spelling-independent identity in the parsed document.
    # Locate each rule in its setting, including multiline arrays and inline tables.
    rows = []
    section = ""
    setting = ""
    for row, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("[") and not stripped.startswith("[["):
            section = stripped.split("]", 1)[0][1:]
            setting = ""
        if "=" in line:
            key = line.split("=", 1)[0].strip().strip("\"'")
            setting = "per-file-ignores" if section.endswith("per-file-ignores") else key
        if setting in {"extend-ignore", "per-file-ignores"}:
            for match in re.finditer(r"[\"']([A-Z]+[0-9]*)[\"']", line.split("#", 1)[0]):
                rows.append((setting, match[1], row))
    result = []
    for identity, code in entries:
        setting = identity.split(":", 1)[0]
        row = next((row for kind, value, row in rows if kind == setting and value == code), 1)
        result.append((identity, row))
    return result


def added_suppression_findings(
    base: Mapping[str, str], head: Mapping[str, str], changed_paths: Sequence[str]
) -> list[str]:
    """Compare semantic suppression identities, allowing existing and moved entries."""
    findings = []
    for path in sorted(set(changed_paths)):
        if path not in head:
            continue
        old = base.get(path, "")
        new = head[path]
        if path.endswith(".py"):
            previous = Counter(identity for identity, _ in _comments(old))
            current = _comments(new)
        else:
            previous = Counter(identity for identity, _ in _config_entries(path, old))
            current = _config_entries(path, new)
        for identity, row in current:
            if previous[identity]:
                previous[identity] -= 1
            elif identity.startswith("per-file-ignores:") and (
                identity.rsplit(":", 1)[1]
                in CATALOGUE_PER_FILE.get(identity.split(":", 2)[1], set())
            ):
                continue
            else:
                findings.append(
                    f"{path}:{row}: RUFF.IGNORES added suppression; remove the new suppression and fix the finding"
                )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", default="origin/main")
    args = parser.parse_args()
    try:
        changed = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMR", args.base_ref, "--"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.splitlines()
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.splitlines()
        paths = sorted(set(changed + untracked))
        base = {}
        head = {}
        for path in paths:
            if not path.endswith(".py") and Path(path).name not in {
                "pyproject.toml",
                "ruff.toml",
                ".ruff.toml",
            }:
                continue
            head[path] = (ROOT / path).read_text(encoding="utf-8")
            result = subprocess.run(
                ["git", "show", f"{args.base_ref}:{path}"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                base[path] = result.stdout
        findings = added_suppression_findings(base, head, paths)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"ruff-suppressions: {error}", file=sys.stderr)
        return 2
    for finding in findings:
        print(finding, file=sys.stderr)
    return int(bool(findings))


if __name__ == "__main__":
    sys.exit(main())
