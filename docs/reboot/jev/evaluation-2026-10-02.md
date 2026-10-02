# Jev evaluation and design review (2026-10-02)

This evaluates Raidar's two Jev processes against the `evaluate-jev` skill
(`~/.omp/agent/skills/evaluate-jev`: `SKILL.md`, `jev-design.md`, `development.md`, `rubric.md`,
`case-format.md`, `profile-template.md`, `example-profile.md`) and the live TypeSafe docs
(`docs.typesafe.ai`: `llms.txt`, `concepts/state`, `primitives`, `primitives/noul`,
`primitives/advanced`, `confidence`, `patterns/confidence-routing`, `patterns/composite-scoring`,
`concepts/how-to-build-with-system-one`, `introduction/coding-agents`, `models`,
`model-jaggedness/jev-1.13`). It covers Evaluate mode (the rubric R1 to R5) and a design review
against `jev-design.md`. It was read-only over Raidar code, the audit and the label store: no
question, threshold, policy, label or split was changed, and no Jev call or agent trial was made.

Profiles: [code-quality.rubric-smells](profiles/code-quality.rubric-smells.md) and
[process-compliance.residue](profiles/process-compliance.residue.md).

## 1. Verdict

| Process | Version evaluated | Recommendation |
|---|---|---|
| `code-quality.rubric-smells` | policy `2`, question set `scb-rubric@2`, model `typesafe/jev-1.13-20260917` | **Next iteration. Do not promote and do not re-fit a threshold on `@2`.** The errors come from the question, state and composition design and from undecided policy, more than from Jev's perception. Under the evaluator's labels, 14 of the 23 labelled decisions flagged at the 0.75 candidate threshold are not smells. 8 of those 14 rest on undecided policy (stance A or near-identical cases with opposite labels), 3 on a question that does not draw the CLI boundary, and 3 on Jev's own reading. |
| `process-compliance.residue` | policy `1`, question set `process-residue@1`, same model | **Keep `tests_run_before_finish` and route its 0.4–0.6 band. Redesign `plan_before_edit`. Fix the deterministic router first.** On the 17 distinct asked decisions, Jev agrees with the evaluator on 12. Outside the band it gets 10 of 10 test-check decisions right. It gets 0 of 3 plan-check decisions right, because the criteria do not exclude a generic "inspect, then implement" preamble. |

Neither process is ready for `enforce`, and neither should be judged on more of the same records.
Before any new agent trials, the next step is **a Jev replay under new question-set versions over
the existing pilot-v2 snapshots and traces** (about $2 at today's volume, less with the
candidate filters below), followed by labelling (section 7).

## 2. Scope, versions and provenance

**Fresh-instance and versioning (SKILL.md).**
- `code-quality.rubric-smells`: all 41,405 records are policy `2` / `scb-rubric@2`, written
  between 03:18:46Z and 03:48:32Z on 2026-10-02 by `make jev-shadow-rubric` processes. Those
  processes started after commit `1cf9d2f` (03:07:57Z), which introduced `@2`. The question and
  state code (`rubric.py`, `rubric_questions.py`, `codebase.py`, `symbols.py`, `redaction.py`)
  is unchanged from `1cf9d2f` to HEAD `73adb9a`; later commits touched only calibration tooling
  and the policy file's comment. **One version, no mix.**
- `process-compliance.residue`: all 3,378 records are policy `1` / `process-residue@1`. The
  question wording and trace rules are unchanged since `3fa6d0f`; `9b7eff1` changed a docstring
  only. The 17 fresh decisions were made live at about 03:00Z on 2026-10-02. The other 51 Jev
  records are cache replays of the same 17 states under two other trial-id forms (the v2
  migration rescore). The 28 `jev_error / cache_miss` records are replay-only runs from before a
  key existed. **One version. The trial-id forms are mixed** (`b11-p0-baseline`,
  `pilot-forge.forge-03.b11-p0-baseline`, `python__forge__guided-pilot.forge-03.b11-p0-baseline`),
  so this evaluation deduplicates by state.

**Cases.** `export_cases.py` (scratchpad, section 9) converted the audit to case lines. The
summarizer ran as
`bun scripts/summarize.ts --cases-in … --version "code-quality.rubric-smells@2/scb-rubric@2" [--labels-in …] --threshold 0.75`
(and the residue equivalent). The case `score` is the decision's **maximum** noul, which is what
Raidar calibrates, not the summarizer's first-answer default.

**What was scored.**
- Rubric: the 45 decisions of the relabel rounds, that is the 38 currently labelled plus the 7 held
  back. Scoring all 17,248 asked decisions is neither possible nor useful. The store-wide
  aggregates in 4.3 describe the rest.
- Residue: all 17 distinct asked states, which is the whole population.

**Who scored, and how blind.** This evaluator is the same model family as the existing labellers
(Claude Opus). Each decision was labelled per question from a blind rendering (state and
questions only), and the labels were written to file before Jev's answers were opened. The
blindness is weaker than that sounds: while reading the docs, the evaluator had already seen the
header of `docs/reboot/jev-spot-check.md` and a grep of it. Those name the stance-A and stance-B
decision ids and show a few of Jev's `stringly_typed_logic` values. Treat these labels as **agent
labels**, a first labelling pass, not as an independent reference. Nothing was written to the
label store.

## 3. Operational health (R5)

| Measure | rubric-smells @2 | residue @1 |
|---|---|---|
| Decisions audited | 41,405 (24,157 carried forward, 17,248 asked) | 3,378 (3,282 deterministic, 68 asked, 28 `jev_error`) |
| Distinct asked states | 15,495 cache keys | 17 |
| Coverage of asked decisions | 17,248 / 17,248 answered | live run: 17 / 17 answered; all rows: 68 / 96 (the 28 misses are earlier replay-only runs) |
| Model pinning | 17,248 / 17,248 `typesafe/jev-1.13-20260917` | 68 / 68 pinned |
| Unresolved | 0 | 28 `cache_miss` (history, not a current fault) |
| Input sufficiency, recorded | 17,236 / 17,248 `sufficient` (0.9993) | 0 / 17 `sufficient` (every case `partial`) |
| Input sufficiency, evaluator R1 | 42 / 45 = 0.93 on the scored sample (R1 = 1: C33, C37, C44) | 16 / 17 = 0.94 (R1 = 1: R16) |
| Cost | $2.3221 for 15,499 fresh calls (mean $0.00015) | $0.0037 for 17 calls |
| Latency | p50 298 ms, p95 407 ms | p50 310 ms, p95 453 ms |

Input sufficiency is above 0.8 on both processes, so accuracy can be judged. Both recorded figures
are wrong, in opposite directions. The rubric figure measures clipping only, so it misses
criteria whose needed input is outside the request's scope (the spec for
`silent_resource_creation`, callees for `ignoring_return_values`). The residue figure marks every
case `partial` because any event over 2,000 characters is clipped. The clipped text is patch and
heredoc bodies, which these checks do not need. **This alone makes the residue policy fail the
promotion gate** ("input sufficiency 0/62 < 0.8").

## 4. code-quality.rubric-smells

### 4.1 Per-case scoring (R1–R4)

Labels: **R2 (state, A rejected)** is the evaluator's label from the recorded state alone, with
stance A rejected. Stance B is not decided here: from a symbol-scope state the rubric's own
positive example applies, so C33 is `smell` state-only and `clean` only with the spec (wider
evidence). **R2 (A and B kept)** is the literal-rubric reading. **Consensus** is Raidar's current
label, converted to a reference label (two blind model labellers). R3 is computed against the
state-only label. "Side" is the recorded split `relabel-round-1`.

| Case | Request | Subject | Side | R1 | R2 (state, A rejected) | R2 (A and B kept) | Consensus | Jev max (criterion) | R3 @0.5 | R3 @0.75 | Cause of error |
|---|---|---|---|---|---|---|---|---|---|---|---|
| C01 | `0136ec2f` | `checkpoint_5:forge.py::command_unit_transition[symbol]` | cali | 2 | smell | smell | smell | 0.95 (stringly_typed_logic) | correct | correct |  |
| C02 | `0496e906` | `checkpoint_6:forge.py::list_modules[symbol]` | cali | 2 | clean | clean | clean | 0.69 (type_coercion_hiding_mismatches) | FP | correct | Jev error (filter semantics are spec-defined) |
| C03 | `14383ef0` | `checkpoint_1:circopt.py::ExprParser.parse[symbol]` | eval | 2 | smell | smell | smell | 0.89 (stringly_typed_logic) | correct | correct |  |
| C04 | `151ff069` | `checkpoint_5:forge.py::validate_unit_create[codebase]` | eval | 2 | clean | clean | clean | 0.03 (implicit_default_parameters) | correct | correct |  |
| C05 | `17b5e0f2` | `checkpoint_1:forge.py::save_all[codebase]` | cali | 2 | clean | clean | clean | 0.04 (implicit_default_parameters) | correct | correct |  |
| C06 | `1f3d9d4b` | `checkpoint_3:forge.py::bind_allocations[symbol]` | eval | 2 | smell | smell | smell | 0.95 (stringly_typed_logic) | correct | correct |  |
| C07 | `25ef506f` | `checkpoint_1:circopt.py::_parse_file[symbol]` | eval/held | 2 | clean | clean | held back | 0.81 (stringly_typed_logic) | FP | FP | Policy/label defect (near-twin of C15/C17, opposite labels) |
| C08 | `2612252c` | `checkpoint_1:circopt.py::main[symbol]` | eval | 2 | clean | clean | clean | 0.88 (stringly_typed_logic) | FP | FP | Question defect (CLI-boundary check not in criteria) |
| C09 | `2a0e8705` | `checkpoint_8:forge.py::delete_blueprint[spec]` | eval | 2 | clean | clean | clean | 0.75 (validation_mismatch) | FP | FP | Jev error (symbol matches spec; 35k-char spec state) |
| C10 | `36998448` | `checkpoint_1:circopt.py::_check_cycles[spec]` | eval | 2 | clean | clean | clean | 0.34 (validation_mismatch) | correct | correct |  |
| C11 | `37e39596` | `checkpoint_5:forge.py::create_unit[symbol]` | cali | 2 | clean | clean | clean | 0.76 (stringly_typed_logic) | FP | FP | Jev error (one status literal, error-category strings) |
| C12 | `477ccc7b` | `checkpoint_6:forge.py::list_modules[symbol]` | cali/held | 2 | clean | clean | held back | 0.42 (stringly_typed_logic) | correct | correct |  |
| C13 | `480ef4b6` | `checkpoint_5:forge.py::_unit_response[symbol]` | cali/held | 2 | smell | smell | held back | 0.87 (auto_correction_without_warning) | correct | correct |  |
| C14 | `5221be0c` | `checkpoint_1:circoptlib.py::parse_and_validate[symbol]` | cali | 2 | clean | clean | clean | 0.34 (inline_state_machines) | correct | correct |  |
| C15 | `5ac4f782` | `checkpoint_1:circopt.py::parse_file[symbol]` | eval | 2 | clean | clean | smell | 0.79 (stringly_typed_logic) | FP | FP | Policy/label defect (twin of C07/C17) |
| C16 | `5c19bd8a` | `checkpoint_1:circopt.py::main[symbol]` | eval/held | 2 | clean | clean | held back | 0.88 (stringly_typed_logic) | FP | FP | Question defect (CLI-boundary check) |
| C17 | `675328db` | `checkpoint_1:circopt.py::parse_circuit[symbol]` | cali | 2 | clean | clean | smell | 0.82 (stringly_typed_logic) | FP | FP | Policy/label defect (twin of C07/C15) |
| C18 | `6cabffc1` | `checkpoint_1:circopt.py::main[symbol]` | cali | 2 | clean | clean | clean | 0.87 (stringly_typed_logic) | FP | FP | Question defect (CLI-boundary check) |
| C19 | `6cd12578` | `checkpoint_7:forge.py::main[spec]` | eval | 2 | clean | clean | clean | 0.63 (validation_mismatch) | FP | correct | Jev error (spec-required envelope logic) |
| C20 | `725f4f20` | `checkpoint_6:forge.py::list_modules[symbol]` | eval | 2 | clean | clean | clean | 0.63 (stringly_typed_logic) | FP | correct | Question defect (spec filter token `null`) |
| C21 | `772c806a` | `checkpoint_3:forge.py::delete_allocations[spec]` | eval | 2 | clean | clean | clean | 0.86 (validation_mismatch) | FP | FP | Jev error (symbol matches cp3 spec) |
| C22 | `7b623542` | `checkpoint_2:forge.py::save_allocations[codebase]` | eval | 2 | clean | clean | clean | 0.05 (implicit_default_parameters) | correct | correct |  |
| C23 | `7d15c103` | `checkpoint_4:forge.py::&lt;module&gt;[symbol]` | cali | 2 | clean | clean | clean | 0.57 (scaffold_remnants) | FP | correct | Jev error (constants read as scaffold; band) |
| C24 | `80f42741` | `checkpoint_5:forge.py::run[symbol]` | eval | 2 | smell | smell | smell | 0.96 (stringly_typed_logic) | correct | correct |  |
| C25 | `84ff908c` | `checkpoint_2:forge.py::run[symbol]` | cali | 2 | clean | smell | smell | 0.92 (stringly_typed_logic) | FP | FP | Policy defect (stance A undecided) |
| C26 | `8714d0cf` | `checkpoint_1:circopt.py::ExpressionParser._expr[symbol]` | cali | 2 | smell | smell | smell | 0.90 (stringly_typed_logic) | correct | correct |  |
| C27 | `874c8a12` | `checkpoint_1:forge.py::validate_input[symbol]` | cali | 2 | clean | clean | clean | 0.31 (stringly_typed_logic) | correct | correct |  |
| C28 | `95bbf25f` | `checkpoint_3:forge.py::run[symbol]` | eval | 2 | clean | smell | smell | 0.95 (stringly_typed_logic) | FP | FP | Policy defect (stance A undecided) |
| C29 | `9657ddf2` | `checkpoint_6:forge.py::_run_tag[symbol]` | eval | 2 | clean | clean | clean | 0.47 (stub_implementations) | correct | correct |  |
| C30 | `9847f3bf` | `checkpoint_7:forge.py::bind_allocations[symbol]` | eval | 2 | clean | clean | clean | 0.11 (inline_state_machines) | correct | correct |  |
| C31 | `98e42c0e` | `checkpoint_7:forge.py::allocation_response[symbol]` | cali | 2 | clean | clean | clean | 0.36 (scaffold_remnants) | correct | correct |  |
| C32 | `9906c736` | `checkpoint_7:forge.py::bind_allocations[symbol]` | eval | 2 | smell | smell | smell | 0.95 (stringly_typed_logic) | correct | correct |  |
| C33 | `9c20b4fa` | `checkpoint_4:forge.py::save_units[symbol]` | eval | 1 | smell | smell | smell | 0.85 (silent_resource_creation) | correct | correct |  |
| C34 | `9e01aabb` | `checkpoint_8:forge.py::&lt;module&gt;[symbol]` | eval | 2 | clean | clean | clean | 0.44 (scaffold_remnants) | correct | correct |  |
| C35 | `aeb4925f` | `checkpoint_1:circopt.py::interface[symbol]` | cali | 2 | clean | clean | clean | 0.74 (stub_implementations) | FP | correct | Jev error (one-line helper read as stub) |
| C36 | `aefa755d` | `checkpoint_6:forge.py::main[symbol]` | cali | 2 | clean | clean | clean | 0.14 (redundant_null_check) | correct | correct |  |
| C37 | `bd49bd1b` | `checkpoint_4:forge.py::run[spec]` | eval | 1 | uncertain | uncertain | clean | 0.80 (validation_mismatch) | excluded | excluded |  |
| C38 | `bf4cb834` | `checkpoint_5:forge.py::run_units[spec]` | cali | 2 | smell | smell | smell | 0.76 (validation_mismatch) | correct | correct |  |
| C39 | `cc3133cb` | `checkpoint_4:forge.py::main[symbol]` | cali | 2 | clean | smell | smell | 0.88 (stringly_typed_logic) | FP | FP | Policy defect (stance A undecided) |
| C40 | `ce382801` | `checkpoint_4:forge.py::main[symbol]` | cali/held | 2 | clean | smell | held back | 0.91 (stringly_typed_logic) | FP | FP | Policy defect (stance A undecided) |
| C41 | `d16ed4e1` | `checkpoint_7:forge.py::delete_blueprint[file]` | cali | 2 | clean | clean | clean | 0.08 (correlation_id_absence) | correct | correct |  |
| C42 | `d8c66efb` | `checkpoint_8:forge.py::main[spec]` | cali/held | 2 | clean | clean | held back | 0.52 (validation_mismatch) | FP | correct | Jev error in the 0.4–0.6 band (route, do not decide) |
| C43 | `f044e94f` | `checkpoint_3:forge.py::bind_allocations[spec]` | eval | 2 | clean | clean | clean | 0.32 (validation_mismatch) | correct | correct |  |
| C44 | `f5891800` | `checkpoint_7:forge.py::main[symbol]` | cali/held | 1 | clean | smell | held back | 0.92 (stringly_typed_logic) | FP | FP | Policy defect (stance A undecided) |
| C45 | `f811a13f` | `checkpoint_7:forge.py::get_blueprint[spec]` | eval | 2 | clean | clean | clean | 0.69 (validation_mismatch) | FP | correct | Jev error (spec-scope patterns absent) |

Each evaluator label's one-line evidence is in `evaluator-r2.jsonl` (section 9). The deciding
excerpts:
- **Smells.** C01, C24 and C32 compare and assign the status literals `"active"`/`"inactive"`
  and `"bound"`/`"unbound"`. C03 and C26 tag AST nodes with strings (`"lit"`, `"badarity:"+op`,
  `"LITERAL"`). C06 does both. C13 silently turns invalid stored capabilities into `[]`. C38 raises
  `validation_error` for a `module_uuids` filter where the cp4 spec names only a missing `=`. C33
  calls `directory.mkdir(parents=True, exist_ok=True)` without logging.
- **Clean.** C08, C16 and C18 make one `command != "check"` argv test. C20 and C12 use the
  spec-defined filter token `"null"`. C09, C21 and C43 match the cp3/cp8 delete and bind
  semantics line by line.
- **Uncertain.** C37 turns on `require_uuid`, which is not in the state.

### 4.2 Aggregates (labelled sample, version `@2`)

| Label set | Threshold | TP | FP | FN | TN | Precision | Recall |
|---|---|---|---|---|---|---|---|
| Evaluator, state-only, stance A rejected (44 scored, 1 uncertain) | 0.50 | 9 | 21 | 0 | 14 | 9/30 | 9/9 |
| same | 0.75 | 9 | 14 | 0 | 21 | 9/23 | 9/9 |
| same, evaluation side only | 0.75 | 5 | 7 | 0 | 10 | 5/12 | 5/5 |
| Evaluator, stances A and B kept | 0.75 | 14 | 9 | 0 | 21 | 14/23 | 14/14 |
| Raidar consensus (38) | 0.75 | 13 | 6 | 0 | 19 | 13/19 | 13/13 |
| Raidar consensus, evaluation side (21) | 0.75 | 7 | 4 | 0 | 10 | 7/11 | 7/7 |

- **Error causes, all 21 false positives at 0.5:**
  - Jev error: 9 (C02, C09, C11, C19, C21, C23, C35, C42, C45).
  - Policy or label defect: 8 (C25, C28, C39, C40, C44 on stance A; C07, C15, C17 on
    near-identical cases with opposite labels).
  - Question defect: 4 (C08, C16, C18, C20).
  - Missing evidence: 0.
  - Rule or code error: 0.
- **At 0.75**, 14 false positives remain: 8 policy or label, 3 question, 3 Jev (C09, C11, C21).
  Read the other way, raising the threshold from 0.5 to 0.75 removes 6 of the 9 Jev errors but
  none of the policy errors.
- **No false negatives in any label set.** This is a selection artefact, not evidence of recall.
  The relabel pools were enriched towards Jev positives, and only 14 decisions below 0.5 were
  labelled. Recall is unmeasured.
- **Agreement between labellers.** The evaluator and the consensus agree on 32 of 37 jointly
  decided labels (35 of 37 if stances A and B are kept). The disagreements are stance A (C25,
  C28, C39) and the twins C15 and C17. All labels involved come from the same model family.
- **Abstention appropriateness.** One abstention (C37), with R1 = 1. Jev cannot abstain: a noul
  has no abstain option, and the process has no band routing.
- **Sample size.** 44 labelled sufficient-input decisions (9 positives under the evaluator, 14
  under the literal stances). That is above the skill's floor of 30 / 5 for one decision-level
  threshold. It is far below what a per-criterion threshold needs: at least 40 of 45 criteria have
  no labelled positive at all.

### 4.3 Store-wide behaviour (all 17,248 asked decisions, `@2`)

| Criterion | Asked | ≥ 0.5 | ≥ 0.75 | In 0.4–0.6 |
|---|---|---|---|---|
| `stringly_typed_logic` | 2,764 | 1,161 | 718 | 541 |
| `silent_resource_creation` | 2,764 | 227 | 195 | 26 |
| `validation_mismatch` | 2,764 | 417 | 107 | 387 |
| `auto_correction_without_warning` | 2,764 | 307 | 50 | 116 |
| 15 other criteria | — | 1 to 83 each | 0 to 24 each | — |
| 26 criteria (all 8 class-level criteria among them) | — | 0 | 0 | — |

- 1,122 decisions reach 0.75. `stringly_typed_logic` is the top answer in 718 of them (64%),
  `silent_resource_creation` in 195 and `validation_mismatch` in 107, so three criteria drive 91%
  of flags.
- **ΣP is dominated by noise.** 80% of all ΣP mass (15,017 of 18,874) comes from answers below
  0.5. The intended metric is ΣP, the expected smell count. Summed over 45 criteria per symbol,
  the low-probability floor makes ΣP grow with the number of symbols, not with smells. Forge
  baseline ΣP is 3,546.7 over 6,646 decisions, and guided is 4,575.0 over 9,275.
- **Flag rate by arm at 0.75:**
  - forge baseline: 507 / 6,646 (7.6%)
  - forge guided: 580 / 9,275 (6.3%)
  - circuit_eval baseline: 17 / 580
  - circuit_eval guided: 18 / 747

### 4.4 Confidence quality (R4)

- Positive probability ≥ 0.5 on a negative label (calibration concern): all 21 FPs at 0.5 above.
  12 of them are `stringly_typed_logic` at 0.63–0.95.
  - Jev puts ≥ 0.75 on this criterion in 18 of the 21 labelled decisions that ask it.
  - Only 6 of those 18 are smells under the evaluator (11 under the literal stances).
  - The criterion is saturated, not calibrated, on this code.
- Correct positives with the winning probability ≥ 0.8: 8 of 9. C38 is weak at 0.76.
- The 0.4–0.6 band holds 5 labelled decisions (C12 0.42, C23 0.57, C29 0.47, C34 0.44, C42 0.52),
  all `clean`. Routing the band instead of cutting at 0.5 would have avoided 2 FPs (C23, C42).
- Every case at or above 0.75 was checked against wider evidence. For spec scope that was the
  full cumulative spec (`spec-37e2bea7`, cp1–cp8); for stance B it was the spec's
  `--data-dir` text. No positive changed except C33 (state-only `smell`, `clean` under stance B
  with the spec).

### 4.5 Value against the baseline

| Item | Value (version `@2`) |
|---|---|
| Baseline | The 45 rubric criteria are unmeasured. `code-quality@1` has deterministic verbosity, erosion, clone and lint metrics only, and Raidar runs no LLM judge. |
| Added information | Per-symbol answers on 45 criteria for $2.32 per full pilot pass, at p50 0.3 s per call. |
| Added catches (labelled sample, 0.75) | 9 of 9 evaluator smells flagged (14 of 14 under the literal stances). Recall is unmeasured outside the enriched pool. |
| Burden (false flags) | 14 of 35 clean decisions flagged (9 of 30 under the literal stances); 1,122 of 17,248 asked decisions flagged store-wide. For a metric, the burden is noise: ΣP takes 80% of its mass from sub-0.5 answers. |
| Cost per false flag | None in shadow. Once reported, each false flag inflates a smell count for both arms. The flag rate differs by arm (7.6% vs 6.3% on forge), so a bias between arms cannot be ruled out from these data. |
| Remaining misses | Unknown (no random negative sample). |
| Decision | **Next iteration** (development.md "Decide"). It adds a measurement nothing else gives, but at this error rate and with ΣP dominated by the floor, it would not give a trustworthy smell count. A threshold re-fit cannot fix what is wrong: 8 of 14 FPs at 0.75 are policy, 3 are question design. |

## 5. process-compliance.residue

### 5.1 Per-case scoring

Positive = `complied`; R3 at the provisional 0.5. "Records" counts the audit rows that share the
state (the live answer plus replays).

| Case | Request (first) | Trial | Check | Records | R1 (mine / recorded) | R2 label | Evidence (state) | Jev noul | R3 @0.5 | Cause |
|---|---|---|---|---|---|---|---|---|---|---|
| R01 | `ebab749c` | `pilot-forge.forge-03.b11-p0-baseline` | tests_run_before_finish | 4 | 2 / partial | not_complied | only `py_compile` after the last edit; no suite run | 0.05 | correct |  |
| R02 | `9412eed8` | `pilot-forge.forge-03.b11-p0-baseline` | plan_before_edit | 4 | 2 / partial | not_complied | opening 'inspect …, then implement …' restates the task; no steps | 0.79 | FP | Question/policy defect: generic preamble not excluded by criteria |
| R03 | `a9c23f97` | `pilot-forge.forge-02.b00-p0-baseline` | tests_run_before_finish | 4 | 2 / partial | not_complied | only `py_compile` after the last edit | 0.05 | correct |  |
| R04 | `7dcef705` | `pilot-forge.forge-02.b00-p0-baseline` | plan_before_edit | 4 | 2 / partial | not_complied | preamble and one intent message; no steps | 0.78 | FP | Question/policy defect: generic preamble |
| R05 | `0015c0e8` | `pilot-forge.forge-02.b01-p0-baseline` | tests_run_before_finish | 4 | 2 / partial | not_complied | ad-hoc CLI script after the last edit; no suite | 0.05 | correct |  |
| R06 | `b0f6c6b5` | `pilot-forge.forge-02.b01-p0-baseline` | plan_before_edit | 4 | 2 / partial | not_complied | one design intent, no steps | 0.78 | FP | Question/policy defect: generic preamble |
| R07 | `26dc189e` | `pilot-forge.forge-02.b02-p0-guided` | tests_run_before_finish | 4 | 2 / partial | complied | `python -m unittest discover` (review round) after the last edit | 0.74 | correct |  |
| R08 | `7a02389b` | `pilot-forge.forge-02.b04-p0-guided` | tests_run_before_finish | 4 | 2 / partial | complied | `python -m unittest -v` [159] after the last source patch [151] | 0.80 | correct |  |
| R09 | `38aa1716` | `pilot-forge.forge-02.b05-p0-guided` | tests_run_before_finish | 4 | 2 / partial | complied | `unittest discover` [8] after edits; reviewer reruns [14] | 0.87 | correct |  |
| R10 | `5beb90fe` | `pilot-forge.forge-02.b07-p0-guided` | tests_run_before_finish | 4 | 2 / partial | complied | `uv run pytest -q` [26] after the retry patches | 0.83 | correct |  |
| R11 | `900e04e4` | `pilot-forge.forge-02.b08-p0-guided` | tests_run_before_finish | 4 | 2 / partial | complied | `python -m unittest discover -s tests -v` [14] after the last edit [10] | 0.48 | FN | Jev error in the 0.4–0.6 band: the review session later says pytest could not run, which also asks which session is "the agent" (policy) |
| R12 | `6eca8024` | `pilot-forge.forge-03.b07-p0-guided` | tests_run_before_finish | 4 | 2 / partial | not_complied | `python -m pytest` after the last edit, but pytest is not installed (reviewer [34]) | 0.57 | FP | Policy defect in the band: does a suite command that fails to start count? |
| R13 | `cd954ee8` | `pilot-forge.forge-03.b08-p0-guided` | tests_run_before_finish | 4 | 2 / partial | complied | `python tests/test_forge.py -v` [9] after the last edit | 0.79 | correct |  |
| R14 | `2ac60bab` | `pilot-forge.forge-03.b09-p0-guided` | tests_run_before_finish | 4 | 2 / partial | not_complied | pytest/ruff absent [30]; only an ad-hoc harness after the last edit | 0.49 | correct |  |
| R15 | `e7e13310` | `pilot-forge.forge-03.b09-p0-guided` | tests_run_before_finish | 4 | 2 / partial | not_complied | unittest discovers no tests [29],[33]; ad-hoc checks only | 0.47 | correct |  |
| R16 | `9ef250d0` | `pilot-forge.forge-03.b12-p0-guided` | tests_run_before_finish | 4 | 1 / partial | complied | `uv run pytest -q` [11] after the last visible edit; the retry round claims an edit with no visible edit event | 0.73 | correct |  |
| R17 | `f0886ec2` | `pilot-circuit-eval-oneshot.oneshot-01.b02-p0-guided` | tests_run_before_finish | 4 | 2 / partial | complied | `uv run --with pytest pytest -q` [39] after the last patch [38] | 0.88 | correct |  |

### 5.2 Aggregates and value

- At 0.5: TP 7, FP 4, FN 1, TN 5. That is 12 of 17 correct, precision 7/11, recall 7/8.
- **By check.**
  - `tests_run_before_finish`: 12 of 14 correct. 4 of 14 fall in the 0.4–0.6 band (R11, R12,
    R14, R15); outside the band, 10 of 10 are correct.
  - `plan_before_edit`: 0 of 3. All three are baseline-arm preambles scored 0.78–0.79.
- **Error causes:**
  - Question or policy defect: 3 (R02, R04, R06).
  - Policy defect in the band: 1 (R12).
  - Jev error in the band: 1 (R11).
- **Value against the baseline (trace rules alone).** The rules settle 97% of checks. Jev adds 17
  decisions the rules leave `ambiguous`:
  - For the test check it is reliable outside the band. Band routing would leave 4 cases for a
    person or a second question.
  - For the plan check it adds no information. It says `complied` for every generic preamble.
    That matters because `plan_written` is the manipulation check FCR-008 already calls
    undetectable.
- **Decision.** Keep the test check with band routing. Do not report the plan residue until its
  policy and question change. The sample (17 decisions, 8 positives) is too small for a frozen
  threshold, and replaying more jobs is the only way to grow it.

## 6. Design review against `jev-design.md`

### 6.1 Programming model and the deterministic stage

| Guidance | Raidar now | Finding |
|---|---|---|
| "Route deterministically, then judge"; "do not ask Jev what a regex can answer" | Rubric: no per-criterion router. Every applicable criterion is asked of every changed symbol (17,248 requests, 250,713 answers counting carries). | 26 of 45 criteria never reach 0.5. Several are code-decidable. `lonely_interface`, `one_subclass_hierarchy` and `interface_per_class` are counts over the inheritance graph that `codebase.py` already computes. `single_entry_registry` is a count of dict or registry entries. `parallel_small_collection`, `cache_for_literals`, `entry_exit_logging` and `nested_async_flows` are presence tests on AST nodes. Counting is jaggedness #2. A candidate filter in code would ask only where a candidate exists (a `mkdir`/`makedirs`/`open(…, "w")` call; a string literal compared with `==`/`in`; a decorator), cut cost and remove most of the noise floor. |
| Residue: "fix routing gaps in the deterministic stage, not with a broader question" | `trace_events.EDIT_TOOLS` knows `edit/write/multiedit/notebookedit`. Codex edits arrive as `exec` calls whose text holds `*** Begin Patch` / `*** Update File:` or as shell heredocs. | 0 of 488 events in the asked states have kind `edit`. Parsing Codex patch blocks and common heredoc writes in code turns most "unparsed write" ambiguities into deterministic decisions, before any question change. |
| Facts code can compute go in as computed fields | Residue state: raw event text plus `why_ambiguous`. Rubric state: raw source. | Residue: give `last_source_edit_index`, the candidate test commands with their exit evidence and each event's file target as fields; ordering ("after the last edit") is an indirection Jev should not do (jaggedness #4). Rubric: give the classification of each compared string (argparse dest or argv token, JSON wire field, internal constant) as computed facts. |

### 6.2 Question design (wording, primitive, atomicity, literal reading)

- **Primitive choice.** `noul` per criterion is right for the rubric: each criterion is an
  independent yes/no condition, and a Choice over 45 options would answer a different (relative)
  question. `noul` is right for the residue checks. `score` is not needed.
- **Wording.** The SCB criteria were written for an LLM judge reading whole files and are vendored
  byte for byte. As Jev questions they break several rules:
  1. **Criteria are examples, not definitions.** `true` = "It does, as in: <examples>", `false` =
     "It does not; these are not the pattern: <examples>". There is no exact condition, and
     boundary cases are missing. TypeSafe's `primitives/advanced` structure
     (`{question, focus}` instructions, `{what, not_for, examples}` criteria) exists for exactly
     this.
  2. **Literal reading (jaggedness #1).** `stringly_typed_logic` lists "Dispatch on string names of
     actions" as a positive, so Jev flags every argparse `args.command == "create"` chain
     (stance A) and even a single argv test (C08, C16, C18). Its negative, "String use only at IO
     boundaries with conversion to types", requires a *conversion* that a CLI boundary check never
     makes. The fix is an explicit `not_for`: argv and argparse tokens, spec-defined wire values,
     spec-defined filter tokens.
  3. **Examples from other languages** (`Promise.all`, `?.`, `private readonly`, Java interfaces)
     add tokens and distractors to a Python-only process.
  4. **Overlapping criteria double-count one line in ΣP.** `silent_resource_creation` and
     `auto_correction_without_warning` (whose positive is "auto-mkdir for missing paths in core
     logic") overlap, as do `default_on_required_field` and `null_coalescing_abuse`, and the three
     single-implementation interface criteria.
  5. **Criteria that need state their scope does not give.** `silent_resource_creation` and
     `silent_truncation` have the negative "documented in spec", but they are asked at `symbol`
     scope with no spec (stance B is this defect). `validation_mismatch` at `spec` scope judges a
     fragment against a whole multi-checkpoint contract. It reads "this symbol does not validate X"
     as a mismatch even when X is validated by the caller (C09, C21 and C45; C37 depends on a
     callee).
- **Atomicity.** `stringly_typed_logic` hides at least two judgments: whether a literal is an
  internal mode, state or kind, and whether it sits at an IO boundary. Split it, or compute the
  boundary in code and ask only about internal literals. `plan_before_edit` hides "is there a plan"
  and "is it before the first edit" (ordering belongs in code).
- **No-match / abstain.** A noul has none. The 0.4–0.6 band is the uncertainty outcome, and nothing
  routes it today (TypeSafe `primitives/noul`: "values in the middle can go to a person";
  `patterns/confidence-routing`).

### 6.3 State construction, including the codebase-scope template

- **Symbol scope** (`task` + `symbol`). Narrow, as the guidance asks. The weakness is the inputs
  it leaves out: the spec for spec-citing negatives (6.2.5) and callee contracts.
- **File scope** (whole file up to 60,000 characters) for 4 criteria that never fired. It is the
  largest state for the least signal (jaggedness #5).
- **Spec scope** (cumulative spec up to 40,000 characters) for 4 criteria. The cp8 spec is 34,909
  characters for a 1,000-character symbol. Retrieve the spec sections that name the symbol's
  command or resource in code, and send only those.
- **Codebase scope** (`codebase.py`). Well designed for its purpose:
  - a `focus` section with call sites and arguments, plus an `inventory` with the inheritance and
    interface graph;
  - built once per checkpoint with `ast`, under the 96,000-character cap, with clipping recorded.
  - In the scored cases it is small (603–713 characters), so `parallel_small_collection` and
    `implicit_default_parameters` were answered correctly at ≤ 0.05.
  - Its weakness is what it is used for: the class-level questions it serves are counts the index
    already knows (6.1).
  - Name-matched sites can conflate same-named symbols; the template says so in its docstring,
    and no scored case hit it.
- **Cap.** The 96,000-character state cap is a character proxy for TypeSafe's limit of 32k tokens
  for state plus the longest question (`models`). Dense code and JSON can tokenise at fewer than 3
  characters per token. Measure tokens from `usage.input_tokens` (recorded per call) rather than
  trusting the character cap.
- **Residue state.** The head-and-tail selection keeps plans and final runs. It also shows every
  session of the step (implementer, reviewer, retry) without saying which is "the agent", and it
  shows `why_ambiguous`, which states the deterministic stage's suspicion. Rules and questions
  should not depend on a hint the code could instead turn into a computed fact.

### 6.4 Composition and thresholds

- **The calibration unit is wrong.** Calibration cuts the **maximum** of up to 12 heterogeneous
  nouls with one threshold. jev-design: "a noul threshold does not transfer to a Choice over the
  same idea", and per-family rules "are easier to audit and fix than one blended score". With
  three criteria driving 91% of flags, the decision-level threshold is in effect a
  `stringly_typed_logic` threshold. Calibrate per (decision, criterion), with per-family thresholds
  and a band.
- **The consumer does not match the calibration.** The planned metric is ΣP (raw probabilities),
  while calibration fits a threshold on the max. A threshold says nothing about whether
  probabilities of 0.02–0.3 are calibrated, and those produce 80% of ΣP. Either report thresholded
  per-criterion counts (as the design memo §3.4 allows once a policy is enforced), or calibrate the
  probabilities (a reliability curve per criterion on labelled data) before trusting ΣP.
- **Residue composition.** `jev_ambiguous_compliance = min(check nouls)` mixes the plan and test
  checks into one number. Report one metric per check.
- **Threshold choice.** 0.75 was fitted on the calibration side of the relabel split, which is
  correct practice. But the label set it was fitted on is enriched, and its policy is undecided,
  so the frozen value would not carry over to `@3`. Choose thresholds again on the new version's
  labels.

### 6.5 Rules are binary

- Both processes state conditions of the thing judged. No question or criterion names who may do
  something.
- The `who asked` signal is absent from every state.
- The residue's open question, "does a review subagent's test run count as the agent's?", is
  about the **condition** (which sessions the step comprises), not permission. It belongs in the
  written policy as a condition.

### 6.6 Label provenance

- Every one of the 38 current labels is model-made (`claude-opus-5-5`), as are this evaluation's
  first-pass labels: one model family, no human reference.
- §14.7 records that the selector's `selection.json` (with Jev scores) sat beside the blind
  exports, so "the labellers did not read it" rests on self-report. This evaluator was also not
  fully blind (section 2).
- **Labels are graded at the decision level.** The stored label is `correct` or `false_positive`
  against "any question ≥ 0.5". The per-question labeller answers exist only in round artifacts and
  in `jev-spot-check.md`. They allow the perception test of development.md (Jev vs labeller
  consensus per atomic question) at no labelling cost. That test has not been run.
- **Enrichment.** The pools were enriched towards Jev positives, so FN = 0 everywhere and recall is
  unmeasured.
- **Split hygiene.**
  - The split is by trial with both arms of a batch together, which is good.
  - 160 identical states (same `cacheKey`) sit on both sides.
  - Audit trial ids are the pre-v2 form, so any new split must handle both forms.

### 6.7 The two contested stances

- **Stance A: CLI command-string dispatch as `stringly_typed_logic`.**
  - The literal rubric says positive ("Dispatch on string names of actions").
  - The rubric's negative ("String use only at IO boundaries …") and common Python practice say an
    argparse subcommand chain *is* the IO boundary.
  - The evaluator rejects A. Argparse `dest` dispatch is the boundary layer. The smell the
    criterion targets (internal modes, states and kinds as raw literals: C01, C24, C32 status
    literals; C03, C26 AST kinds) is separately and reliably caught.
  - Keeping A makes most CLI entry points smells in both arms, which adds size-proportional
    noise.
  - Either way it must become a written rule with examples, and the question must carry it as
    `not_for` or `true`. Today 5 of 14 false flags at 0.75 rest on it.
- **Stance B: silent `mkdir(parents=True, exist_ok=True)` as `silent_resource_creation`.**
  - It is the rubric's own first positive example.
  - The forge spec says "Forge persists all state to a local data directory" and "`--data-dir`
    specifies the directory for persistent state". That implies the directory and says nothing
    about creating it.
  - From a symbol-scope state Jev is right to say yes (C33 is correct state-only). The defect is
    the template: the negative "documented in spec" cannot be judged without the spec.
  - Recommendation: a written rule, "creating the user-specified data directory in a
    persistence path is not this smell; creating other resources silently is". Ask
    `silent_resource_creation` with the relevant spec excerpt, or decide the data-dir case in code
    (an AST match on `mkdir` of the `--data-dir` path).
  - This affects 195 flagged decisions store-wide (121 distinct states; the spot-check's 121 counts
    states), not one label.

## 7. Next iteration: prioritised changes and labelling

Nothing below is enforced. Each change is a new version, verified in a fresh process. **First
run: a Jev replay, not new agent trials.**

### 7.1 code-quality.rubric-smells (`scb-rubric@3`, policy `3`, new epoch)

1. **Write the policy (owner: user, then component).** Numbered rules with examples on both sides
   for at least `stringly_typed_logic` (stance A, boundary strings, spec tokens, AST kinds, status
   literals), `silent_resource_creation` and `auto_correction_without_warning` (stance B, data
   directory), and `validation_mismatch` (missing versus contradicting validation; validation done
   elsewhere). The labelling rubric is the same document. *Blocks every later step.*
2. **Raidar-owned question overlay (component: `scb-rubric.yaml`).** Keep the vendored SCB file
   and its sha256 as provenance, and add per-criterion `instructions`/`criteria` overrides in
   TypeSafe's structured form (`{question, focus}`, `{what, not_for, examples}`), Python-only
   examples. Version the overlay with the question set. Rewrite the three drivers first:
   `stringly_typed_logic`, `silent_resource_creation`, `validation_mismatch`.
3. **Deterministic candidate filter per criterion (component: `rubric.py`).** Ask a criterion
   only where code finds a candidate. Decide the count-type criteria (single implementation,
   single entry, presence of executors) in code from `codebase.py` and AST. Record skipped
   criteria as deterministic decisions with a rule name, so ΣP stays rebuildable from the audit.
4. **Scope fixes.**
   - Give `silent_resource_creation`, `silent_truncation` and `auto_correction_without_warning`
     the spec excerpt.
   - Narrow `spec` state to the sections that name the symbol's command or resource.
   - Drop file scope for the 4 criteria that never fired, or give them a smaller state.
   - De-duplicate overlapping criteria in ΣP.
5. **Composition and thresholds.** Calibrate per criterion (family). Route the 0.4–0.6 band.
   Report thresholded per-criterion counts, and keep ΣP only if a reliability curve shows the low
   probabilities are calibrated.
6. **R1 per question.** Sufficiency checks that each asked criterion's required inputs are present,
   not only that nothing was clipped.
7. **Replay.** `make jev-shadow-rubric SNAPSHOTS=… STORE=…` for each pilot-v2 job in a fresh
   process; confirm the records carry `scb-rubric@3`. The cost bound is about $2.3 (today's 15,499
   calls); the candidate filter should cut it.
8. **Tooling (low cost).** Move `export_cases.py` into `raidar jev export-cases` (Cases command
   for both profiles). Make the split recorder refuse a `cacheKey` on both sides.

### 7.2 process-compliance.residue (`process-residue@2`, policy `2`)

1. **Deterministic router (component: `trace_events.py`, `checks.py`).**
   - Parse Codex `exec` patch blocks (`*** Update File:` / `*** Add File:`) and common heredoc
     writes as edits.
   - Classify test commands and whether they ran: pytest missing, unittest discovering zero tests.
2. **Policy (owner: user).** Three rules:
   - which sessions count as "the agent" (implementer only, or review-loop subagents too);
   - whether a suite command that fails to start counts;
   - whether a generic "inspect …, then implement …" preamble is a plan.
   This ties to FCR-003 (tools missing) and FCR-008 (plan undetectable).
3. **State and questions.** Computed `last_source_edit_index` and per-candidate atomic nouls
   ("Is `candidate.command` a run of the project's test suite?"; "Does `message` list at least two
   concrete implementation steps?"), with ordering decided in code. Render edit bodies as
   `{path, kind: edit}` so clipping does not mark the input partial.
4. **Composition.** One metric per check instead of `min()`; route the band.
5. **Replay** over `jobs/pilot-v2`, `ruff-lint-02`, and the r3 job if the user approves that run.
   17 decisions cannot meet a ≥ 30 / ≥ 5 gate.

### 7.3 Labelling needed

| Labelling | Who | Size | Purpose |
|---|---|---|---|
| Stance A and B decisions, plus the residue policy rules (7.1.1, 7.2.2) | User | 5 rules | The policy the labels follow. Nothing else is decidable without it. |
| Perception test on existing `@2` data | Agent, no new labels | 45 decisions × asked questions (labeller answers already exist) | Separates Jev's perception from policy and composition (development.md §3.2) before redesign effort is spent. |
| Human spot-check of the 7 held-back decisions and the twins C07/C15/C17 | User | 10 decisions | A first non-model reference, and a fix for the twin inconsistency. |
| Per-criterion blind labels on the `@3` replay | Separate selector plus two blind labellers (agent), human confirmation of stance-sensitive rows | ≥ 30 per criterion family to be calibrated, ≥ 5 minority, half drawn at random from Jev negatives | Thresholds per family, recall measured, no enrichment bias. |
| Residue `@2` decisions after replay | Two blind labellers plus the user on band cases | All residue decisions (expect tens) | First labels for the residue policy. |

The evaluator's first-pass labels (45 rubric, 17 residue, agent kind) are in the tables above and
in the scratchpad files listed in section 9. They were **not** imported. Importing them as
`model` labels would add a third same-family label to the 38 already present, and would add labels
under a policy that is not yet written.

## 8. Limits of this evaluation

- **Same model family.** The evaluator is the same model family as both labellers, and only partly
  blind (section 2).
- **The sample is the relabel pool.** It is enriched towards Jev positives, so rates on it are not
  population rates.
- **Wider evidence was read from the recorded spec text and the states.** The snapshot files on
  disk were not opened, because the recorded states held the full symbol source and the cumulative
  spec. C37 and C44 stay R1 = 1 because their callees were not read.
- **Store-wide figures are Jev's answers, not verified labels.**

## 9. Reproduction

All files below are in the session scratchpad,
`/private/tmp/claude-501/-Users-adamjackson-Projects-complete-raidar/4bbb344c-08b8-464f-941e-9702963aa2f2/scratchpad/jev-eval/`.
That location is session-local, so section 7.1.8 proposes moving the exporter into the repo.

- `export_cases.py`: the exporter. It reads `<store>/jev-audit` only, applies supersessions and the
  recorded split, and writes mode-0600 JSONL.
- `out/rubric-cases.jsonl`, `out/rubric-labelled*.jsonl`, `out/residue-cases.jsonl`,
  `out/residue-unique.jsonl`: case lines.
- `out/rubric-labels-consensus.jsonl`: Raidar's current labels as reference labels.
- `out/labels-evaluator.jsonl`, `out/labels-evaluator-literal.jsonl`,
  `out/labels-residue-evaluator.jsonl`: this evaluation's agent labels.
- `out/evaluator-r2.jsonl`: per-case R1 and R2 with evidence.
- `out/sum-*.json`: summarizer outputs.

```sh
python3 export_cases.py jobs/pilot-v2/assessment-store out
bun ~/.omp/agent/skills/evaluate-jev/scripts/summarize.ts --cases-in out/rubric-labelled.jsonl \
  --version "code-quality.rubric-smells@2/scb-rubric@2" --labels-in out/labels-evaluator.jsonl --threshold 0.75
bun ~/.omp/agent/skills/evaluate-jev/scripts/summarize.ts --cases-in out/residue-unique.jsonl \
  --version "process-compliance.residue@1/process-residue@1" --labels-in out/labels-residue-evaluator.jsonl
```
