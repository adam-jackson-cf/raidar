"""``gate/1`` attribution of a gate record: pure decisions over records, no checks run."""

from __future__ import annotations

from raidar.subject.primitive.gate import Attribution, attribute


def test_changed_tool_bytes_or_a_missing_record_are_integrity_failures() -> None:
    assert attribute({"verdict": "passed", "checks": []}, tools_intact=False)[0] is (
        Attribution.INTEGRITY
    )
    nothing = {"verdict": "error", "error": "no record", "checks": []}
    attribution, reason = attribute(nothing)
    assert attribution is Attribution.INTEGRITY
    assert "no record" in reason


def test_passed_and_refused_records_keep_their_verdicts() -> None:
    assert attribute({"verdict": "passed"})[0] is Attribution.PASSED
    assert attribute({"verdict": "refused"})[0] is Attribution.REFUSED
    assert Attribution.REFUSED.value == "refused"
