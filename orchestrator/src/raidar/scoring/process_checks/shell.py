"""Static shell commands and the control flow that makes their execution uncertain.

Parse shell syntax rather than treating punctuation inside quoted arguments as
command boundaries. This establishes syntax, not branch outcomes: conditional
commands are possible executions, and function definitions are not executions.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterator
from dataclasses import dataclass

from tree_sitter import Node
from tree_sitter_language_pack import get_parser


@dataclass(frozen=True)
class ShellCommand:
    offset: int
    text: str
    conditional: bool = False


def _conditional(node: Node) -> bool:
    """Control flow whose execution cannot be established from command text alone."""
    parent = node.parent
    while parent is not None:
        if parent.type in {
            "if_statement",
            "elif_clause",
            "else_clause",
            "case_statement",
            "while_statement",
            "for_statement",
            "c_style_for_statement",
            "pipeline",
        }:
            return True
        if parent.type == "list" and node != parent.named_children[0]:
            return True
        node, parent = parent, parent.parent
    return False


def _commands(node: Node) -> Iterator[Node]:
    if node.type == "function_definition":
        return
    if node.type == "command":
        yield node
    for child in node.named_children:
        yield from _commands(child)


def _literal_words(node: Node) -> list[str] | None:
    """Static command words, without executing interpolation or substitutions."""
    try:
        return shlex.split((node.text or b"").decode("utf-8"), comments=True)
    except ValueError:
        return None


def _may_stop(words: list[str]) -> bool:
    """Following commands need execution evidence after exits or enabling errexit."""
    if words[0] in {"exit", "return", "exec"}:
        return True
    if words[0] != "set":
        return False
    flags = words[1:]
    return any(re.fullmatch(r"-[a-zA-Z]*e[a-zA-Z]*", word) for word in flags) or (
        "-o" in flags and "errexit" in flags
    )


def _shell_script(words: list[str]) -> str | None:
    """A literal shell -c operand, never a script-file name or another option."""
    if words[0] not in {"sh", "bash", "zsh"} or len(words) < 3:
        return None
    return words[2] if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", words[1]) else None


def _invocations(
    offset: int, words: list[str], conditional: bool, raw: str
) -> Iterator[ShellCommand]:
    script = _shell_script(words)
    if script is None:
        yield ShellCommand(offset, shlex.join(words), conditional)
        return
    # Shell -c executes its operand; interpolated scripts still need execution evidence.
    conditional = conditional or "$" in script or "`" in script
    script_offset = raw.find(script)
    if script_offset < 0:
        # Escaped or concatenated operands have no direct source-position mapping.
        conditional, script_offset = True, len(raw)
    for nested in shell_commands(script):
        yield ShellCommand(
            offset + script_offset + nested.offset,
            nested.text,
            conditional or nested.conditional,
        )


def shell_commands(text: str) -> Iterator[ShellCommand]:
    """Commands in source order, preserving uncertainty about shell control flow."""
    source = text.encode("utf-8")
    tree = get_parser("bash").parse(source)
    may_have_stopped = False
    for node in _commands(tree.root_node):
        words = _literal_words(node)
        if not words:
            continue
        conditional = tree.root_node.has_error or may_have_stopped or _conditional(node)
        # Other classifier offsets index Python strings, not UTF-8 bytes.
        offset = len(source[: node.start_byte].decode("utf-8"))
        raw = (node.text or b"").decode("utf-8")
        yield from _invocations(offset, words, conditional, raw)
        may_have_stopped = may_have_stopped or _may_stop(words)
