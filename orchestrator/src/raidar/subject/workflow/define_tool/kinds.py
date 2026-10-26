"""The kinds of fault the intake's checks find in a feature definition.

Every error of an intake check (``compact.check``) carries one ``Kind``. Counts
go by kind and never by message text, so rewording a message cannot silently
change a measure.

A kind says what is wrong, not whether it blocks: that is the finding's own
``advisory`` flag.
"""

from __future__ import annotations

from enum import StrEnum


class Kind(StrEnum):
    # The intake's reply: its replies, questions and features' evidence.
    REPLY = "reply"
    # A line of a `define put` block the tool could not read (``define``'s grammar).
    GRAMMAR = "grammar"
    # The definition's sections, features, ids, outcomes and verification lines.
    STRUCTURE = "structure"
    # A criterion line outside every `Acceptance criteria:` list.
    UNLISTED_CRITERION = "unlisted_criterion"
    # A `Source:` quote that is not the issue's own words.
    QUOTE_VERBATIM = "quote_verbatim"
    # A criterion with no `Source:` line.
    QUOTE_MISSING = "quote_missing"
    # The shape of a criterion's `Aspect:`, `Example:` and `Kind:` lines (blocking).
    ASPECT_SHAPE = "aspect_shape"
    # A value the criterion or its quotes state that no line carries (advisory).
    VALUE_LITERAL = "value_literal"
    # A normative sentence of the issue that nothing quotes (advisory under unit
    # rules 1, blocking under 2: ``unitrules``).
    NORMATIVE_UNDISPOSED = "normative_undisposed"
    # What the intake still owes the acceptance probes (``capability.accept``): an
    # example for an arrow outcome, one per labelled-list item, one for an example
    # section no probe can be derived from.
    PROBE_ARROW = "probe.arrow"
    PROBE_LIST = "probe.list"
    PROBE_UNDERIVABLE = "probe.underivable"
