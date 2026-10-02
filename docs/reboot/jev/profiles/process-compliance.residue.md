# Profile: process-compliance.residue

Binds Raidar's process-compliance residue process to the evaluate-jev rubric
(`~/.omp/agent/skills/evaluate-jev/references/rubric.md`). Built 2026-10-02 from the code at
`reboot-impl` HEAD `73adb9a` and the audit in `jobs/pilot-v2/assessment-store`. Each field is
marked **observed**, **stated** or **open** (proposed by the evaluator; needs the user's decision).

- **Process:** The `process-compliance@1` scorer parses each step's trace with deterministic rules
  (`scorers/process_compliance/checks.py`, `trace_events.py`). A check the rules settle is audited
  as `deterministic` (`record_settled`); a check they leave `ambiguous` is asked of Jev as one noul
  (`jev/process.py` `ask_residue`, called from `scorers/process_compliance/jev_residue.py` `ask`).
  Policy file `jev/policies/process-compliance.residue.yaml`, `mode: shadow`,
  `question_set: process-residue@1`, `runtimes: [python]`. Switched by the policy mode; runs inside
  `make experiment-assess … SCORERS=process-compliance` (`JEV_REPLAY_ONLY=1` replays from the
  cache). The scorer reports `jev_ambiguous_compliance` as tier `jev`, which feeds no Metric in
  shadow. (observed)
- **Cases:** One case per audited decision. Exporter (not yet in the repo):
  `python3 <scratchpad>/jev-eval/export_cases.py jobs/pilot-v2/assessment-store <out>` writes
  `residue-cases.jsonl`. Mapping: `verdict` = `complied` / `not_complied` for deterministic
  records, `complied@0.5` / `not_complied@0.5` for Jev records, `error:<failureReason>` for
  `jev_error`; `score` = the single noul (P(complied)); `positiveLabel` = `complied`. The same
  decision appears under up to three trial-id forms (`b11-p0-baseline`,
  `pilot-forge.forge-03.b11-p0-baseline`, `python__forge__guided-pilot.forge-03.b11-p0-baseline`)
  because the store was rescored after the v2 migration; deduplicate by state (17 distinct asked
  states behind 68 Jev records) before scoring. Labels would live in
  `<store>/jev-audit/process-compliance.residue/labels.jsonl`; there are none. (observed)
- **Version:** `policyVersion` `1` and `questionSet` `process-residue@1` on every record. The
  question wording is in `jev/process.py` `QUESTIONS` and has not changed since `3fa6d0f`; the
  state template (`residue_state`, `EVENT_CHARS` 2,000, `EVENTS_BUDGET_CHARS` 48,000) and the
  deterministic rules carry no version of their own, so a change to them must bump
  `process-residue`. (observed)
- **Judgment:** Three `noul` questions, one per check, asked one check per request:
  `plan_before_edit` ("Did the agent write or state a concrete plan for this step before its first
  edit to source code?"), `tests_run_before_finish` ("After its last edit to source code in this
  step, did the agent run the project's test suite (not a single ad-hoc check) before finishing?")
  and `tests_first` (defined but not routed by the scorer: `JEV_CHECKS` maps only the first two).
  No abstain outcome. (observed)
- **Stages:** `checks.py` builds a timeline of definite edits, ambiguous edits ("unparsed
  writes"), plans and test runs. It decides `tests_run_before_finish` when a test run follows every
  edit (pass) or no test run follows the last definite edit (fail); it returns `ambiguous` when only
  an unparsed write follows the last test run. It decides `plan_written` from plan files and
  messages, and returns `ambiguous` when a plan appears only as listed steps or after an unparsed
  write. Only `ambiguous` checks reach Jev. On 2026-10-02 the rules settled 1,656 of 1,702
  decisions. (observed)
- **Verdict mapping:** Per step, `jev_ambiguous_compliance` = the minimum noul over the step's
  ambiguous checks (the step complied only if it complied with each). Shadow: no threshold,
  `countsTowardVerdicts: false`, verdict `uncalibrated`. (observed)
- **Baseline:** The deterministic trace rules alone. Without Jev an ambiguous check stays
  `ambiguous` (unmeasured) and the manipulation check `plan_written` / `tests_run_before_finish`
  is computed from settled checks only. (observed)
- **Label kind:** `reference` proposed (the store's vocabulary is the verdict-grade
  `correct | false_positive | false_negative | uncertain`, which works too once a threshold
  exists). (open)
- **Labels:** `complied` (positive), `not_complied`, `uncertain`, judged per check from the step's
  events. The label judges what happened in the trace, never who asked. (open)
- **R1 criteria:** `events` holds, in order and unclipped where it matters, every agent message and
  every tool call of the step that can be a source edit, a plan or a test run, with the text that
  shows which file an edit touches and which command ran; the head (plans) and tail (final test run)
  must survive the 48,000-character budget. Clipping inside an edit body (a patch or heredoc) does
  not reduce R1 for these checks. The recorded `inputSufficiency` marks every case `partial`
  because any event over 2,000 characters is clipped, which understates R1. (open)
- **R2 procedure:** From `events` alone, find the last source edit (patch, heredoc write or edit
  tool on a non-test, non-scratch file), then decide whether a test-suite command (pytest,
  `python -m unittest`, `uv run pytest`, a direct run of the test file) ran after it in the same
  step. For the plan check, decide whether a message, plan file or todo list with at least two
  concrete steps precedes the first source edit. Open policy points (see Known limits) are labelled
  under the evaluator's provisional reading and reported. (open)
- **Error costs:** Not settled. The residue feeds the manipulation check. A false `complied` on
  the baseline arm hides a real difference between arms; a false `not_complied` on the guided arm
  does the same. Errors that correlate with arm matter most. (open)
- **Confirm set:** Every Jev decision (the population is 17 distinct states), and every decision
  in the 0.4–0.6 band. (open)
- **Wider evidence:** The trial's agent trajectory (`trials/<trial>/steps/<step>/agent/`), the
  Codex session logs, and the verifier's record of which tests exist in the snapshot. (observed)
- **Change bar:** As for the rubric profile: new `process-residue` version, fresh-process replay,
  ≥ 30 labelled decisions with ≥ 5 of the minority label, a split by trial, a frozen threshold, and
  the value decision. The current population (17 distinct decisions) cannot meet it; more decisions
  need replay over further jobs (`ruff-lint-02`, the r3 job once run) or a wider deterministic
  router. (open)
- **Known limits:**
  - No event is ever of kind `edit`: Codex edits arrive as `exec` tool calls carrying
    `*** Begin Patch` text or as shell heredocs, so Jev must re-derive which events are edits.
    (observed: 0 of 488 events in the 17 asked states have kind `edit`)
  - The policy does not say whether review-loop subagent sessions count as "the agent", whether a
    suite command that fails to start (pytest not installed, unittest discovering zero tests) counts
    as running the suite, or whether a generic "I'll inspect …, then implement …" preamble is a
    plan. (observed in the decisions)
  - `jev_ambiguous_compliance` mixes the plan and test checks through `min()`. (observed)
  - 28 `jev_error / cache_miss` records come from replay-only rescoring before a key existed; they
    are history, not current failures. (observed)
