# python__forge__guided-r3: rationale

- **Status:** proposed. Validated and locked (`experiment.lock.json`, lock `d4e2c9171a28`: `12f2b2fb05fc` relocked with the forge errata `9a6dec369dea` pinned, no other execution change); **not executed**.
- **Update (2026-10-04).** The status line above is historical.
  - **Run:** r3 was executed as jobs `r3-01`, `r3-04`, `r3-05` and `r3-06`; the results are in gate packet §14.11.
  - **Scorer:** the experiment now declares `process-compliance@2` (deterministic trace checks only; Jev parked), and those jobs were rescored under it. This was an assessment-section relock; `lock_sha256` did not move.
  - **Why `@1` still appears below:** every mention of `process-compliance@1` in this file is historical. `@1` is the scorer this file pre-registered, and the text is kept as written.
  - **What `@2` decides:** `plan_written` (a plan tool or plan file before the first source edit) and `tests_run_before_finish` (a test run after every source edit), from the trace alone. A check the trace cannot decide is `insufficient_evidence`, never a Jev decision.
  - **Commands:** the commands in section 7 now name `@2`.
- **Iteration:** research loop, manual iteration 1, built from the pilot v2 fault catalog (`docs/reboot/pilot-v2-fault-catalog.md`, FCR-001 to FCR-008).
- **Question:** on `scb/forge@38d627ecf668`, does the candidate guided condition (`guided-r3`) beat the incumbent pilot v2 guided condition (`guided-r2`)?

## 1. Design

| | Incumbent (`guided-r2`, baseline arm) | Candidate (`guided-r3`) |
|---|---|---|
| Shared | `prompt-wrapper@1` starter framing (both arms, byte-identical) | same |
| Rules | `rules-file@1`, pilot `AGENTS.md` | `rules-file@1`, revised `AGENTS.md` (C1-C5) |
| Skill | `skills@1` `checkpoint-discipline` | removed; its steps are folded into `AGENTS.md` (C5) |
| Lint config | `lint-config@1` `ruff.toml` | removed (C5) |
| Turn-end hook | `stop-hook@1` byte-compile | removed (C5) |
| Review loop | `review-loop@2`, 2 rounds | unchanged |

Everything else is fixed at the pilot's values: problem pin, agent (`codex` 0.156.1, `gpt-6-luna`, effort low), protocol (`continue`, 1800 s agent and 900 s verifier timeouts, artifact snapshots), scorers (`correctness@1`, `code-quality@1`, `resource-usage@1`, `process-compliance@1` as pre-registered; `@2` since 2026-10-04, see the update above), headline metric `scb-canonical@2`, and the verdict thresholds (min pairs 5, max exclusion 20%, alpha 0.05). The run uses 16 attempts with seed 20261002.

**The incumbent is the pilot's guided condition exactly.** Its files are byte-identical copies, and `make experiment-plan` reports its condition digest as `73cc19faeeb0`, the same digest the pilot v2 guided trials carry. Only the arm digest differs, because the arm and condition ids differ.

**Why a new experiment.** The baseline arm is the incumbent guided condition, not the unguided baseline, so this is a new question (AGENTS.md: a new experiment for a new baseline arm). The unguided arm is not included: its contrast with `guided-r2` is already measured.

## 2. Changes, each tied to a fault category

| Change | Targets | What changes | Mechanism | Expected signal |
|---|---|---|---|---|
| **C1** plan file | FCR-008 | `AGENTS.md`: write `PLAN.md` at the workspace root as a separate first write, before any source edit, each checkpoint | `process-compliance@1` (historical; `@2` also passes `plan_written` on it) counts a write to `PLAN.md` before the first edit as a plan; Codex here exposes no plan tool | `plan_written` ≥ 0.8 in the candidate, ≈ 0 in the incumbent |
| **C2** error contract on every failure path | FCR-005 | `AGENTS.md`: bad or missing arguments, unknown commands and conflicting flags follow the specification's error contract; never the parser's own usage text or exit code | removes the argparse default path that produced most `cli_surface_missing` failures | `cli_surface_missing` new-at-checkpoint failures ≤ 50% of the incumbent's |
| **C3** propagate cross-cutting changes | FCR-006 | `AGENTS.md`: a change to something every command shares (output shape, error format, global flag) is applied to every command, old and new | targets the checkpoint-7 envelope migration that broke earlier commands | checkpoint-7 regression breaks ≤ incumbent's |
| **C4** available test tools | FCR-003 | `AGENTS.md`: pytest and ruff are not installed; write `unittest` tests and run `python3 -m unittest discover`; do not install test tools | removes failed pytest/ruff attempts and install detours; `unittest` is already what agents fall back to and what `tests_run_before_finish` counts | sessions with a pytest/ruff not-found error ≤ 5% of steps; `tests_run_before_finish` ≥ 0.9 |
| **C5** remove inert components | FCR-004 | fold the skill's six steps into `AGENTS.md`; drop `skills@1` (opened in 3/128 steps), `stop-hook@1` (output invisible under Codex) and `lint-config@1` (no ruff) | Codex loads `AGENTS.md` in every session; the condition then declares only what the agent consumes | every candidate component shows use in ≥ 90% of steps |

The pre-registered resolution criteria are those in the catalog's records. They were set before this experiment has any data.

## 3. Hidden-test firewall

The fault analysis read the hidden verifier tests to classify failures. None of that knowledge is in the candidate. Every line of `components/guided-r3/AGENTS.md` follows from the specification the agent sees, from the agent environment (which tools exist), from the scorer's documented rules (what counts as a plan), or from general engineering practice:

- C2 restates the specification's own contract ("all errors produce exit code 1 and write a JSON error object to stdout"); it names no command, category or message.
- C3 restates a general rule; checkpoint 7's specification says the envelope "supersedes all earlier ... response-body shapes".
- C1 follows the process-compliance definition, not the verifier.

**Excluded on purpose:**
- Any guidance on error-message wording, on what a detail must contain (the offending value, the bare identifier) or on JSON byte format (compact, sorted, no trailing newline). These would target FCR-001 and FCR-002, but the right answers are known only from the hidden tests. Steering towards them would make the condition a carrier of answer-key knowledge.
- "Use only the standard library" (FCR-007). It would hide a verifier fidelity gap, and checkpoint 8 requires YAML, which the standard library cannot parse.

## 4. Deliberately not addressed

| Record | Why not a component change here | Owner and next step |
|---|---|---|
| FCR-001 (unspecified verifier literals) | measurement fault; problems are not changed, and teaching the literals crosses the firewall | problem / verifier: decide whether to report it upstream (SCB) and whether solve-based outcomes need a pre-registered treatment of wall cases |
| FCR-002 (verbatim spec prose in messages) | the "better" wording is known only from hidden tests | observe only: C5 rewrites the rules file and may shift it incidentally; report the checkpoint-1 wording share per arm |
| FCR-007 (offline verifier lacks declared dependencies) | harness gap, symmetric across arms | Raidar verifier: dependency parity with native SCB grading |

## 5. Predicted outcome and how to read it

- **`solve_rate` and `reached`** (the primary outcome under `continue`) are capped at checkpoints 1-2 by FCR-001 and decided there by the wording pair (FCR-002). No change here targets them. Expect `inconclusive`. A `worse` or `improved` verdict here would mostly be a shift in FCR-002's wording share, so read it with that share.
- **Case pass rate C and headline S** are where C2-C5 can show up (checkpoints 3-8). `min_effects` sets δ = 0.02 for C and 2 points for S. Pilot v2's guided-vs-baseline SDs (S 11.0) suggest 16 pairs are underpowered for 2 points of S. The run's sample-size projection will say how many pairs a confirmatory run needs.
- **Manipulation check:** read it per check. `plan_written` should separate (C1). `tests_run_before_finish` should tie, because both arms are guided and already run tests at about 0.95. Raidar's overall verdict requires every default check to separate (`DEFAULT_CHECKS`, not configurable per experiment), so **the overall verdict will be `not_separated` by construction**. This is a known analysis limitation (FCR-008, analysis gap), not evidence against C1.
- **Process signals** (pytest/ruff not-found errors, skill use, `cli_surface_missing`, regression breaks) are read from the verifier records and sessions with the `fault-catalog` skill. They are not `raidar analyze` outputs.

## 6. Attribution limits

C1-C5 ship together in one candidate, so an overall effect cannot be attributed to one change. Each record's detection signal is read separately, and C1, C2 and C4 have signals that only their own change should move. If the candidate's C or S differs from the incumbent's, the follow-up is a factorial or leave-one-out experiment on C2 and C3.

## 7. Cost and running it

- Pilot v2 guided trials averaged $0.158 nominal (Harbor token-priced, ChatGPT session auth). 32 guided trials come to about $5. Wall time is about 8 h at concurrency 3: 32 trials at about 2,400 agent-seconds each (pilot v2 guided mean), plus verifier time.
- Not executed at the time of writing (executed since: see the update at the top). When approved, run on OrbStack (`orbstack-data-status` reports `external_available=True`) through the public targets:

```sh
make experiment-lock EXPERIMENT=experiments/ab-test/python__forge__guided-r3/experiment.yaml LOCK_CHECK=1
make experiment-execute EXPERIMENT=experiments/ab-test/python__forge__guided-r3/experiment.yaml CONCURRENCY=3 JOB_ID=r3-01
make experiment-assess JOB=jobs/python__forge__guided-r3/r3-01 STORE=stores/python__forge__guided-r3 \
  SCORERS="correctness@1 code-quality@1 resource-usage@1 process-compliance@2"
make experiment-analyze JOBS="jobs/python__forge__guided-r3/r3-01" STORE=stores/python__forge__guided-r3 \
  ANALYSIS_OUT=analysis/python__forge__guided-r3
```

Then rerun the `fault-catalog` analyse-job runbook on the job and reconcile FCR-003 to FCR-006 and FCR-008 against their criteria.
