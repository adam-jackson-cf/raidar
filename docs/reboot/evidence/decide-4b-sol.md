Q1: astra (high) — Adopt (a), including Astra’s naming, coverage and optional pre-registered errata amendments.
  Dispute settled: flags do combine static and dynamic evidence (`audit.py:_Combiner.classify_case`), but that is not proof of specification conformance.
  The frozen shim replaces error details with “request rejected”; Forge’s hash-verified checkpoint-1 spec requires details describing failing constraints.
  `determinacy.py:classify` is explicitly syntactic. Broader assertion coverage does not resolve that semantic limitation.
  Pinned records confirm 25 Forge flags, all retained, and six problems with zero audited assertions. Forge errata match those flags; agreement does not independently validate automatic removal.

Q2: astra (high) — Adopt (a): five reference repeats; report “no variation observed in 5 runs,” limited to that reference and environment.
  Under independent 10% failure probability, reference admission falls from 72.9% to 59.049%; neither establishes determinism.
  Opus’s 1% double-failure calculation is correct, but discarding disagreements credits success with probability 99% rather than 90%.
  [INFERENCE] That changes the outcome to reward some submission instability; regrading only failed steps retains this problem.
  Dispute settled: datagate records the cited cross-reference variation, but is excluded. Removing its negative-repeat check would not restore it: `reference_k-1_fails_k` also fails.

Q3: astra (high) — All eleven listed fixes in 4b, after phase 4 and before phase 9; defer none. Adopt Astra’s amendments in full:
  #1 covers aggregate verdicts and selects binary methods from locked outcome support, not observed values.
  #3 pre-registers material exclusion differences; nonsignificance alone does not establish acceptable attrition.
  #5 binds immutable grader environment and implementation identities, not merely tags.
  #6 expects starter grading completion, not task success; a passing canary supplies attribution evidence, not immunity from input-dependent grader bugs.
  #7 bounds and records retries; exhaustion remains infrastructure, never an agent score.
  Opus’s proposed deferrals include substantive safeguards: frozen P8 ignores instruction origins; first-rework duration defaults to zero; resource cost reads agent usage only (`isolation.py`, `judgeloop.py`, `resource_usage/scorer.py`).
  Dispute settled: aggregate verdicts also use continuous intervals (`effects.py:aggregate_effect`). Frozen statistical probes give McNemar p=0.0625 for five one-way pairs and 0.03125 for six; a blanket zero-spread prohibition is wrong.
  Verification: frozen `712bf3d` source, pinned records and in-memory probes only. No files edited; no Docker or trials run.
