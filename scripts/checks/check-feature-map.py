#!/usr/bin/env python3
"""Check the feature map under ``docs/features`` and the review-surface journeys.

SURFACE.feature-map: every Markdown file in the feature map except ``README.md`` documents one
feature. It has exactly one H1, the feature name, unique across the map, and exactly these H2s
in this order, each with a non-empty body (backticks are ignored when comparing headings):
``Sub-features``, ``How to get to it (user POV)``, ``Driving it with <lever>`` and ``Gotchas``.
In every map file, including ``README.md``, relative links and reference definitions resolve to
repository files, and ``#anchors`` name a heading of the target Markdown file.

SURFACE.journeys: every file under ``review-surface/journeys`` matches
``review-surface/journeys/*.journey.mjs``. Each journey references the lever (the lever command,
its script path or its script name) and is linked or named by repository path from a map file.

Paths are the tracked files plus untracked files that are not git-ignored.
"""

from __future__ import annotations

import argparse
import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from git_inventory import repo_paths

ROOT = Path(__file__).resolve().parents[2]
FEATURE_MAP = "docs/features"
INDEX_FILES = frozenset({"README.md"})
LEVER_COMMAND = "node scripts/surface/lever.mjs"
JOURNEY_PATTERN = "review-surface/journeys/*.journey.mjs"
SECTIONS = ("Sub-features", "How to get to it (user POV)", "Driving it with {lever}", "Gotchas")
MAP_RULE = "SURFACE.feature-map"
JOURNEY_RULE = "SURFACE.journeys"

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
INLINE_LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'(][^)]*)?\)")
REFERENCE_LINK = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s+.*)?$")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


@dataclass
class Document:
    """A Markdown file's headings, section bodies and link targets, outside code fences."""

    headings: list[tuple[int, str]] = field(default_factory=list)
    bodies: dict[int, list[str]] = field(default_factory=dict)
    links: list[str] = field(default_factory=list)


def finding(path: str, rule: str, problem: str, repair: str) -> str:
    """Render one single-line finding."""
    return f"{path}: {rule}: {problem}; {repair}"


def normalise(text: str) -> str:
    """Compare headings without backticks, case or repeated spaces."""
    return " ".join(text.replace("`", "").split()).casefold()


def parse(text: str) -> Document:
    """Read headings, bodies and links; fenced code counts as body but holds no links."""
    document = Document()
    in_fence = False
    current = -1
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            if current >= 0:
                document.bodies[current].append(line)
            continue
        heading = HEADING.match(line)
        if heading:
            document.headings.append((len(heading.group(1)), heading.group(2)))
            current = len(document.headings) - 1
            document.bodies[current] = []
            continue
        if current >= 0:
            document.bodies[current].append(line)
        document.links.extend(INLINE_LINK.findall(line))
        reference = REFERENCE_LINK.match(line)
        if reference:
            document.links.append(reference.group(1))
    return document


def slug(heading: str) -> str:
    """GitHub-style heading anchor."""
    text = heading.replace("`", "").strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def resolve_link(doc: str, target: str) -> tuple[str | None, str]:
    """The repository path a relative link names (None for the same file) and its anchor."""
    path_part, _, anchor = target.partition("#")
    if not path_part:
        return None, anchor
    path_part = unquote(path_part.split("?", 1)[0])
    if path_part.startswith("/"):
        joined = PurePosixPath(path_part.lstrip("/"))
    else:
        joined = PurePosixPath(doc).parent / path_part
    parts: list[str] = []
    for part in joined.parts:
        if part == "..":
            if not parts:
                return "..", anchor
            parts.pop()
        elif part != ".":
            parts.append(part)
    return "/".join(parts) or ".", anchor


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def present(resolved: str, inventory: frozenset[str]) -> bool:
    """A link target is a listed file or a directory holding one."""
    if resolved == ".":
        return True
    return resolved in inventory or any(path.startswith(f"{resolved}/") for path in inventory)


def link_findings(root: Path, doc: str, document: Document, inventory: frozenset[str]) -> list[str]:
    found = []
    own_slugs = {slug(text) for _, text in document.headings}
    for target in document.links:
        if SCHEME.match(target):
            continue
        resolved, anchor = resolve_link(doc, target)
        if resolved == ".." or (resolved is not None and not present(resolved, inventory)):
            found.append(
                finding(doc, MAP_RULE, f"link '{target}' does not resolve", "fix the link")
            )
            continue
        if not anchor:
            continue
        if resolved is None:
            slugs = own_slugs
        elif resolved.endswith(".md") and resolved in inventory:
            slugs = {slug(text) for _, text in parse(read(root / resolved)).headings}
        else:
            continue
        if anchor.lower() not in slugs:
            found.append(
                finding(doc, MAP_RULE, f"link '{target}' names a missing heading", "fix the anchor")
            )
    return found


def section_lines(document: Document, index: int) -> list[str]:
    """A heading's body including deeper sub-headings, up to the next heading at its level."""
    level = document.headings[index][0]
    lines = list(document.bodies[index])
    for later in range(index + 1, len(document.headings)):
        if document.headings[later][0] <= level:
            break
        lines.append(document.headings[later][1])
        lines.extend(document.bodies[later])
    return lines


def section_findings(doc: str, document: Document) -> list[str]:
    expected = [section.format(lever=LEVER_COMMAND) for section in SECTIONS]
    found = []
    titles = [text for level, text in document.headings if level == 1]
    if len(titles) != 1:
        found.append(
            finding(
                doc,
                MAP_RULE,
                f"has {len(titles)} H1 headings, a feature document has one, the feature name",
                "give the document exactly one H1 naming its feature",
            )
        )
    h2 = [(index, text) for index, (level, text) in enumerate(document.headings) if level == 2]
    if [normalise(text) for _, text in h2] != [normalise(text) for text in expected]:
        found.append(
            finding(
                doc,
                MAP_RULE,
                f"H2 headings are {[text for _, text in h2]}, expected exactly {expected}",
                "use exactly the four feature-map H2s, in order",
            )
        )
        return found
    for index, text in h2:
        if not any(line.strip() for line in section_lines(document, index)):
            found.append(finding(doc, MAP_RULE, f"section '{text}' is empty", "fill the section"))
    return found


def map_documents(inventory: Sequence[str]) -> list[str]:
    return sorted(
        path for path in inventory if path.startswith(f"{FEATURE_MAP}/") and path.endswith(".md")
    )


def feature_map_findings(root: Path, inventory: Sequence[str]) -> list[str]:
    """SURFACE.feature-map over every map file."""
    documents = map_documents(inventory)
    if not documents:
        return [
            finding(
                FEATURE_MAP,
                MAP_RULE,
                "the feature map holds no documents",
                "write one document per feature",
            )
        ]
    listed = frozenset(inventory)
    found: list[str] = []
    owners: dict[str, str] = {}
    for doc in documents:
        document = parse(read(root / doc))
        found.extend(link_findings(root, doc, document, listed))
        if PurePosixPath(doc).name in INDEX_FILES:
            continue
        found.extend(section_findings(doc, document))
        for level, text in document.headings:
            if level != 1:
                continue
            key = normalise(text)
            if key in owners:
                found.append(
                    finding(
                        doc,
                        MAP_RULE,
                        f"feature '{text}' is also documented in {owners[key]}",
                        "keep one document per feature",
                    )
                )
            else:
                owners[key] = doc
    return found


def lever_references() -> list[str]:
    """The lever command, plus each path-like token of it and that token's file name."""
    references = [LEVER_COMMAND]
    for token in shlex.split(LEVER_COMMAND):
        if "/" in token or (token.count(".") and not token.startswith("-")):
            references.extend((token, PurePosixPath(token).name))
    return list(dict.fromkeys(references))


def mapped_paths(root: Path, inventory: Sequence[str]) -> tuple[set[str], str]:
    """Files the feature map links to, and the map's combined text for path mentions."""
    linked: set[str] = set()
    texts = []
    for doc in map_documents(inventory):
        text = read(root / doc)
        texts.append(text)
        for target in parse(text).links:
            if SCHEME.match(target):
                continue
            resolved, _ = resolve_link(doc, target)
            if resolved and resolved != "..":
                linked.add(resolved)
    return linked, "\n".join(texts)


def journey_findings(root: Path, inventory: Sequence[str]) -> list[str]:
    """SURFACE.journeys over every file in the journey directory."""
    directory = PurePosixPath(JOURNEY_PATTERN).parent.as_posix()
    in_directory = [path for path in inventory if path.startswith(f"{directory}/")]
    journeys = [path for path in in_directory if fnmatchcase(path, JOURNEY_PATTERN)]
    references = lever_references()
    linked, map_text = mapped_paths(root, inventory)
    found = [
        finding(
            path,
            JOURNEY_RULE,
            f"file in the journey directory does not match '{JOURNEY_PATTERN}'",
            "rename it to the journey pattern or move it out of the journey directory",
        )
        for path in in_directory
        if path not in journeys
    ]
    for journey in journeys:
        text = read(root / journey)
        if not any(reference in text for reference in references):
            found.append(
                finding(
                    journey,
                    JOURNEY_RULE,
                    f"journey does not reference the lever (looked for {references})",
                    "drive the feature through the lever",
                )
            )
        if journey not in linked and journey not in map_text:
            found.append(
                finding(
                    journey,
                    JOURNEY_RULE,
                    "journey is not linked from any feature-map document",
                    "link the journey from its feature's 'Driving it with' section",
                )
            )
    return found


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    inventory = repo_paths(args.repo_root)
    findings = [
        *feature_map_findings(args.repo_root, inventory),
        *journey_findings(args.repo_root, inventory),
    ]
    for line in findings:
        print(line)
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
