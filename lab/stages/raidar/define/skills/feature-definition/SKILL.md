---
name: "feature-definition"
description: "USE WHEN turning an issue into a feature definition: settling what the issue leaves open, handling conflicts with earlier accepted behaviour and retired tests, and deciding whether anything must be asked."
---

# Feature definition

## Settle every gap yourself

Work down this ladder for every gap and stop at the first rung that gives an
answer; a decision names its rung.

1. **Keep what earlier requirements accepted, and follow the repository's own
   guidance.** Behaviour that earlier requirements established and that the
   repository's tests assert is accepted: it is protected, and the change keeps
   those tests passing. Existing code is evidence of how things are done, not
   proof that they are right: code that contradicts the issue or an earlier
   requirement's tests may change. Follow the patterns of the code the change
   touches and of similar features, the agent and contributor guidance
   (`AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING`, READMEs, docs) and the quality
   gates (lint, format and type-check configs, pre-commit hooks, CI). These
   decide every detail the issue leaves open that they touch: formats, error
   handling, naming, structure, test style.
2. **Follow the issue's own words,** read literally: its examples, tables and
   stated values.
3. **Follow the applicable standard:** HTTP semantics, a format's
   specification, a library's documented behaviour.
4. **Follow best practice** for the task, runtime and language, weighed across
   the whole repository: a locally "better" pattern that diverges from the
   codebase, duplicates what exists or leaves two ways of doing one thing is
   not best practice here.
5. **Otherwise, choose the simplest option that satisfies the acceptance
   criteria.**

These are always yours to decide, however low your confidence, and never a
question: values and thresholds (timeouts, limits, sizes, defaults); ordering
and tie-breaks; error and edge-case behaviour (invalid input, missing or
unreadable files, unavailable dependencies); message text and error codes the
issue does not give; data types, formats and parsing; internal structure,
naming, libraries within the stated stack, the test approach; how to create
something the issue implies but does not specify (a column, a file, a config
key, a helper).

Record a decision only for what a builder could not get right without being
told: a detail settled at rungs 2–5 without direct evidence (an assumption also
says how to reverse it), a conflict with earlier accepted behaviour, a
retirement. Following the repository's guidance at rung 1 needs no record: the
builder reads the repository. A conflict with earlier accepted behaviour is
recorded, even though it is settled at rung 1 (see the next section). One short
line each.

## When the issue and earlier accepted behaviour disagree

Earlier acceptance is the truth. When the issue contradicts behaviour an
earlier requirement accepted and the repository's tests assert ("currently",
"as before", "still", or a plain statement of it), keep the earlier behaviour
unless the issue explicitly states the change ("change X to Y", "replace", "no
longer"). Record it as a rung-1 decision with risk `requires review`, citing
the issue's words and the `path:line` that asserts the earlier behaviour, and
mark the feature `requires review`. Code that no test or earlier requirement
holds in place is not accepted behaviour: where it contradicts the issue, the
issue wins.

- The issue says greenhouse misting already runs hourly, but `mister.py:12`
  sets twenty minutes and `test_mister.py:8` asserts it: keep twenty minutes,
  define nothing that changes it, and record the conflict for review.
- The issue says the misting log keeps a week, `mister.py:30` keeps a day and
  nothing asserts a day: the issue wins; define the week.

Never widen or narrow accepted behaviour because the new feature would be
simpler that way.

When the issue explicitly changes accepted behaviour, the tests asserting the
old behaviour must stop passing: retire each one by its test id as the runner
names it (`tests/test_mister.py::test_interval`), citing the words that change
it and why. A retirement is not also a decision. The verify gate lets exactly
the retired tests stop passing and refuses any other test that passed before.

## Ask only what blocks a feature

Ask only when a feature's outcome, or a criterion's intent, cannot be written
without the answer:

- `objective_unclear`: the objective, or what a named feature is for, is
  missing or could mean materially different features. Every feature,
  operation, endpoint, option, control or section the issue names (headings,
  summary and lists included) needs its behaviour stated by the issue, the code
  or the tests; a heading or passing mention states nothing. Never drop a named
  feature and never invent what it does.
- `feature_conflict`: two stated objectives or criteria cannot both hold.
- `missing_input`: something you cannot invent, such as credentials or an
  external system that is named but absent.

Each question carries one concrete proposal (the answer you would build with)
and ready alternatives that do not restate it. Most issues have none.

- Ask: an "Archive" feature that never says what is archived or for whom
  (`objective_unclear`, proposing the reading the rest of the issue supports);
  loyalty points kept forever in one criterion and expiring after a year in
  another (`feature_conflict`, proposing which wins).
- Decide, never ask: the rounding of a recipe scaler (follow how the code
  formats quantities); leaderboard ties (follow existing ordering); an
  unreadable schedule file (follow how the code reports unreadable files).
