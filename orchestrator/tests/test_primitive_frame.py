"""``frame/1``: one slot per enabled package plus the task, filled in a single pass."""

from __future__ import annotations

from typing import Any

import pytest

from raidar.subject.primitive.frame import FrameError, FramePart, render_frame, token_name

BUILD = "Do the work.\n\n%%%TASK%%%\n\nMethod:\n%%%REVIEW_NOTES%%%\n\nAlso:\n%%%DOCS_CHECK%%%\n"


def part(name: str, fragment: str, **kwargs: Any) -> FramePart:
    return FramePart(name, fragment, **kwargs)


def two() -> list[FramePart]:
    return [part("review-notes", "Keep notes."), part("docs-check", "Check the docs.")]


def test_each_package_fills_its_own_slot_and_the_task_is_verbatim() -> None:
    parts = [
        part(
            "review-notes",
            "Keep notes. %%%GUIDANCE%%% in %%%MAX_WORDS%%% words.",
            params={"max-words": 40},
            tokens={"GUIDANCE": "Be brief."},
        ),
        part("docs-check", "Check the docs."),
    ]
    framed = render_frame(BUILD, "Fix %%%GUIDANCE%%% and %%%REVIEW_NOTES%%%", parts)
    assert framed.prompt == (
        "Do the work.\n\nFix %%%GUIDANCE%%% and %%%REVIEW_NOTES%%%\n\n"
        "Method:\nKeep notes. Be brief. in 40 words.\n\nAlso:\nCheck the docs.\n"
    )
    assert framed.fragments == {
        "REVIEW_NOTES": "Keep notes. Be brief. in 40 words.",
        "DOCS_CHECK": "Check the docs.",
    }


def test_values_are_not_interpreted_again() -> None:
    parts = [
        part("review-notes", "Say: %%%GUIDANCE%%%", tokens={"GUIDANCE": "%%%DOCS_CHECK%%%"}),
        part("docs-check", "docs"),
    ]
    framed = render_frame(BUILD, "task", parts)
    assert "Say: %%%DOCS_CHECK%%%" in framed.prompt


def test_param_values_render_as_json_text_except_strings() -> None:
    parts = [part("review-notes", "%%%A%%% %%%B%%% %%%C%%%", params={"a": True, "b": 3, "c": "x"})]
    framed = render_frame("%%%TASK%%%%%%REVIEW_NOTES%%%", "t", parts)
    assert framed.prompt == "ttrue 3 x"


@pytest.mark.parametrize(
    "build",
    [
        "no task slot %%%REVIEW_NOTES%%% %%%DOCS_CHECK%%%",
        "%%%TASK%%% %%%TASK%%% %%%REVIEW_NOTES%%% %%%DOCS_CHECK%%%",
        "%%%TASK%%% %%%DOCS_CHECK%%%",
        "%%%TASK%%% %%%REVIEW_NOTES%%% %%%REVIEW_NOTES%%% %%%DOCS_CHECK%%%",
        "%%%TASK%%% %%%REVIEW_NOTES%%% %%%DOCS_CHECK%%% %%%ELSEWHERE%%%",
    ],
)
def test_missing_repeated_or_unknown_slots_are_refused(build: str) -> None:
    with pytest.raises(FrameError):
        render_frame(build, "task", two())


def test_a_slot_for_a_disabled_package_is_unknown() -> None:
    with pytest.raises(FrameError, match="unknown slots: DOCS_CHECK"):
        render_frame(BUILD, "task", two()[:1])


def test_packages_whose_names_collide_are_refused() -> None:
    parts = [part("review-notes", "a"), part("review_notes", "b")]
    with pytest.raises(FrameError, match="share a slot"):
        render_frame("%%%TASK%%% %%%REVIEW_NOTES%%%", "t", parts)


def test_a_package_named_task_cannot_shadow_the_task_slot() -> None:
    with pytest.raises(FrameError, match="reserved"):
        render_frame("%%%TASK%%%", "t", [part("task", "fragment")])


def test_unresolved_and_colliding_tokens_are_refused() -> None:
    with pytest.raises(FrameError, match="NOPE"):
        render_frame("%%%TASK%%% %%%A%%%", "t", [part("a", "%%%NOPE%%%")])
    clash = part("a", "x", params={"word": "w"}, tokens={"WORD": "v"})
    with pytest.raises(FrameError, match="collide"):
        render_frame("%%%TASK%%% %%%A%%%", "t", [clash])


def test_a_fragment_cannot_read_the_task_slot() -> None:
    with pytest.raises(FrameError, match="TASK"):
        render_frame("%%%TASK%%% %%%A%%%", "t", [part("a", "%%%TASK%%%")])


def test_instructions_join_in_workflow_order_and_disabled_packages_add_none() -> None:
    parts = [
        part("review-notes", "a", instructions="First.\n"),
        part("docs-check", "b"),
        part("zed", "c", instructions="Third."),
    ]
    framed = render_frame(BUILD + "%%%ZED%%%", "t", parts)
    assert framed.instructions == "First.\n\nThird."
    assert render_frame(BUILD, "t", two()).instructions == ""


def test_slot_names_uppercase_with_underscores() -> None:
    assert token_name("review-notes") == "REVIEW_NOTES"
