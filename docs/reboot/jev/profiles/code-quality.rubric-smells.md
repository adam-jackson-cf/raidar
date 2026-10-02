# Profile: code-quality.rubric-smells

Binds Raidar's SCB-rubric Jev process to the evaluate-jev rubric
(`~/.omp/agent/skills/evaluate-jev/references/rubric.md`). Built 2026-10-02 from the code at
`reboot-impl` HEAD `73adb9a` and the audit in `jobs/pilot-v2/assessment-store`. Each field is
marked **observed** (read from code or records), **stated** (given by the user) or **open**
(proposed by the evaluator; needs the user's decision before it can be treated as stated).

- **Process:** For each checkpoint snapshot of a trial, every production Python symbol is asked
  the applicable SCB rubric criteria as Jev `noul` questions, one question per criterion
  (`orchestrator/src/raidar/jev/rubric.py` `RubricRunner`, questions from
  `jev/rubric_questions.py`, criteria vendored byte-for-byte from SCB
  `configs/rubrics/llm_judge.jsonl` and pinned by sha256 in
  `jev/question_sets/scb-rubric.yaml`). Policy file
  `jev/policies/code-quality.rubric-smells.yaml`, `mode: shadow`, `question_set: scb-rubric@2`,
  `runtimes: [python]`. Switched by the policy mode (`off | shadow | enforce`); a run may only lower
  it. Run through `make jev-shadow-rubric SNAPSHOTS=… STORE=…` (no scorer consumes it yet:
  `code-quality@1` does not bind this policy). (observed)
- **Cases:** One case per audited decision record. Exporter (not yet in the repo):
  `python3 <scratchpad>/jev-eval/export_cases.py jobs/pilot-v2/assessment-store <out>` writes
  `rubric-cases.jsonl` (all records, no state), `rubric-labelled.jsonl` (labelled and held-back
  decisions with state and questions) and `rubric-labels-consensus.jsonl` (current labels after
  supersessions, converted to reference labels). Mapping: `id` = `requestId`; `version` =
  `code-quality.rubric-smells@<policyVersion>/<questionSet>`; `verdict` = `flag@0.5` / `clear@0.5`
  for `stage: jev` (the provisional cut the stored labels grade), `carried` for carry-forward
  records; `score` = the decision's **maximum** noul (Raidar's `calibration.decision_score`, not the
  summarizer's first-answer default); `positiveLabel` = `smell`; `sufficient` = recorded
  `inputSufficiency.level == sufficient`; `group` = `<problem>/<arm>/<scope>`. Labels live in
  `<store>/jev-audit/code-quality.rubric-smells/labels.jsonl` with supersessions in
  `ledger/label-supersessions.jsonl`; read them only through `raidar.jev.ledger.current_labels`.
  *Proposal:* add this exporter as `raidar jev export-cases` so the command is durable. (observed)
- **Version:** `policyVersion` and `questionSet` on every decision record (`2` /
  `scb-rubric@2`). The question-set version lives in `scb-rubric.yaml` and must be bumped with any
  change to criteria overlays, clip budgets, scope (`requires`) or kinds; the policy version with any
  mode, threshold or composition change. State-building code (`rubric.py`, `codebase.py`,
  `symbols.py`) carries no version of its own, so a change there must bump the question set too.
  (observed)
- **Judgment:** 45 `noul` questions, one per SCB criterion. Instructions:
  "Does the code in `symbol.source` exhibit the '<name>' pattern (<category>)? <description>";
  criteria `true` = "It does, as in: <positive indicators>", `false` = "It does not; these are not
  the pattern: <negative indicators>". No abstain outcome (a noul has none; 0.4–0.6 is the
  uncertainty band). Each criterion applies to symbol kinds (`function`, `method`, `module`,
  `class`) and needs one scope of state: `symbol` (30 criteria), `file` (4), `spec` (4) or
  `codebase` (7). At most 12 questions per request; requests are grouped by scope. (observed)
- **Stages:** Deterministic first: (1) a symbol whose span is unchanged since the previous
  checkpoint and whose earlier request settled is **carried forward** with no Jev call (rule
  `carry_forward:<origin checkpoint>`, 24,157 of 41,405 records); (2) a request whose required input
  is unavailable (no spec) is recorded `missing` and not asked. Everything else is asked. There is no
  per-criterion candidate filter: every applicable criterion is asked of every changed symbol.
  (observed)
- **Verdict mapping:** Shadow: every decision's verdict is `uncalibrated`,
  `countsTowardVerdicts: false`. The intended metric is ΣP, the sum of noul probabilities per
  criterion per checkpoint (`RubricCheckpoint.expected_counts`; `None` unless every request
  settled). Calibration grades a decision as positive when its **maximum** noul over the request's
  questions is at least the threshold (provisional 0.5; the §14.2 candidate fitted on the
  calibration side is 0.75). No threshold is in force. (observed)
- **Baseline:** Without the Jev step the 45 rubric criteria are not measured at all in Raidar:
  `code-quality@1` reports deterministic verbosity, erosion, clone and lint-rule metrics only, and
  Raidar runs no LLM judge. So the baseline is "unmeasured", and value is the information the ΣP
  metric adds, at its error rate. (observed)
- **Label kind:** `verdict-grade` in the store (`correct`, `false_positive`, `false_negative`,
  `uncertain`, graded against the provisional 0.5 cut on the max noul). For evaluation they are
  converted to `reference` labels `smell` / `clean` / `uncertain` (`calibration.reference_positive`).
  (observed)
- **Labels:** Reference vocabulary `smell` (positive: at least one asked criterion holds for the
  symbol), `clean`, `uncertain`. The label judges the code against the criterion text only; no actor
  or approval signal exists in this process. Current store: 38 current labels (26 `correct`,
  12 `false_positive`), all reviewer kind `model` (`claude-opus-5-5`), blind, two-labeller
  consensus; 33 earlier labels superseded; 7 held back. (observed)
- **R1 criteria:** The state holds the symbol's full source unclipped, plus, for each asked
  criterion, every input its definition refers to: the spec when a positive or negative indicator
  cites the spec (`unrequested_features`, `handling_excluded_cases`, `validation_mismatch`,
  `domain_blind_normalization`, and also `silent_truncation` and `silent_resource_creation`, whose
  negatives say "documented in spec"); callee behaviour when the criterion turns on a called
  function (`ignoring_return_values`, `validation_mismatch` on delegated validation); the codebase
  index for usage criteria. The recorded `inputSufficiency` checks clipping only, so it overstates
  R1 for criteria whose needed input is outside their scope. (open: proposed by the evaluator)
- **R2 procedure:** Answer each asked question from the recorded state alone as a 0–1 probability,
  blind to Jev's answers; the decision is `smell` when any answer is at least 0.5. Then re-decide
  with wider evidence (the full spec, the snapshot file) and record both when they differ (the
  difference is a state defect). Abstain (`uncertain`) only when the deciding fact is outside the
  state and the wider evidence does not settle it either. Contested stances are labelled under the
  written policy once it exists; until then label both ways and report each.
  (open: proposed by the evaluator)
- **Error costs:** Not settled. In shadow a wrong answer costs nothing; once ΣP feeds a reported
  metric, a false positive inflates a smell count and a false negative hides one. What matters most
  for an A/B benchmark is **differential** error between arms (a bias that follows the treatment),
  then total noise. Proposed: treat false positives on patterns common to both arms (CLI dispatch,
  data-directory creation) as the costlier error, because they add noise proportional to code size.
  (open)
- **Confirm set:** Every decision with a max noul at or above the candidate threshold (0.75), every
  decision in the 0.4–0.6 band, every held-back decision, and every case whose label rests on a
  contested stance (A: CLI command-string dispatch as `stringly_typed_logic`; B: silent
  `mkdir(parents=True, exist_ok=True)` as `silent_resource_creation`). (open)
- **Wider evidence:** The snapshot file at the checkpoint
  (`jobs/pilot-v2/<experiment>/<job>/trials/<trial>/…/snapshot`), the cumulative spec of the
  checkpoint, and the store's other decisions for the same symbol. `docs/reboot/jev-spot-check.md`
  holds the two labellers' per-question answers for the 45 relabel decisions. (observed)
- **Change bar:** A change is a new question-set and policy version and a new epoch. Evidence:
  replay the new version over the pilot-v2 snapshots in a fresh process
  (`make jev-shadow-rubric`), then blind-label a fresh sample chosen by a separate selector, with
  ≥ 30 labelled sufficient-input decisions **per criterion family that is to be enforced**, ≥ 5 of
  the minority label in each, a random (not only Jev-positive) component so recall is measurable, a
  calibration/evaluation split by trial with both arms of a batch on one side and identical
  `cacheKey` states kept on one side, a threshold chosen on calibration only and frozen, and the
  value decision in evaluate-jev `development.md` "Decide". Never tune on the evaluation side.
  (open: proposed; the ≥ 30 / ≥ 5 / ≥ 0.8 gate itself is observed in `policies.py`)
- **Known limits:**
  - Composition: one threshold over the maximum of up to 12 heterogeneous nouls; a noul threshold
    does not transfer between questions. (observed)
  - Three criteria drive 91% of decisions flagged at 0.75 (`stringly_typed_logic` 718,
    `silent_resource_creation` 195, `validation_mismatch` 107 of 1,122); 26 of 45 criteria never
    reach 0.5 on the pilot. (observed, `scb-rubric@2`)
  - 80% of all ΣP mass comes from answers below 0.5, so ΣP scales with the number of symbols asked.
    (observed, `scb-rubric@2`)
  - Symbol-scope questions cannot see the spec, so "documented in spec" negatives are undecidable
    there (stance B). (observed)
  - Calibration-split leakage: 160 identical states (`cacheKey`) occur in both calibration and
    evaluation trials. (observed)
  - Trial ids in the audit use the pre-v2 form (`pilot-forge.forge-02.b00-p0-baseline`); later
    decisions use the new experiment id. (observed)

## Reading the summary for this profile

- Pass `--version "code-quality.rubric-smells@2/scb-rubric@2"` and use the exporter's `score` (max
  noul). The summarizer's confusion and sweep then describe the decision-level verdict Raidar
  calibrates, not any one criterion.
- `byStage` separates carried-forward (`deterministic`) from asked (`jev`) records; carried records
  repeat an earlier answer and must not be scored twice.
- A decision's error is attributed to the criterion with the highest noul; read `answers` for the
  rest.
