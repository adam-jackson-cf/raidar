"""Markdown code spans of an issue's text, as the aspect checks read literals.

A leaf: it imports nothing of the loops.
"""

from __future__ import annotations

import re

# A markdown code span (CommonMark): a run of backticks, the text, and a closing run
# of exactly as many. A span that holds a backtick is fenced by a longer run.
CODE_SPAN = re.compile(r"(?<!`)(?P<fence>`+)(?!`)(?P<body>[^\n]+?)(?<!`)(?P=fence)(?!`)")


def _span_body(body: str) -> str:
    """Return a code span's content, without the one space of padding on each side."""
    if len(body) > 2 and body[0] == body[-1] == " " and body.strip():
        return body[1:-1]
    return body


def code_spans(text: str) -> list[str]:
    """Return the content of every code span in ``text``, in order."""
    return [_span_body(found.group("body")) for found in CODE_SPAN.finditer(text)]


def blank_code(text: str) -> str:
    """Return ``text`` with every code span (backticks included) blanked, same length."""
    return CODE_SPAN.sub(lambda found: "\x00" * len(found.group()), text)
