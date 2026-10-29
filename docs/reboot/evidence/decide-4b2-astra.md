Q1: (b) (med) — No default. Before data collection, each experiment must lock `analysis.max_exclusion_difference = d`.
  Refuse comparative verdicts when max(arm exclusion rates) − min(arm exclusion rates) > d; equality is allowed.
  Each rate is that arm’s excluded trials / planned trials. Missing d means descriptive-only analysis; retain the independent 20% paired-exclusion cap.
  `evaluation/analysis/effects.py:exclusion_refusal` already implements this rule.
  The audits establish the need for a bound, not a defensible universal number; statistical nonsignificance cannot establish acceptable attrition.

Q2: (b) (med) — One immediate retry after a resolver timeout: at most two attempts per declared set and architecture, each capped at 120 seconds.
  Retry identical requirements under the same pinned policy; record attempts. A second timeout is infrastructure, never `cap-exceeded` or an agent score.
  `grading/resolver.py:_resolve_with_retries` already supports this bounded setting.
  One retry permits recovery without the extra delay of three attempts. This is a pragmatic bound, not an empirically calibrated optimum.

Q3: (a) (high) — With the fit rule enabled, no first rework starts without an available `first_build` duration estimate; introduce no fallback floor.
  Otherwise require estimated turn + last gate + last judge duration ≤ remaining time.
  `subject/workflow/stage.py:expected_seconds` and `judgeloop.py:fits` implement the refusal; the in-memory probe confirmed the missing estimate.
  No reviewed evidence supports a positive fallback duration. Inventing one would weaken the protection against grading a deadline-interrupted edit.

Q4: (b) (high) — Advance after a step explicitly excluded by pinned validation; it is neither pass nor fail and is omitted from solvable-score denominators.
  Execute its task normally, preserving its workspace changes and normal budget; bypass only its halt gate, not the task.
  Missing validation, grading infrastructure failures and unexplained missing rewards must still fail closed—not become exclusions.
  `scoring/correctness/scorer.py:_skip_excluded` already excludes these steps from scoring, while `engine/trial/progression.py:gate_passes` currently halts.
  Align progression with scoring and §3.2: excluding one step must not make its usable neighbours unreachable.
