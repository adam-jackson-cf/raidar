"""The unit rules: which statements of an issue the intake must cite, by version.

A workflow names its unit rules version (``[intake] unit_rules``) and the engine
reads the issue with exactly that rule set (``normative`` cuts and signals the
units, ``compact`` numbers them and checks the intake's reply against them). A
rule set is frozen once a run is measured with it: a changed reading is a new
version, never an edit, and the version a definition names is part of its hash
from version 2 on (``engine.workflow.config.definition_sha256``; version 1 is
the reading every earlier hash already stood for).

- **Version 1.** A statement is normative by one of the signals of
  ``normative.signals`` (keyword, imperative, conditional, behaviour, definition,
  contract); a required unit no criterion cites is advisory (a finding the
  intake is shown and may leave, and that ``define done`` does not refuse).
- **Version 2.** Version 1, and: a statement that states an outcome with an
  arrow (``X → Y``, the arrow outside a code span) is normative (signal
  ``outcome``); a labelled list of backticked items (``labelled.labelled_list``)
  is a required, citable unit however few words it holds (signal
  ``enumeration``), so a definition has to cite the whole set; and a required
  unit no criterion cites blocks ``define done``. Since a unit that blocks must
  be citable, a normative statement that fails the ``Source:`` quote check (its
  words first occur in a heading, say) is not required under version 2 (it stays
  required, and advisory, under version 1) unless it is a labelled list.

A leaf: it imports nothing of the loops.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UnitRules:
    """One frozen rule set: its version and what it adds to the first."""

    version: int
    arrow_outcome: bool
    lists: bool
    uncovered_blocks: bool


V1 = UnitRules(1, arrow_outcome=False, lists=False, uncovered_blocks=False)
V2 = UnitRules(2, arrow_outcome=True, lists=True, uncovered_blocks=True)
VERSIONS = {rules.version: rules for rules in (V1, V2)}
