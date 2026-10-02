# Pilot v2 fault catalog (research-loop iteration 1)

- **Date:** 2026-10-02
- **Scope:** `python__forge__guided-pilot`, jobs `forge-02` (attempts b00-b06) and `forge-03` (b07-b15): both arms, all 16 attempts, all 8 checkpoints, 32 valid trials, 256 step results. The one-shot leg is out of scope.
- **Configuration:** `codex` 0.156.1, `openai/gpt-6-luna`, reasoning effort low; problem `scb/forge@38d627ecf668`, progression `continue`; executed lock `5a73637e8ac3` (migrated to schema v2 as `015c57e4f59c`); condition digests baseline `d8491ebcf57c`, guided `73cc19faeeb0`.
- **Method:** the `fault-catalog` skill (`.agents/skills/fault-catalog/`, analyse-job runbook) on the verifier records, grading logs, graded snapshots, agent sessions and review-loop records of every valid trial. The skill's evidence map defines the failure-mechanism classes used below.
- **Status of the numbers:** every count below was recomputed from persisted job artifacts. Fisher tests and the sensitivity view in §2 are exploratory (chosen after seeing the data). The experiment's verdicts are those of `analysis.md` and are not changed here.

Path abbreviations: `J` = `jobs/pilot-v2/python__forge__guided-pilot`, `P` = `problems/store/scb/forge/38d627ecf668`, `A` = `jobs/pilot-v2/gate-evidence/schema-v2/analysis/forge/analysis.md`. Trial `bNN-p0-<arm>` lives in `J/forge-02` for b00-b06 and `J/forge-03` for b07-b15.

---

## 1. The two answers

### Why is the guided arm worse?

**Observed.** The whole primary-outcome gap sits at checkpoints 1-2. Baseline solves them in 10/16 trials and guided in 2/16 (`A`: solved, continue view, 0.625 vs 0.125). In every guided trial that misses checkpoint 1 (14/16), the failing cases are the same two: `test_create_rejects_empty_name_string` and `test_create_rejects_empty_requirement_sets`. One trial (b04 guided) also misses two ordering tie-breaks. Baseline misses that same pair in 5/16 trials.

**Mechanism (observed).** Both cases pass only when the error detail contains a specific phrase: `name must be a non-empty string` and `requirement_sets must be a non-empty array` (`P/oracle/tests/test_checkpoint_1.py:568-572`, `:588-592`). The specification never states that phrase. It lists the constraint as `` `name` must be present and a non-empty string. `` (`P/task/steps/checkpoint_1/instruction.md:53`, `:128`). The verifier then reports, for example, `actual detail='name must be present and a non-empty string'` (`J/forge-02/trials/b00-p0-guided/raidar-oracle/steps/checkpoint_1/grading.log:92-93`).
- Guided implementers copied the specification's sentence into the error message in 14/16 trials. Baseline did so in 5/16 (two-sided Fisher p = 0.0032).
- The failing guided programs validate the input correctly. They reject the empty name and the empty list with the right category and exit code; only the wording differs.

**Inference.** Guided does not lose on checkpoints 1-2 because it validates badly. It loses because it reproduces the specification's wording more literally, and the verifier happens to assert a different, unspecified wording (FCR-002 acting on FCR-001). The review loop never changed the wording: it was already in `diffs/round_1.patch` in 14/14 failing trials, and no reviewer feedback mentions message wording (0/16). Which guided component causes the literal transcription is a hypothesis. The arm bundles five components and the review-loop's implementer preamble, so this data cannot attribute it.

### Why does neither arm ever solve checkpoint 3 or later?

**Observed.** 21 verifier cases fail in all 32 trials at the checkpoint where they first appear. Under `continue`, a checkpoint counts as solved only when every graded case passes, including earlier checkpoints' cases rerun as regressions. These cases therefore block every checkpoint from 3 on:

| Origin | Cases failing 32/32 | What the case asserts | What the specification says |
|---|---|---|---|
| checkpoint 3 | 4 (`test_bind_invalid_op_*`, `test_invalid_op_*`) | the `invalid_operation` detail contains the bad operation value (`detail_contains="invalid-op"`) | only the category and its priority (`P/task/steps/checkpoint_3/instruction.md:54-61`); the detail's content is not stated |
| checkpoint 6 | 13 (flash, module and tag errors) | `detail` **equals** the bare identifier (`P/oracle/tests/test_checkpoint_6.py:193`, `:208`) | error categories only (`P/task/steps/checkpoint_6/instruction.md:66-72`, `:121-131`); checkpoints 1-5 only ever assert `detail_contains` (0 exact-detail asserts in tests 1-5, 12 in test 6) |
| checkpoint 7 | 4 | compact, key-sorted JSON with no trailing newline (`test_revision_preserves_compact_json`), and `detail` equals the identifier | revision envelope only (`P/task/steps/checkpoint_7/instruction.md`); output byte format not stated |

Example: every trial's checkpoint-3 log shows `expected detail to contain 'invalid-op'; actual detail='Invalid operation for allocation(s): [...]'` (`J/forge-03/trials/b09-p0-baseline/raidar-oracle/steps/checkpoint_3/grading.log:176-179`).

**Consequence.** On this problem, with this verifier and in continue mode, `solve_rate` cannot exceed 2/8 = 0.25 for any trial, `reached` cannot exceed 2, and `final_solved` is 0 by construction. The primary outcome therefore measures only checkpoints 1-2, which turn on two unspecified wording literals. This is FCR-001, a Measurement fault owned by the problem and verifier, not an agent fault.

---

## 2. Exploratory sensitivity view (not a verdict)

To show how much FCR-001 shapes the outcome, the per-checkpoint outcomes were recomputed with 24 cases excluded: the 21 cases in the walls above, the checkpoint-1 wording pair, and `test_create_sorts_capabilities_even_with_duplicates` (checkpoint 5 calls capabilities a "set", `P/task/steps/checkpoint_5/instruction.md:9`, but the test expects duplicates to be kept; 26/32 fail). The exclusion rule was chosen after seeing the data, so this view is exploratory.

| Checkpoint | Adjusted solved, baseline | Adjusted solved, guided | Adjusted case pass rate, baseline | Adjusted case pass rate, guided |
|---|---|---|---|---|
| 1 | 15/16 | 15/16 | 0.998 | 0.997 |
| 2 | 15/16 | 15/16 | 0.999 | 0.998 |
| 3 | 7/16 | 11/16 | 0.984 | 0.993 |
| 4 | 7/16 | 11/16 | 0.986 | 0.994 |
| 5 | 7/16 | 11/16 | 0.976 | 0.995 |
| 6 | 1/16 | 4/16 | 0.965 | 0.990 |
| 7 | 0/16 | 1/16 | 0.920 | 0.981 |
| 8 | 0/16 | 0/16 | 0.829 | 0.892 |

Adjusted mean C: baseline 0.957, guided 0.980; Δ +0.023, bootstrap 95% CI [−0.002, +0.047].

On the cases the specification determines, the direction reverses: guided is at least as good at every checkpoint and better from checkpoint 3 on. This matches `A`, where the headline S under `scb-canonical@2` (C = mean case pass rate) is inconclusive, 76.87 vs 78.42 for guided.

---

## 3. Index

| ID | Fault category | Category | Owner | Status | First observed | Current target |
|---|---|---|---|---|---|---|
| [FCR-001](#fcr-001) | Verifier asserts error-detail content or output format that the specification does not determine | Measurement | problem / verifier | `open` | guided-pilot forge-02 | — |
| [FCR-002](#fcr-002) | Specification constraint prose is copied verbatim into user-facing error details | Behavioral | component (blocked by the hidden-test firewall) | `open` | guided-pilot forge-02 | — |
| [FCR-003](#fcr-003) | The condition requires verification tools the agent environment does not provide | Process | component | `targeted` | guided-pilot forge-02 | python__forge__guided-r3 |
| [FCR-004](#fcr-004) | Condition components are delivered but not consumed by the agent | Process | component | `targeted` | guided-pilot forge-02 | python__forge__guided-r3 |
| [FCR-005](#fcr-005) | Command-surface and argument-parser failures escape the program's error contract | Integration | component | `targeted` | guided-pilot forge-02 | python__forge__guided-r3 |
| [FCR-006](#fcr-006) | A cross-cutting contract change is not propagated to earlier commands | Structural | component | `targeted` | guided-pilot forge-02 | python__forge__guided-r3 |
| [FCR-007](#fcr-007) | A third-party runtime dependency is unavailable to the offline verifier | Measurement | harness (Raidar verifier) | `open` | guided-pilot forge-02 | — |
| [FCR-008](#fcr-008) | The plan step is neither instructed nor detectable, so the plan manipulation check cannot separate | Process | component + analysis | `targeted` | guided-pilot forge-02 | python__forge__guided-r3 |

The records below carry the pilot v2 forge configuration (§ header) unless a row says otherwise.

---

## FCR-001

### FCR-001 — Verifier asserts error-detail content or output format that the specification does not determine

- **Category:** Measurement
- **Owner:** problem / verifier (SCB `forge`; out of scope for components, and problems are not changed here)
- **Status:** `open`
- **Description:** hidden tests assert an error detail's exact wording, its content (the offending value) or exact equality with an identifier, or the output's byte format. None of these is stated in the checkpoint's specification. Agents cannot infer them, so the assertions fail in every trial whatever the condition. Under `continue` they carry forward as failing regressions to every later checkpoint.
- **Detection signal:** a case that fails at its origin checkpoint in every valid trial of every arm, whose asserted value does not appear in that checkpoint's `instruction.md`; failure mechanism `detail_exact`, `detail_contains` or an output-format assertion.
- **Arm differential:** none for the 21 wall cases (32/32 in both arms). The checkpoint-1 pair is arm-dependent through FCR-002.
- **Example evidence:** `J/forge-03/trials/b09-p0-baseline/raidar-oracle/steps/checkpoint_3/grading.log:176-179` (`expected detail to contain 'invalid-op'`) against `P/task/steps/checkpoint_3/instruction.md:54-61`.
- **Observed configurations:**

  | Experiment / job | Arm | Checkpoints | Evidence |
  |---|---|---|---|
  | guided-pilot forge-02 + forge-03 | baseline + guided | 3 | 4 cases fail 16/16 + 16/16 (`detail_contains` the operation value) |
  | guided-pilot forge-02 + forge-03 | baseline + guided | 6 | 13 cases fail 16/16 + 16/16; of the failure instances new at checkpoint 6, 206 (baseline) and 208 (guided) are `detail_exact` |
  | guided-pilot forge-02 + forge-03 | baseline + guided | 7 | 4 cases fail 16/16 + 16/16 (compact sorted JSON, `detail` equals the identifier); e.g. `J/forge-03/trials/b11-p0-guided/raidar-oracle/steps/checkpoint_7/grading.log:1307-1310` |
  | guided-pilot forge-02 + forge-03 | baseline 5/16, guided 14/16 | 1 (carried to 8) | wording pair (`detail_contains`), see FCR-002 |
  | guided-pilot forge-02 + forge-03 | baseline 14/16, guided 12/16 | 5 (carried to 8) | "capability set" read as deduplicated; test keeps duplicates (`P/oracle/tests/test_checkpoint_5.py:492-501`) |

- **Impact:** the solve-based outcomes are capped (`solve_rate` ≤ 0.25, `reached` ≤ 2, `final_solved` = 0 for every trial), so `solve_rate`, the primary outcome under `continue`, measures only checkpoints 1-2. The checkpoint-1 pair alone decides the A/B verdict on `solve_rate` and `reached`. Case pass rate (C) and S are affected far less, since each wall case is one of 101-295 graded cases.
- **Working hypothesis:** the SCB reference solution fixed these literals and the tests were written against it. The third-party audit's F8 class ("core tests assert values the spec does not determine") did not sample `forge` (`docs/reboot/oracle-validation.md:186`).
- **Resolution criterion:** not a component target. A verifier-side resolution would need every asserted literal to be stated by its checkpoint's specification, or the walls to be removed from the solve-based outcomes by a pre-registered analysis rule.
- **Target experiment:** —
- **Solution:** —
- **Validation evidence:** —
- **History:**
  - 2026-10-02 — created from pilot v2 forge (forge-02, forge-03).

## FCR-002

### FCR-002 — Specification constraint prose is copied verbatim into user-facing error details

- **Category:** Behavioral
- **Owner:** component, but blocked by the hidden-test firewall: the wording the verifier wants is known only from hidden tests, so a component that steered towards it would teach to the test.
- **Status:** `open`
- **Description:** the implementer turns the specification's constraint sentences into error messages word for word ("name must be present and a non-empty string"). Where the verifier asserts an unspecified shorter wording (FCR-001), correct validation fails.
- **Detection signal:** production error strings equal to a constraint sentence of the specification, at the cases FCR-001 lists for checkpoint 1.
- **Arm differential:** guided 14/16 vs baseline 5/16 at checkpoint 1 (two-sided Fisher p = 0.0032, exploratory). The wording is fixed in the implementer's first round in 14/14 failing guided trials (`review-loop/diffs/round_1.patch`), and reviewers never raise it (0/16 checkpoint-1 reviews mention message wording).
- **Example evidence:** `J/forge-02/trials/b00-p0-guided/steps/checkpoint_1/artifacts/app/forge.py:55` (`"name must be present and a non-empty string"`) vs `J/forge-02/trials/b00-p0-baseline/steps/checkpoint_1/artifacts/app/forge.py:63` (`"name must be a non-empty string"`).
- **Observed configurations:**

  | Experiment / job | Arm | Checkpoints | Evidence |
  |---|---|---|---|
  | guided-pilot forge-02 + forge-03 | guided (`73cc19faeeb0`) | 1 (carried to 8) | 14/16 trials; 12 copy the sentence verbatim, 2 write "must be a present, non-empty string" (b01, b07) |
  | guided-pilot forge-02 + forge-03 | baseline (`d8491ebcf57c`) | 1 (carried to 8) | 5/16 trials (b02, b04, b07, b10, b12) |

- **Impact:** with FCR-001, all of the guided arm's `solve_rate` (−0.125) and `reached` (−1.0) deficit (`A`). The programs are not less correct.
- **Working hypothesis:** guided context pushes towards literal transcription. Candidates are the rules file's "Read the whole specification ... re-read it", the skill's "read the whole specification", and the review-loop implementer preamble "Complete the checkpoint specification below". Untested; the bundle cannot attribute it.
- **Resolution criterion:** none set. Any component targeted at the wording would cross the hidden-test firewall. The r3 candidate rewrites the rules file and may shift this signal incidentally; it is to be observed, not targeted.
- **Target experiment:** —
- **History:**
  - 2026-10-02 — created from pilot v2 forge.

## FCR-003

### FCR-003 — The condition requires verification tools the agent environment does not provide

- **Category:** Process
- **Owner:** component
- **Status:** `targeted`
- **Description:** the guided rules and skill require the agent to write tests and run them, and lint-config ships a `ruff.toml`. The agent image has neither pytest nor ruff, so agents try them, fail, and fall back to `unittest`, or report that they could not run the suite.
- **Detection signal:** `No module named pytest` / ruff-not-found in implementer or reviewer session outputs, and reviewer feedback saying the tests could not be run.
- **Arm differential:** guided only (baseline never tried pytest).
- **Example evidence:** `J/forge-02/trials/b00-p0-guided/steps/checkpoint_1/agent/review-loop/stage_1_implement/sessions/2026/10/01/rollout-2026-10-01T11-27-30-01a0f738-31fe-7a73-80a6-2febd13323cb.jsonl:40` (pytest missing); reviewer: "The test suite could not be run because `pytest` is not installed" (`J/forge-02/trials/b00-p0-guided/steps/checkpoint_1/agent/review-loop/review-loop.json:81`).
- **Observed configurations:**

  | Experiment / job | Arm | Checkpoints | Evidence |
  |---|---|---|---|
  | guided-pilot forge-02 + forge-03 | guided | 1-8 | pytest missing in 65/128 implementer, 30/128 first-review, 21/93 fix-round and 25/93 second-review sessions; ruff not found 17 times and never ran; 288 `unittest` runs in implementer sessions. pytest later succeeded in 55 calls after agents installed it (3 implementer sessions ran installs) |

- **Impact:** wasted turns and uncertain self-verification in an arm that already costs 4.5× baseline ($0.158 vs $0.035 per trial, `A`). `tests_run_before_finish` still separated (0.947 vs 0), because `unittest` counts.
- **Working hypothesis:** stating the available tools removes the failed attempts without changing what is verified.
- **Resolution criterion (pre-registered for r3):** in the candidate arm, implementer sessions with a pytest or ruff not-found error ≤ 5% of steps (pilot: 65/128 = 51%), with `tests_run_before_finish` ≥ 0.9.
- **Target experiment:** `python__forge__guided-r3` (change C4).
- **History:**
  - 2026-10-02 — created; `open` → `targeted` (r3 locked `12f2b2fb05fc`, not run).

## FCR-004

### FCR-004 — Condition components are delivered but not consumed by the agent

- **Category:** Process (treatment fidelity)
- **Owner:** component
- **Status:** `targeted`
- **Description:** components pass their discovery probes (the component is installed and discoverable), yet the agent does not use them. The arm's effect then comes from fewer components than the condition declares, and the inert ones add cost and confounds.
- **Detection signal:** per step, whether the agent opened the skill file, whether hook output reached the agent, whether a shipped tool config was usable.
- **Arm differential:** guided only.
- **Example evidence:** `skills@1`: `SKILL.md` opened in 3/128 implementer sessions (b02 guided checkpoints 1-2, b07 guided checkpoint 1; e.g. `J/forge-02/trials/b02-p0-guided/steps/checkpoint_1/agent/review-loop/stage_1_implement/sessions/2026/10/01/rollout-2026-10-01T12-16-03-01a0f764-a3b5-7de2-945a-481d0bdd4c13.jsonl:13`). `stop-hook@1`: maps to Codex `notify`, whose output the agent never sees (gate packet F5). `lint-config@1`: ships `ruff.toml`, but ruff is absent (FCR-003).
- **Observed configurations:**

  | Experiment / job | Arm | Checkpoints | Evidence |
  |---|---|---|---|
  | guided-pilot forge-02 + forge-03 | guided | 1-8 | skill read 3/128 steps; stop-hook invisible 128/128; ruff runnable 0/128 |

- **Impact:** the pilot's guided effect is effectively the effect of `rules-file@1` plus `review-loop@2`. Claims about skills, the stop hook or the lint config are unsupported.
- **Resolution criterion (pre-registered for r3):** every component in the candidate condition shows use in ≥ 90% of steps (`AGENTS.md` loaded, which Codex does in every session, and review-loop stages present).
- **Target experiment:** `python__forge__guided-r3` (change C5: fold the skill's steps into the rules file; remove `skills@1`, `lint-config@1`, `stop-hook@1`).
- **History:**
  - 2026-10-02 — created; `open` → `targeted`.

## FCR-005

### FCR-005 — Command-surface and argument-parser failures escape the program's error contract

- **Category:** Integration
- **Owner:** component
- **Status:** `targeted`
- **Description:** the specification defines one error contract (exit code 1, a JSON object with `error` and `detail` on stdout). Invalid or unimplemented invocations instead reach the argument parser's default handler, which prints usage to stderr and exits 2. Examples are an unknown subcommand the specification says must return `unknown_command`, and conflicting or missing mode flags.
- **Detection signal:** failing cases of mechanism `cli_surface_missing` (`usage:`, `invalid choice`, `unrecognized arguments` in the failure block).
- **Arm differential:** new-at-checkpoint failures of this class: baseline 74 (checkpoints 3/5/6/7/8: 15/20/10/17/12), guided 22 (4/0/2/9/7). `test_tag_update_unknown_command`: baseline 12/16, guided 3/16. `test_delete_requires_exactly_one_mode_flag[both_flags]`: baseline 9/16, guided 2/16.
- **Example evidence:** `J/forge-03/trials/b09-p0-baseline/raidar-oracle/steps/checkpoint_6/grading.log:769` (`forge.py tag: error: argument command: invalid choice: 'update'`) against `P/task/steps/checkpoint_6/instruction.md:119`.
- **Impact:** a whole command group fails at once and keeps failing as regressions. Guidance already reduces it; it is still present in guided.
- **Resolution criterion (pre-registered for r3):** candidate `cli_surface_missing` new-at-checkpoint failures ≤ 50% of the incumbent's over the same 16 attempts, with no increase in `wrong_error_category`.
- **Target experiment:** `python__forge__guided-r3` (change C2).
- **History:**
  - 2026-10-02 — created; `open` → `targeted`.

## FCR-006

### FCR-006 — A cross-cutting contract change is not propagated to earlier commands

- **Category:** Structural
- **Owner:** component
- **Status:** `targeted`
- **Description:** when a checkpoint changes something every command shares (checkpoint 7's revision envelope "supersedes all earlier non-`version` response-body shapes"), the agent applies it to new commands and misses or breaks earlier ones. Earlier checkpoints' cases then fail as regressions.
- **Detection signal:** regression breaks: a case that passed at the previous step of the same trial and fails now.
- **Arm differential:** checkpoint 7, baseline 4 trials with 105 breaks (b01 19, b03 39, b05 6, b13 41), guided 1 trial with 1 break. Regression breaks are 0 in both arms at checkpoints 1-5.
- **Example evidence:** b13 baseline checkpoint 7, allocation output without the `allocations` collection key (`_helpers.py:231` assertion in `J/forge-03/trials/b13-p0-baseline/raidar-oracle/steps/checkpoint_7/grading.log`); b03 baseline checkpoint 7, a `KeyError` crash on `allocation create` breaking 39 earlier cases.
- **Impact:** the largest single-step drops in case pass rate (b03 baseline 0.90 → 0.70, b13 baseline 0.87 → 0.66 at checkpoint 7). G (regression) in `A` moves the other way (guided worse), so the two measures disagree; see Unresolved candidates.
- **Resolution criterion (pre-registered for r3):** candidate checkpoint-7 regression breaks ≤ incumbent's, and no trial with ≥ 5 breaks at any checkpoint except FCR-007 crashes.
- **Target experiment:** `python__forge__guided-r3` (change C3).
- **History:**
  - 2026-10-02 — created; `open` → `targeted`.

## FCR-007

### FCR-007 — A third-party runtime dependency is unavailable to the offline verifier

- **Category:** Measurement (harness fidelity)
- **Owner:** harness (Raidar verifier)
- **Status:** `open`
- **Description:** checkpoint 8 requires reading YAML rules files (`P/task/steps/checkpoint_8/instruction.md:9`). The Python standard library has no YAML parser. Agents that import PyYAML at module top level and declare it in `pyproject.toml` work in the agent environment, but the offline grader (`UV_OFFLINE`, `UV_NO_SYNC`) cannot install it. Every case then fails with `ModuleNotFoundError: No module named 'yaml'`. Native SCB grades with network, so this is the fidelity gap already recorded in gate packet §11.11.
- **Detection signal:** `ModuleNotFoundError` for a dependency declared in the snapshot's `pyproject.toml`, in `grading.log`.
- **Arm differential:** none (1 trial per arm).
- **Example evidence:** `J/forge-02/trials/b06-p0-guided/steps/checkpoint_8/artifacts/app/forge.py:10` (`import yaml`) with `dependencies = ["PyYAML>=6.0"]`; `J/forge-03/trials/b07-p0-baseline/steps/checkpoint_8/artifacts/app/forge.py:13`.
- **Observed configurations:**

  | Experiment / job | Arm | Checkpoints | Evidence |
  |---|---|---|---|
  | guided-pilot forge-02 | guided b06 | 8 | case pass rate 0.00; 221 earlier-passing cases broken |
  | guided-pilot forge-03 | baseline b07 | 8 | case pass rate 0.00; 216 earlier-passing cases broken |

- **Impact:** a correct-in-agent-environment submission scores 0 at checkpoint 8, which lowers C and S for those trials. Symmetric here, so it adds noise rather than bias.
- **Resolution criterion:** verifier parity: a snapshot that declares runtime dependencies is graded with them installed (from a pinned, offline wheel cache or a network-enabled install step), or the problem is recorded as stdlib-only. Owner decision.
- **Target experiment:** — (not a component target: a "stdlib only" rule would hide a harness gap and push agents to hand-write a YAML parser).
- **History:**
  - 2026-10-02 — created from pilot v2 forge.

## FCR-008

### FCR-008 — The plan step is neither instructed nor detectable, so the plan manipulation check cannot separate

- **Category:** Process (manipulation check)
- **Owner:** component (instruction) + analysis (check selection)
- **Status:** `targeted`
- **Description:** `process-compliance@1` `plan_written` passes only on a plan tool call (`update_plan`, `TodoWrite`, ...) or a write to a plan file (`PLAN.md`, `plans/...`) before the first source edit (`orchestrator/src/raidar/scorers/process_compliance/checks.py:88`, `trace_events.py:32-33`, `:60-64`). No guided component asks for a plan. Codex 0.156.1 in these sessions exposes only the `exec` tool, with no `update_plan` call in any session. A plan file is therefore the only way to satisfy the check.
- **Detection signal:** `plan_written` share per arm in the manipulation check.
- **Arm differential:** 0 vs 0 (`A`: manipulation check `not_separated`; `tests_run_before_finish` 0 vs 0.947, p = 5e-05).
- **Impact:** the overall manipulation verdict is `not_separated` by construction.
- **Analysis gap (observed in code):** the requested checks are fixed: `manipulation_check` takes a `checks` argument (`orchestrator/src/raidar/analysis/manipulation.py:202`), but `raidar analyze` never passes it, so it always uses `DEFAULT_CHECKS` (`manipulation.py:49`, call at `orchestrator/src/raidar/analysis/report.py:210`), and the overall verdict needs every check to separate. In an incumbent-vs-candidate experiment where both arms are guided, `tests_run_before_finish` is expected to tie (≈ 0.95 vs ≈ 0.95). The overall verdict of r3 will then be `not_separated` even if the plan instruction works. Per-check verdicts stay readable. Declaring the expected checks per experiment needs a schema and analysis change, which is not made here.
- **Resolution criterion (pre-registered for r3):** candidate `plan_written` share ≥ 0.8 and the per-check verdict `separated` against the incumbent.
- **Target experiment:** `python__forge__guided-r3` (change C1).
- **History:**
  - 2026-09-30 — first recorded as gate packet F4.
  - 2026-10-02 — created as a record; `open` → `targeted`.

---

## 4. Unresolved candidates

Recorded as evidence only; distinctness or mechanism is not settled.

- **Newly gated features accepted without enforcement (checkpoint 8).** `accepted_invalid_input` new-at-checkpoint-8 failures: baseline 82, guided 21 (role and rules gating not enforced, e.g. `test_missing_role_forbidden_with_rule` baseline 12/16, guided 6/16). It may be requirement-coverage loss under a long specification, not distinct from FCR-005 or FCR-006. Evidence: `J/forge-03/trials/b09-p0-baseline/raidar-oracle/steps/checkpoint_8/grading.log`.
- **Wrong error category at checkpoint 8, guided-heavy.** `wrong_error_category` new-at-checkpoint-8: guided 32, baseline 6. Not yet read case by case.
- **G (regression) and regression breaks disagree.** `A` reports G worse for guided (0.183 vs 0.042), while verifier-level regression breaks favour guided (checkpoint 7: 105 vs 1). G is a code-attribution measure (changed symbols attributed to failing regression cases). The disagreement needs a reading of `code-quality@1`'s G inputs before either is used as a fault signal.
- **Review rounds without convergence.** `review-loop@2` ended `rounds_exhausted` in 64/128 guided steps and `review_approved` in 63. There is no evidence yet that review rounds changed verifier outcomes in either direction, and checkpoint-1 wording was never changed by review. Measuring it needs grading the round-1 snapshot, which Raidar does not keep.

## 5. Reconciliation result

```yaml
reconciled_faults:
  - {canonical_id: FCR-001, exact_fault_type: "Verifier asserts error-detail content or output format that the specification does not determine", category: Measurement, owner: problem / verifier, status: open, record_link: "docs/reboot/pilot-v2-fault-catalog.md#fcr-001", changed_record_ids: [FCR-001]}
  - {canonical_id: FCR-002, exact_fault_type: "Specification constraint prose is copied verbatim into user-facing error details", category: Behavioral, owner: component (firewall-blocked), status: open, record_link: "docs/reboot/pilot-v2-fault-catalog.md#fcr-002", changed_record_ids: [FCR-002]}
  - {canonical_id: FCR-003, exact_fault_type: "The condition requires verification tools the agent environment does not provide", category: Process, owner: component, status: targeted, record_link: "docs/reboot/pilot-v2-fault-catalog.md#fcr-003", changed_record_ids: [FCR-003]}
  - {canonical_id: FCR-004, exact_fault_type: "Condition components are delivered but not consumed by the agent", category: Process, owner: component, status: targeted, record_link: "docs/reboot/pilot-v2-fault-catalog.md#fcr-004", changed_record_ids: [FCR-004]}
  - {canonical_id: FCR-005, exact_fault_type: "Command-surface and argument-parser failures escape the program's error contract", category: Integration, owner: component, status: targeted, record_link: "docs/reboot/pilot-v2-fault-catalog.md#fcr-005", changed_record_ids: [FCR-005]}
  - {canonical_id: FCR-006, exact_fault_type: "A cross-cutting contract change is not propagated to earlier commands", category: Structural, owner: component, status: targeted, record_link: "docs/reboot/pilot-v2-fault-catalog.md#fcr-006", changed_record_ids: [FCR-006]}
  - {canonical_id: FCR-007, exact_fault_type: "A third-party runtime dependency is unavailable to the offline verifier", category: Measurement, owner: harness, status: open, record_link: "docs/reboot/pilot-v2-fault-catalog.md#fcr-007", changed_record_ids: [FCR-007]}
  - {canonical_id: FCR-008, exact_fault_type: "The plan step is neither instructed nor detectable, so the plan manipulation check cannot separate", category: Process, owner: component + analysis, status: targeted, record_link: "docs/reboot/pilot-v2-fault-catalog.md#fcr-008", changed_record_ids: [FCR-008]}
```

All entries: experiment `python__forge__guided-pilot`, jobs `forge-02` and `forge-03`, model `openai/gpt-6-luna`, effort low.

## 6. Limitations

- One problem, one model at one effort, 16 paired attempts. The categories are reusable, but their rates are specific to this configuration.
- The failure-mechanism classification is a regex over `grading.log` failure blocks (`.agents/skills/fault-catalog/references/raidar-evidence-map.md`). `value_mismatch` is a catch-all and was not read case by case.
- The Fisher test (FCR-002) and the sensitivity view (§2) were chosen after seeing the data.
- Session-level counts (FCR-003, FCR-004) come from searching tool calls and outputs in the Codex rollouts. A search hit is not proof that a step did or did not verify its work.
- Hidden tests were read to classify failures. That knowledge is deliberately kept out of the proposed components (`experiments/ab-test/python__forge__guided-r3/rationale.md` §3).
