Q1: (a) (high) — Keep a fixed, generic benchmark outcome; do not promote heuristic flags into grading authority.
Verified: all 25 Forge flags remain in the solvable sets; six problems have zero audited assertions.
But assertion-style coverage alone does not establish semantic correctness: `determinacy.classify` uses syntactic rules, and `variant_shim.neutralise` replaces error details with “request rejected.”
Automatically removing whole cases can also remove legitimate behavioral checks.
Label the primary “reference-solvable SCB success,” not specification compliance; show flags and coverage, with zero audited = `unmeasured`. Keep adjudicated errata optional and pre-registered.

Q2: (a) (high) — Five reference repeats improve screening without making each submission determine its own scored set.
Under independent 10% flakiness, admission probability falls from 72.9% to 59.0%; five passes still do not establish determinism.
Reject (b): disagreement-based removal conditions scoring on the submission’s outcomes and rewards some instability.
For one such case, two grades disagree 18% of the time but both fail only 1%; discarding disagreements hides failures rather than merely measuring noise.
Report “no variation observed in 5 runs,” explicitly limited to that reference and environment.

Q3: (a) (high) — All eleven listed fixes belong in 4b, after phase 4 and before phase 9; defer none.
Verified code paths support the verdict, attrition, identity, attribution, provenance, timing and accounting concerns; the reporting/variant fixes prevent misleading validation evidence.
Exact amendments:
#1: Cover aggregate verdicts too; select binary methods from locked outcome support, not merely observed 0/1 values.
#3: Pre-register a material exclusion-rate difference; nonsignificance alone must not establish acceptable attrition.
#5: Bind immutable grader environment and implementation identities, not merely a Docker tag.
#6: Canary success means expected grading completion on the starter—not that the starter solves the task; a passing canary is attribution evidence, not proof against input-dependent grader bugs.
#7: Bound and record resolver retries; exhaustion remains infrastructure, never an agent score.

Audit findings I believe are wrong:

- **Opus A1’s proposed blanket prohibition on verdicts with zero-spread deltas.** Executing frozen `evaluation/analysis/stats.py:mcnemar_exact` gives p=0.0625 for five treatment-only successes, but p=0.03125 for six. Six identical +1 deltas can support an exact verdict. Reject the degenerate continuous interval, not every zero-spread sample.
- **Opus A8: “every verdict is validated_partial.”** At `712bf3d`, `lab/problems/registry/scb/{datagate,mocked_http}/38d627ecf668.validation.json` both say `excluded`; seven are `validated_partial`. Native parity is indeed unrun on all nine. Astra’s seven-report count is correct.
- **Opus A2: “Every *.validation.json … flagged: 0.”** Forge’s pinned report records `spec_determinacy.summary.flagged = 25`. The six zero-coverage reports do misleadingly report zero flags; that substantive finding stands.

Verification was read-only: frozen source, pinned records, retained Forge grading evidence and an in-memory statistical probe. No files edited; no Docker or trials run.
