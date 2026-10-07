"""The code-quality versions that analysis and the review surfaces read, newest first.

``code-quality@2`` is ``code-quality@1`` whose G also reads an offline coverage
run (``raidar.scorers.code_quality_v2``), and ``code-quality@3`` is ``@2`` whose G
leaves out a few flipped cases instead of the step (``raidar.scorers.code_quality_v3``,
which adds one diagnostic metric); all report the same quality metrics, so a
consumer reads whichever version a trial was assessed with, preferring the
newer one, in the quality slot it has always read ``code-quality@1`` into.
"""

from __future__ import annotations

from collections.abc import Iterable

CODE_QUALITY_REFS = ("code-quality@3", "code-quality@2", "code-quality@1")


def code_quality_ref(declared: Iterable[str]) -> str:
    """The code-quality version ``declared`` names (newest first), else ``code-quality@1``."""
    names = set(declared)
    return next((ref for ref in CODE_QUALITY_REFS if ref in names), CODE_QUALITY_REFS[-1])


__all__ = ["CODE_QUALITY_REFS", "code_quality_ref"]
