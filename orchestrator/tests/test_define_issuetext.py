"""Code spans (CommonMark)."""

from __future__ import annotations

from raidar.subject.workflow.define_tool import issuetext as texts


def test_code_spans_read_single_and_longer_backtick_runs() -> None:
    text = "run `ls` then `` a`b `` and ``` `` ``` done"

    assert texts.code_spans(text) == ["ls", "a`b", "``"]


def test_blanking_a_span_hides_an_arrow_inside_it() -> None:
    blanked = texts.blank_code("in -> ``a -> `b` ``")

    assert blanked.count("->") == 1
    assert len(blanked) == len("in -> ``a -> `b` ``")
