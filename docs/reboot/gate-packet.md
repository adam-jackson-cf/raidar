# Phase 1 gate packet (pause-and-review)

- **Date:** 2026-09-30 (v1 record); updated 2026-10-02 with the N1 enforcement model (§5.2), the completed pilot v2 forge leg (§11.13), the current exit criteria (§11.14) the decisions now open (§11.15), the Jev calibration and promotion attempt (§11.17), the Phase 1 close-out (§12: Infisical secrets, Claude Haiku readiness, live Jev, labelling provenance, promotion, scoring alignment, cleanup) and schema v2 (§13: Harbor vocabulary, experiment kinds, runtime-compatibility gate, migrated pilot jobs); updated 2026-10-04 with §14.8-§14.15 (errata and spec-determinacy audit, verifier dependency parity, pilot v2 under errata, r3 results, ruff r2 results, Jev evaluation verdict, first-pass labels, decisions).
- **Plan reference:** `docs/reboot/implementation-plan.md` §3 (1.P pilot and the Phase 1 exit criteria) and §4 (this packet and the gate decisions).
- **Branch:** `reboot-impl`. The wave 1-4 work is committed up to `6b8e44d`. The pilot's infra fixes, this packet, **and the WS-L scenario-era cutover** (251 tracked files, about −47k lines, including scorer package files) are committed together on top of it (see §9). The WS-L cutover landed after the pilot ran and after the pilot store and locks were built; its consequences for the pilot evidence are in §1.1.
- **AgentSpec substitution (user directive):** plan 1.P names Claude Code as the pilot's single AgentSpec. By user directive (ADR-001 §2.7), every real-LLM trial in this pilot used **codex-cli 0.156.1 + `gpt-6-luna`, reasoning effort low**. No Claude Code trial has run, so the Claude mappings of the built-in components are unit-tested only.
- **Jev:** `OPENROUTER_API_KEY` is not set. Jev ran in shadow with replay and fixtures only; every live call is reported below as blocked. *Superseded 2026-10-02:* the key now comes from Infisical and live Jev ran over the whole pilot (§12.3).

> **Schema v2 reading note (2026-10-02, §13).** This packet is a historical record and keeps the words and paths in use when each section was written. Since §13, Raidar uses Harbor's vocabulary: a *run* is a **job**, a *trajectory* is a **trial**, a *checkpoint attempt* is a **step result**, *blocks* are **attempts**, the *oracle* is the **verifier**, a *harness* (`codex-cli`) is an **agent name** (`codex`), a *headline policy* is the headline **metric**. The `runs/` directory was renamed `jobs/` in place, so a path `runs/X` below now reads `jobs/X`, except that the migrated pilot v2 jobs moved to `jobs/pilot-v2/python__forge__guided-pilot/{forge-02,forge-03}` and `jobs/pilot-v2/python__circuit_eval-cp1__guided-pilot/oneshot-01`. `examples/pilot/experiment.yaml` is `experiments/ab-test/python__forge__guided-pilot/experiment.yaml` and `examples/pilot/oneshot.experiment.yaml` is `experiments/ab-test/python__circuit_eval-cp1__guided-pilot/experiment.yaml`. Make variables were renamed the same way (`RUN=` → `JOB=`, `RUNS=` → `JOBS=`, `RUN_ID=` → `JOB_ID=`, `RUNS_DIR=` → `JOBS_DIR=`, `BLOCK=` → `ATTEMPT=`, `HARNESS=` → `AGENT=`, `REVIEW_RUNS_ROOT=` → `REVIEW_JOBS_ROOT=`, `QUALITY_RUNS=` → `QUALITY_JOBS=`), and the old names now fail.

Evidence is under `runs/pilot/`, which is gitignored and local to this worktree. Paths below are relative to the worktree root. `<scratchpad>` (and bare `verify/…`, `ws-c/…` paths) means the orchestration session scratchpad, `/private/tmp/claude-501/-Users-adamjackson-Projects-complete-raidar/4bbb344c-08b8-464f-941e-9702963aa2f2/scratchpad`, which is also local and not committed.

| Artifact | Path |
|---|---|
| Forge run (8 checkpoints × {baseline, guided} × 5 blocks, continue) | `runs/pilot/pilot-forge/forge-04`: executed under version-1 lock `3a7bbb646214`, migrated to lock `80e1b5fdb1a2` (§1.1) |
| One-shot run (`circuit_eval.checkpoint_1` × {baseline, guided} × 3 blocks) | `runs/pilot/pilot-circuit-eval-oneshot/oneshot-04`: executed under version-1 lock `8404fb62f03f`, migrated to lock `f561e3653f35` (§1.1) |
| **Canonical Assessment store** (rescored on the current tree with `make experiment-assess`) | `runs/pilot/assessment-store-v2` |
| Original pilot store (pre-WS-L digests; kept unmodified as the pilot-time record, no longer resolved by the current tree) | `runs/pilot/assessment-store` |
| Analysis reports (pilot-time) | `runs/pilot/analysis/forge/analysis.{json,md}`, `runs/pilot/analysis/oneshot/analysis.{json,md}` |
| Analysis reports reproduced from the canonical store, rescore/replay counts, store digests, review-suite log and Quality projection after the provenance fix | `runs/pilot/gate-evidence/lock-fix/` |
| Rescore, replay and determinism evidence | `runs/pilot/gate-evidence/rescore/` |
| Perf check logs | `runs/pilot/gate-evidence/perf/` |
| Jev rubric shadow reports (per trial) | `runs/pilot/gate-evidence/jev-rubric/` |
| Review-surface suite log, Quality projection and screenshots | `runs/pilot/gate-evidence/review-surface/` |
| Component probe re-evaluation and per-trial pilot summaries | `runs/pilot/gate-evidence/pilot/` |
| Aborted pilot attempts (evidence for the three infra fixes) | `runs/pilot/_aborted/` |
| Real-trial probe run for the components the pilot did not use (WS-C) | `<scratchpad>/ws-c/probe-trials-v2/builtins-codex/probe-summary.json` |
| Oracle validation records | `problems/registry/scb/*/38d627ecf668.validation.json`, `docs/reboot/oracle-validation.md` |

---

## 1. Phase 1 exit criteria

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | All pilot trajectories complete with ≤1 infra exclusion per arm | **Met** | Both final runs: 16/16 trajectories valid, 0 infra errors in every arm (forge 5+5, one-shot 3+3). No replacement run. The three earlier failed attempts per leg are separate archived runs (`runs/pilot/_aborted/`), not part of the data. |
| 2 | Context-isolation and no-test-leak checks green on every trajectory | **Met** | 86/86 executed steps have a clean `raidar-reset.json` and a `pre-agent-leak.json` with `clean: true` (0 unclean). The `no_oracle_leak` validity check passes on 16/16 trajectories. |
| 3 | Every component's discovery probe passes | **Met (on codex-cli)** | Offline re-evaluation (`gate-evidence/pilot/probes.txt`): context-reset passes on 40+40 forge and 3+3 one-shot steps. rules-file, skills, lint-config, stop-hook and review-loop pass on all 40 forge and 3 one-shot guided steps (re-run on the post-WS-L code: same table). workspace-files, harness-settings, mcp-server and prompt-wrapper were not in the pilot; their only real-trial probe evidence is the WS-C run (`ws-c/probe-trials-v2/builtins-codex/probe-summary.json`, all passed, compiled digest `30f8bac2`, taken before the later component-fingerprint change). Caveats: C2's acceptance names Claude Code, which was substituted (see header) and never ran, so that part is unverified. The lint-config and workspace-files probes only check that the file is present; the stop-hook probe proves only that it fired (under Codex it maps to `notify`, and the agent never sees its output). On guided trials the `harness_version` validity check compares the 0.156.1 pin with the review-loop wrapper's report, not the inner Codex (rollout traces do show 0.156.1). |
| 4 | Offline rescoring reproduces deterministic observations byte-identically | **Met** | On the current tree, `make experiment-assess` rescored both runs with all four scorers into the canonical store `runs/pilot/assessment-store-v2` (40 + 24 `added`) and again into a fresh store B: the same 64 Assessment keys, and all 64 files are byte-identical (`lock-fix/store-v2.sha` = `lock-fix/store-b.sha`). `REPLAY=1 JEV_REPLAY_ONLY=1` over the canonical store then gave 64/64 `replayed_identical`. Against the original pilot store the 64 Assessments pair one to one and differ only in `.key` and `.scorer.implementation_digest` (the digest scheme changed, §1.1): every observation is unchanged. The Jev audit log is excluded from these comparisons: it carries timestamps and request ids by design. |
| 5 | G2 golden parity against the SCB fork passes | **Met** | `tests/test_code_quality_fork_parity.py`: 15/15 pass (adapters, V/E/H evidence, R/G live producers, lineage and regression kernels, problem arithmetic, sensitivity). Together with `test_code_quality_invariants.py`, 61/61 pass. |
| 6 | N1 budgets hold (`raidar perf check`) | **Not met** | forge-04: 10 of 12 phases pass. `artifact_collection` p95 was 3.97 s against a 3.5 s budget, and `teardown_result_write[init]` p95 was 4.62 s against 4 s. one-shot: every phase is `insufficient` (6 trajectories, fewer than the 10-sample minimum), but two point estimates are already over budget: `per_step_reset` 3.28 s against 2 s and `artifact_collection` 3.54 s against 3.5 s. The no-LLM `make perf-fixture` rerun also fails `artifact_collection` (p95 3.65 s against 3.5 s) and passes teardown (3.38 s). See §5. **Update (§5.1):** all three misses are fixed in code and every budget passes on the no-LLM fixture at concurrency 1 and 3; the criterion stays Not met until one real-agent leg is re-run on the fixed tree. **Update (§5.2, §11.14):** under the user's 2026-10-01 enforcement model the criterion is judged on `make perf-gate`, which passes on its latest run. |
| 7 | Frozen review-surface suite green; Quality element live | **Met** | `make review-surface-test REVIEW_RUNS_ROOT="runs/pilot/pilot-forge runs/pilot/pilot-circuit-eval-oneshot" REVIEW_STORE=runs/pilot/assessment-store-v2`: **55 passed**. At the cutover commit the one store now serves both roots, so the suite's Quality data holds 58 runs (42 synthetic + the 16 pilot runs); before that fix the target silently left the pilot runs out of the suite's Quality data when the root and store counts differed, and it now fails loudly on any other mismatch (`lock-fix/review-surface-test.log`; the suite grew from 40 while the review surface was being integrated in the same worktree, see §9), with 67 runs across 19 experiments in the review projection. The 16 pilot runs resolve their migrated locks and their Assessments; 14 are scored and the 2 baseline trajectories without code-quality values (`forge-04` `b00`, `b03`) are Unscored with per-component `ineligible` reasons, which the observations (unchanged since the pilot) determine. The Quality projection over the two pilot runs with the canonical store (`lock-fix/qp-store-v2.json`) gives 16 runs across 2 experiments, 662 ok cells and no "lock unavailable" note, the same counts as the pilot-time projection. The earlier screenshots (`quality*.png`) predate this rerun. |
| 8 | The analysis gives an honest verdict (`inconclusive` is acceptable) | **Met** | forge primary `solve_rate`: Δ 0, 95% CI [−0.22, 0.22], **inconclusive**. one-shot primary: **insufficient** (3 pairs, below the lock's minimum of 5). No override was used; the thresholds come from the locks (`pinned_by_lock=true`, min_pairs 5, alpha 0.05, max_exclusion_fraction 0.2). Reproduced on the current tree from the canonical store (`lock-fix/analysis-{forge,oneshot}`): the only field that differs from the pilot-time reports is `lock_sha256`, which is now the execution digest of the migrated lock (§1.1). |

> **Pilot v2 (2026-10-01):** this table is the v1 record. The criteria re-evaluated on pilot v2 data are in §11.10, and the end-to-end process verdict is in §11.11. **The current status, after the forge leg was completed, is §11.14.**

**Summary:** 7 of 8 criteria are met, and criterion 6 (N1 budgets) is not. Criterion 3 is met on the substituted AgentSpec only. Criteria 4, 7 and 8 reproduce on the current tree from the canonical store `runs/pilot/assessment-store-v2`, after the provenance fix in §1.1.

### 1.1 Post-pilot regression: WS-L changed every scorer digest (fixed)

- **What happened.** The WS-L cutover rewrote `orchestrator/src/raidar/scorers/__init__.py` (it deleted the legacy re-exports) and edited docstrings in `scorers/sdk/ownership.py` and `raidar/__init__.py`. Every scorer's implementation digest hashed those files byte for byte, so all four digests moved (correctness `8d74ef3b→f0609ba3`, code-quality `ab6c7d86→add2a82d`, resource-usage `44e7e5a0→cf0b8d0a`, process-compliance `64f76529→8593e305`). Two things broke. The pilot locks pinned the scorer digests inside `lock_sha256`, so they went stale and `make experiment-plan` refused them. The Assessment keys include the digest, so the current tree no longer found `runs/pilot/assessment-store`: the pilot runs rendered as Unscored, and one-shot analysis found 0 pairs.
- **Fix 1: execution identity is separate from assessment identity.** The lock (`spec/lock.py`, `lock_version` 2) now has four hashed sections, each with its digest under `sections`:
  - `execution`: the design (problems, runtime, agents, protocol, factors, conditions, arms, blocks, seed), problem and component files and references, arm digests, Harbor and harness versions, and the tools those references pin.
  - `assessment`: the scorers with their manifests, implementation digests and tool pins, the scorer config and the headline policy.
  - `analysis`: the baseline arm and verdict thresholds.
  - `toolchain`: Python, Raidar, LiteLLM and the spec-rules digest.

  `lock_sha256` is the digest of `execution` alone. It is what `run.json` records and what analysis, `review_quality` and `review_projection` match runs and baselines through. `document_sha256` covers the whole file. A scorer, policy, threshold or toolchain change therefore relocks to the same `lock_sha256`, and it means "rescore" or "re-analyse", not "stale run". `raidar lock --check` and `raidar plan`/`run` stay byte-strict, and their refusal now names each stale section and what it means.
- **Fix 2: digests ignore cosmetic edits.** Implementation digests and the spec-rules digest now hash each module's normalised syntax tree (`spec/source_digest.py`, scheme `python-ast/1`). Docstrings, comments and layout drop out; any code change still counts. The normal form is identical on Python 3.12.12 and 3.13.7 for all 233 `raidar` modules. The WS-L `scorers/__init__.py` rewrite deleted imports, which is a code change, so Fix 2 alone would still have moved the digests; Fix 1 is what keeps runs matched when that happens. Tests: `tests/test_source_digest.py` (cosmetic edits keep the digest, code edits change it) and `test_implementation_digest_covers_helpers_the_scorer_imports`.
- **Migration.** Both pilot locks were relocked with `make experiment-lock` (forge `80e1b5fdb1a2`, one-shot `f561e3653f35`). `make experiment-migrate-run-lock` (`raidar migrate-run-lock`, `execution/lock_migration.py`, `tests/test_run_lock_migration.py`) then moved `forge-04` and `oneshot-04`, with the executed version-1 locks taken from commit `2b66568`. The command:
  - rebuilds the execution section from the executed lock's own records and refuses the move unless it equals the new lock's, field for field;
  - keeps the executed lock byte for byte as `executed-lock.json`;
  - rewrites only `run.json`: the new `lock_sha256` plus a `lock_migration` record of both digests. `plan.json` and the trajectories keep the digest the trials ran under.

  Both runs verified. They were then rescored into the new canonical store `runs/pilot/assessment-store-v2` through `make experiment-assess`. The original store was not modified.
- **Gate.** `make quality` now runs `make experiment-locks-check`, which runs `raidar lock --check` on every `examples/*/*.lock.json`. Only the host Python patch version (`toolchain.python`) may differ, so that CI on Python 3.12 can pass; every other field of every section must match.

---

## 2. Pilot report

### 2.1 Execution

| Leg | Run | Arms × blocks | Valid / infra | Wall time | Concurrency |
|---|---|---|---|---|---|
| forge (8 checkpoints, continue) | forge-04 | 2 × 5 | 10 / 0 | 2 h 36 m 09 s | 3 |
| circuit_eval.checkpoint_1 (one-shot) | oneshot-04 | 2 × 3 | 6 / 0 | 12 m 09 s | 2 (concurrently with forge) |

Three Raidar infra bugs were found and fixed during the pilot. Each fix has a regression test, and each is evidenced by an archived aborted run:
1. **Compose project collision across concurrent runs.** Fixed by the `RunScopedDockerEnvironment` subclass.
2. **The review-loop's inner Codex sessions had no auth.** Fixed by resolving the wrapped native agent.
3. **The review-loop reviewer crashed on an oversized argument.** The diff included `.venv`, and the diff cap was above the per-argument limit. The fix changed review-loop@1's digest, so both locks were rebuilt before any kept data was collected.

### 2.2 Effects: forge (`runs/pilot/analysis/forge/analysis.md`)

Paired t intervals (fewer than 10 pairs). Thresholds pinned by the lock: min pairs 5, max excluded share 20%, alpha 0.05.

| Metric | View | n | Baseline | Guided | Δ [95% CI] | Verdict |
|---|---|---|---|---|---|---|
| solve_rate (primary) | continue | 5 | 0.10 | 0.10 | 0 [−0.22, 0.22] | inconclusive |
| reached | strict | 5 | 0.8 | 0.8 | 0 [−1.76, 1.76] | inconclusive |
| final_solved | continue | 5 | 0 | 0 | 0 [0, 0] | inconclusive (McNemar p = 1; 0/5 in both arms) |
| headline S (`scb-canonical@1`) | headline | 3 | 14.27 | 7.15 | −7.12 [−37.8, 23.5] | insufficient (2 baseline trajectories ineligible) |
| V verbosity | quality | 3 | 1.000 | 0.999 | −0.0007 [−0.004, 0.002] | insufficient |
| E erosion | quality | 3 | 0.199 | 0.244 | +0.045 [−0.40, 0.49] | insufficient |
| H architecture | quality | 3 | 1.000 | 1.000 | 0 | insufficient |
| R rework | quality | 3 | 0.263 | 0.270 | +0.007 [−0.12, 0.14] | insufficient |
| G regression | quality | 3 | 0.193 | 0.102 | −0.091 [−0.52, 0.34] | insufficient |

The quality metrics are `insufficient` because 2 of 5 baseline trajectories (b00, b03) are `ineligible` for code-quality (`production_source_loc_zero`). In those trajectories the model wrote no code at all (§6, F1).

**Per-checkpoint curves.** Solved share is 0.4 in both arms at checkpoints 1-2 and 0 from checkpoint 3 on, and the strict and continue views are identical. Case pass rate is higher under guided at every checkpoint (cp1 0.97 vs 0.59; cp8 0.82 vs 0.51). Every per-checkpoint Δ interval still spans 0, because the baseline is bimodal: the empty-workspace trajectories score near 0.

### 2.3 Effects: one-shot (`runs/pilot/analysis/oneshot/analysis.md`)

All verdicts are `insufficient`: there are 3 pairs, below the lock's minimum of 5.
- solve_rate: 0.67 in both arms, Δ 0.
- McNemar: 1 both, 1 baseline only, 1 guided only, 0 neither; p = 1.
- headline S (`scb-canonical-oneshot@1`): 60.6 vs 61.7.
- E: +0.115 [−0.19, 0.42].
- H: +0.071 [0.035, 0.106].
- R and G are not applicable to a one-shot problem. They appear as "ineligible 3/3".

### 2.4 Cost

cost_usd is Harbor's token-priced estimate. The runs used ChatGPT session auth, so it is nominal, not billed.

| Leg | Arm | Mean cost / trajectory | Mean tokens | Mean agent s | Cost Δ vs baseline [95% CI] |
|---|---|---|---|---|---|
| forge | baseline | $0.0285 | 1.14 M | 482 | |
| forge | guided | $0.1153 | 4.94 M | 1,670 | +$0.087 [0.051, 0.123] |
| one-shot | baseline | $0.0036 | 74 k | 93 | |
| one-shot | guided | $0.0151 | 583 k | 247 | +$0.011 [−0.002, 0.025] |

- Guided costs about 4× baseline, because the review-loop's reviewer sessions are billed to the arm.
- Total pilot spend, including the aborted attempts, was about $0.83 nominal. Jev spend was $0.00.

### 2.5 Sample-size projection (J2)

`n = ceil(7.84·(SD/δ)²)`: 80% power at two-sided α = 0.05. δ is the default smallest effect of interest, because the locks set no `min_effects`.

| Leg | Metric | Pilot SD | δ | Pairs needed | Additional | Projected USD |
|---|---|---|---|---|---|---|
| forge | solve_rate | 0.177 | 0.125 | 16 | 11 | $2.30 |
| forge | headline S | 12.3 | 5 | 48 | 45 | $7.25 |
| forge | rework R | 0.052 | 0.05 | 9 | 6 | $1.36 |
| forge | erosion E | 0.179 | 0.05 | 101 | 98 | $15.24 |
| forge | regression G | 0.172 | 0.05 | 93 | 90 | $14.04 |
| one-shot | solve_rate | 1.0 | 0.125 | 502 | 499 | $9.39 |
| one-shot | final_solved | 1.0 | 0.25 | 126 | 123 | $2.36 |

These SDs come from 3-5 pairs, and the forge SDs are inflated by the empty-workspace trajectories. Treat them as order-of-magnitude figures.

---

## 3. Oracle validation evidence

Both pilot problems are **validated**:
- `problems/registry/scb/forge/38d627ecf668.validation.json` (multi-checkpoint, 8 steps).
- `problems/registry/scb/circuit_eval.checkpoint_1/38d627ecf668.validation.json` (one-shot).

Each passes all six checks, with 3 repeats: `reference_k_passes_1..k`, `reference_k-1_fails_k`, `repeat_identical_positive`/`negative` and `native_parity_positive`/`negative`. The environment was Harbor 0.23.0 on OrbStack, grader image `raidar-oracle-grader:466e21c75a524af1`, pack `raidar/pack-python:2b91a52663e099f2`, and native runner commit `25069ba`.

The multi-checkpoint `circuit_eval` replication candidate stays excluded, because of native parity at checkpoints 6-8 (`docs/reboot/oracle-validation.md` §4).

In the validation records, `headline_policy.eligible` is false with `component_missing` for V/E/H/R/G. This is expected: validation computes only C. On the pilot, S was computed wherever code-quality was eligible.

---

## 4. Component probes and manipulation check

**Probes.** Every probe passes on every executed step (§1, criterion 3).

**Manipulation check (G5)** (`raidar analyze`, exact one-sided permutation test at the trajectory level):

| Leg | Check | Baseline share | Guided share | p | Verdict |
|---|---|---|---|---|---|
| forge | tests_run_before_finish | 0.00 (n=3) | 0.94 (n=5) | 0.018 | separated |
| forge | plan_written | 0.00 | 0.00 | 1 | not separated (tied) |
| one-shot | tests_run_before_finish | 0.00 (n=3) | 1.00 (n=3) | 0.05 | separated |
| one-shot | plan_written | 0.00 | 0.00 | 1 | not separated (tied) |

**Overall verdict: `not_separated` for both legs.** The overall verdict is `separated` only when every requested check is.

The treatment clearly changed testing behaviour. No arm ever wrote a plan before editing (0 of 70 deterministic `plan_before_edit` settlements complied). The guided rules file and skill ask the agent to "list what is new", but they never ask for a plan file or plan tool. The `plan_written` check is therefore measuring something the treatment does not instruct (§6, F4).

---

## 5. Overhead budget results (N1)

| Phase | Budget | forge-04 p95 (n) | one-shot p95 (n) | perf-fixture p95 (n) |
|---|---|---|---|---|
| environment_start[warm] | 20 s | 10.00 (10) pass | 14.83 (6) insufficient | 5.10 (10) pass |
| grader_image[warm] | 1 s | 0.35 (80) pass | 0.31 (6) insufficient | 0.33 (20) pass |
| component_apply | 2.5 s | 0.04 (10) pass | 0.03 (6) insufficient | 0.02 (10) pass |
| per_step_reset | 2 s | 1.81 (80) pass | 3.28 (6) insufficient | 1.50 (20) pass |
| artifact_collection | 3.5 s | **3.97 (80) fail** | 3.54 (6) insufficient | **3.65 (20) fail** |
| snapshot_transfer[artifact] | 0.5 s | 0.00 pass | 0.00 insufficient | 0.00 pass |
| verifier_plumbing | 1.5 s | 0.06 pass | 0.01 insufficient | 0.01 pass |
| step_archive_gap | 2.5 s | 1.73 (70) pass | n/a (one-shot) | 1.94 (10) pass |
| teardown_result_write[init] | 4 s | **4.62 (10) fail** | 3.83 (6) insufficient | 3.38 (10) pass |
| raidar_result_write | 0.5 s | 0.00 pass | 0.00 insufficient | 0.00 pass |
| per_step_overhead | 10 s | 7.36 pass | 6.30 insufficient | 6.46 pass |
| per_trial_fixed_overhead[init] | 27 s | 13.24 pass | 17.46 insufficient | 8.09 pass |
| scorer_orchestration | 6 s per scorer | not checked by `perf check` | | |

**Reading:**
- The pilot legs ran concurrently: up to 5 trials at once, with review-loop sessions on top.
- `teardown_result_write` passes uncontended (fixture 3.38 s), so its forge failure is plausibly contention.
- `artifact_collection` fails even in the concurrency-1 fixture (3.65 s). The Phase 0 real-agent notes had already seen 4.7-5.2 s on real steps. **This is a real budget miss, not just contention.** The Jev rubric shadow and the review-surface suite were running on the host during part of the fixture run, so the fixture figure is not fully quiet either.
- one-shot has too few trajectories for any N1 verdict.
- `scorer_orchestration`: not measured by `raidar run`. Measured by hand from the rescore log in the fresh store, per trial (all steps), p95:
  - code-quality@1: 5.85 s (about 0.7 s per step on forge).
  - process-compliance@1: 0.08 s.
  - resource-usage@1: 0.02 s.
  - correctness@1: under 0.01 s.
  - All are well inside 6 s per step, but this was measured by hand, not by `perf check`.

Also outside N1: Harbor reinstalls codex in every trial, which takes about 36 s of `agent_setup` (the pack's `HOME=/tmp/agent_home` breaks Harbor's version parse; WS-I). `agent_setup` has no budget, so `perf check` does not see it.


### 5.1 N1 misses: fixed on the fixture; real-leg re-run pending (2026-09-30)

**Status.**
- The three misses above are fixed in code. No budget value was changed.
- **Criterion 6 stays "Not met" until a real-agent leg is re-run on the fixed tree.** Every forge-04 and one-shot figure in the table above predates the fix.

**Root cause (one cause for all three).**
- Harbor 0.23 runs every main-container `exec`/`cp` as a new `docker compose` process. On OrbStack each costs about 0.3 s of compose start-up; `docker exec`/`docker cp` cost about 0.1 s. Under concurrency the gap widens, because each compose process is CPU-bound on the host.
- The budgeted gaps were built almost entirely from these calls:
  - `artifact_collection`: 7 calls (3 × log `chown -R`, `test -d`, `tar czf` to a temp file, `compose cp`, `rm`).
  - `per_step_reset`: 3 calls (`pwd`, workdir `cp`, `setup.sh`).
  - teardown: 3 `chown -R` calls before `compose down`.
- Two one-off costs also landed inside the first step's collection:
  - Harbor's `docker info` rootless probe (about 0.4 s);
  - with a real codex or claude-code agent, Harbor's lazy `import litellm` to price the step (1.6–2.1 s, once per `raidar run` process). This is why the real-agent samples were the worst ones.
- `/app` size was not the driver. `.venv` and the caches were already excluded, with one list shared by Harbor and the verifier.

**Fix.** In `raidar.execution.environment.RunScopedDockerEnvironment`:
- Main-service `exec`/`cp` go straight to the engine once the container is resolved. Flags, user, env, output and exit status are unchanged, and anything else falls back to compose.
- `/app` is collected with one streamed `tar cf -`, using Harbor's excludes and extraction filter.
- The log `chown` runs as one exec.
- The rootless probe and the `litellm` import run concurrently with `compose up`.

Tested in `tests/test_execution_environment.py`. Full measurements, including p50s: overhead-baseline.md §8, "N1 misses: profile and fixes".

**Re-measured** (`make perf-fixture`, OrbStack, no LLM, 10 trajectories each, all valid and solved; `make perf-check` on each run). Values are p95 in seconds:

| Phase | Budget | before c1 | after c1 | before c3 | after c3 |
|---|---|---|---|---|---|
| per_step_reset | 2 s | 1.44 | 0.74 | **2.26 fail** | 1.08 |
| artifact_collection | 3.5 s | **3.75 fail** | 0.47 | **6.17 fail** | 0.90 |
| teardown_result_write[init] | 4 s | 3.02 | 2.84 | **5.12 fail** | 2.86 |
| per_step_overhead | 10 s | 5.87 | 1.83 | 9.58 | 2.77 |
| `make perf-check` | | fail | **pass** | fail | **pass** |

- "before c3" reproduces all three pilot misses in a no-LLM fixture, which confirms they were concurrency-amplified framework overhead.
- One real `make orchestrator-smoke` trial (codex-cli, `gpt-6-luna` low, one-shot) was valid and solved. It measured `artifact_collection` 0.47 s, `per_step_reset` 0.70 s and teardown 1.49 s. That is n=1, so it is not a p95.

**No re-baseline is recommended.** Every budget now holds with margin on the fixture at concurrency 1 and 3. The smallest margin is teardown, about 1.1 s at p95, now dominated by `compose down`; `down -t 0` and `rm -f` first were measured and are no faster.

**Next action** (the Phase 2 entry condition proposed in §8; superseded by the user decision in §5.2):
- Re-run one real-agent leg on the fixed tree at pilot concurrency, then `make perf-check` it. The forge leg has 80 per-step samples; the one-shot leg needs at least 10 trajectories for any verdict.
- If `teardown_result_write[init]` still exceeds 4 s there, it is pure `compose down` under host load. The options would then be to cap trial concurrency for `perf check` runs, or for the budget owner to re-baseline teardown for concurrency at 5 s. This is not applied.

### 5.2 N1 enforcement model (user decision, 2026-10-01) and measurements

**The decision, as implemented.** No budget value and no budget structure changed (`execution/budgets.py` adds only `PerfInputError`, `TrialSamples` and record loading; `overhead-baseline.md` is unmodified).

| Check | What it runs | On a budget breach | Where it is wired |
|---|---|---|---|
| **Enforced:** `make perf-gate` | `make perf-fixture` (no LLM, 5 blocks × 2 arms, 2 checkpoints) at fixed concurrency 1, then 3, each judged with `perf check --strict` against the accepted budgets | fails (non-zero) | Release/gate check, run separately. `make quality` has no Docker step (Docker-backed tests are opt-in and skipped there), so the gate is documented in `AGENTS.md` ("Performance") and `README.md` instead of being added to `make quality`. |
| **Report-only:** `make perf-check RUN=...` | every phase of a real experiment run against its budget | warning, exit 0; `PERF_STRICT=1` (`--strict`) makes it fail | Run after any real experiment. Malformed or empty input (a corrupt trajectory, `run.json` or trial `result.json`, or no trajectories) still exits non-zero and names the file. |

`perf-check` also records each run's contention context: configured trial concurrency (`run.json`), peak concurrent trials in the checked runs, the peak including other Raidar runs under the same runs root that overlapped in time (listed by path), and trials without timestamps. Each breached p95 budget is re-judged separately for trials that ran at ≤3 and at ≥4 concurrent trials (JSON: `contention`, `warnings[].by_concurrency`). Docker work outside Raidar runs is not recorded and is reported as not detected.

**Budget structure rule (decision 3).** A concurrency-scaled `teardown_result_write[init]` budget (strict 4 s at ≤3 concurrent trials, a separate value at ≥4) is introduced only if, after the forge completion run on a healthy drive at pilot concurrency 3, its p95 still exceeds 4 s at ≥4 concurrent trials. **It did not apply:** forge-02 and forge-03 never exceeded 3 concurrent Raidar trials, even counting overlapping runs, so there are no ≥4 samples (§11.13). The misses at ≤3 concurrent are reported as a regression to investigate, not re-baselined.

**Enforced gate runs** (logs in `runs/pilot-v2/gate-evidence/perf/perf-gate-*.log`), p95 seconds:

| Run (UTC) | Concurrency | env_start[warm] (20) | per_step_reset (2) | artifact_collection (3.5) | teardown[init] (4) | per_trial_fixed[init] (27) | Result |
|---|---|---|---|---|---|---|---|
| 1, 2026-10-01 18:09 | 1 | | | | | | did not run: the fixture problem was `validated_partial` (one validation repeat); the fixture now validates with two repeats |
| 2, 2026-10-01 ~18:14-18:20 | 1 | 5.68 | 0.97 | 0.94 | 2.64 | 7.94 | pass |
| 2 | 3 | 13.76 | 1.28 | 1.03 | **4.85** | 18.38 | **fail** (`make: *** [perf-gate] Error 2`) |
| 3, 2026-10-02 01:30-01:38 | 1 | 6.15 | 0.76 | 0.91 | 2.46 | 8.13 | pass |
| 3 | 3 | 6.63 | 1.26 | 1.57 | 2.14 | 8.31 | **pass** (exit 0) |

Every other budgeted phase passes in all four fixture runs. Run 2 finished before forge-03 started; external Docker load at that time was not sampled. Run 3 ran with 1-2 containers of another project (`slop-code`) on the same OrbStack VM (sampled every 30 s, `perf-gate-3-external-containers.log`). **Reading:** the enforced gate passes on its latest run, but its c3 teardown margin is not stable: one of two complete gate runs breached it at 4.85 s. The gate is sensitive to host load, which is what the regression investigation in §11.15 has to settle.

---

## 6. Jev shadow audit

**Current state (2026-10-02; supersedes the blocked-call statements in this section).** `OPENROUTER_API_KEY` is injected from Infisical (§12.1), and live Jev has run in shadow over every pilot v2 trial: 15,516 provider calls for $2.33 in total (§12.3). The `process-compliance@1` rescore added 44 Assessments, and **0 of them are provisional**. The 5+8 provisional Assessments described below and in §11.2/§11.13 stay in the store (it is add-only) under the earlier scorer identity, so they are history, not open work. Nothing is enforced: both policies are still `shadow` (§11.17, §12.5). The text below is the 2026-09-30 record, kept as it was.

Everything below is shadow: `counts_toward_verdicts: false` on every decision. Pinned model: `typesafe/jev-1.13-20260917`.

**Process-compliance residue** (`process-compliance.residue`, question set `process-residue@1`; 140 decisions across 16 trajectories in one rescore):
- 135 were settled deterministically: 36 `complied`, 99 `not_complied`.
- 5 were ambiguous residues, all from guided forge trajectories, on the check `tests_run_before_finish` with the ambiguity "an unparsed write follows the last test run". All 5 went to Jev and ended as `jev_error / missing_key`.
- **Live calls are blocked on `OPENROUTER_API_KEY`.** The 4 affected Assessments are stored as `provisional` and will be re-asked on the next rescore once a key exists.
- The `--jev-replay-only` pass answered 0 of 5 from cache (the cache is empty) and left the Assessments byte-identical.
- Coverage: 5 of 140 decisions (3.6%) needed Jev, and 0 of 5 are answered.

**SCB rubric smells** (`code-quality.rubric-smells@1`, question set `scb-rubric@1`; `make jev-shadow-rubric … REPLAY_ONLY=1` over all 16 pilot trajectories, reports in `gate-evidence/jev-rubric/`):

| Measure | Value |
|---|---|
| Decisions audited | 12,663 (0 provider calls, 0 cache hits, $0.00) |
| Input sufficiency | 8,418 / 12,663 = **0.665** sufficient, 0 partial, 4,245 missing |
| Question coverage (answered) | 0 / 76,320 = **0.0**. Every sufficient request ended `cache_miss`, because live calls are blocked |
| Symbol coverage | 0 / 617 |
| Measured criteria per checkpoint | 0 / 45 |

- **Disagreements:** none can be computed. There are no Jev answers, and so no deterministic-vs-Jev or label-vs-Jev pairs.
- **Promotion readiness:** no policy can be promoted, because promotion needs at least 30 labels with at least 5 minority labels, and there are 0 labels and 0 answers.
- The b01-p0-guided forge rubric request set was run twice while I was assembling this packet, so the store's rubric audit counts it twice. Shadow audit rows do not affect Assessments.

---

## 7. Friction points in the spec and components

The friction points are grouped by what each affects.

**Problem × model**
- **F1. Empty-workspace refusals.** In forge baseline trials b00 and b03, gpt-6-luna at low effort saw an empty `/app`, asked for the repository, and wrote nothing at any of the 8 checkpoints.
  - The SCB instruction is spec-only, and Raidar imports it faithfully.
  - It confounds the forge effect: the guided rules supply the missing "build it" framing.
  - It also made 2 of 5 baseline trajectories ineligible for code-quality, which is why every quality metric is `insufficient`.
- **F2. Checkpoint 3 is a wall.** Neither arm solved checkpoint 3 or later on forge (0 of 10). With this model and effort, forge measures only checkpoints 1-2 for solve outcomes, although case pass rate still varies.

**Treatment fidelity**
- **F3. The review-loop verdict parser does not fit this model.** The ported factory-x-loops parser expects feedback after `VERDICT: CHANGES`, but gpt-6-luna writes it before that line. On forge (40 guided checkpoints) the outcomes were 18 EMPTY_FEEDBACK, 18 APPROVE and 4 rounds_exhausted; on one-shot, 2 EMPTY_FEEDBACK and 1 NO_VERDICT. EMPTY_FEEDBACK ends the loop before the fix round, so the treatment was often "implement plus review" only. The parser was not changed, because that would alter locked review-loop@1 semantics.
- **F4. The plan check is not instructed.** No component asks for a plan file, so `plan_written` is 0 in both arms and forces the overall manipulation verdict to `not_separated`. Either add a plan instruction to the treatment, or drop `plan_written` from the requested checks.
- **F5. The stop-hook is invisible under Codex.** It maps to `notify`, which fires but whose output the agent never sees. Its probe proves only that it fired.

**Spec, lock and analysis**
- **F6. The one-shot leg cannot pass its own verdict threshold.** Its lock pins `min_pairs: 5`, but the experiment has 3 blocks, so every verdict is `insufficient` by construction. The spec validator could warn when `blocks < min_pairs`.
- **F7. One-shot R/G are counted as "ineligible".** On a one-shot problem, R and G are `not_applicable`, but analysis counts them as `ineligible` exclusions (3/3). The Quality table shows their Pairs as "0 / −3". This is cosmetic, but it reads as a data loss.
- **F8. Several scorer configurations have no route through the experiment spec.** Scorer config, `ProtocolSpec.progression`, the code-quality `entry_file` and `InventoryPolicy` can only be set through `assessment-config.yaml` or `raidar assess --config`. That is why H (architecture) needs `entry_file` wiring before it measures anything beyond the default.
- **F9. (Resolved by WS-L.)** At pilot time there was no public make target for `raidar assess --rescore`; WS-L added `make experiment-assess`.

**Jev and review surface**
- **F10. Process-residue Jev decisions carry bare trial ids.** The rescore records process-residue decisions under ids like `b01-p0-guided`, which are shared by the two runs. The Quality run view therefore refuses to attach them ("18 Jev decision(s) … which 2 runs share; not attached"). The rubric shadow accepts `--trial <experiment>.<run>.<trial>`, but the rescore path has no such option.
- **F11. Quality discovery does not follow symlinks.** `review_quality` uses `rglob` without following symlinks, so a curated runs root made of symlinks projects 0 runs. The frozen `review_projection` does follow them. I worked around this with APFS clones.
- **F12. Aborted runs sit inside the runs root.** `runs/pilot/_aborted/<run>/run.json` matches the `<experiment>/<run>/run.json` layout, so pointing the review tools at `runs/pilot` would ingest aborted runs as an experiment named `_aborted`.

**Infra**
- **F13. (Partly resolved by WS-L.)** An interrupted `make experiment-execute` left trial containers running and they were removed by hand. WS-L rewrote `raidar harbor cleanup` (`make harbor-cleanup [RUN=]`); the driver still does not clean up automatically on interrupt.
- **F14. codex is reinstalled in every trial** (§5), costing about 36 s per trial with network access.
- **F15. `artifact_collection` missed its budget.** It exceeded 3.5 s even at concurrency 1 (§5). Fixed in code (§5.1: p95 0.47 s at c1, 0.90 s at c3 on the fixture); a real-agent re-run is pending.

---

## 8. Decisions for the user at the gate

Each option below is followed by the evidence that bears on it. The recommendations are proposals, not decisions.

1. **Proceed to Phase 2 (Node/TS).**
   - For: the platform works end to end. There were 0 infra exclusions, isolation and leak checks are clean, rescoring is deterministic, fork parity holds, the frozen UI is green, and the Quality element renders real data.
   - Against: N1 is not met (`artifact_collection` everywhere; teardown under concurrency).
   - *Proposal:* proceed only once the `artifact_collection` budget is either fixed or re-baselined by the budget owner. Treat this as a blocking entry condition for Phase 2.
   - *Update (§5.1):* the misses are fixed in code and pass on the no-LLM fixture with no re-baseline; the remaining entry condition is one real-agent leg re-run at pilot concurrency plus `make perf-check`.
2. **Revise the treatments.**
   - The manipulation check shows the treatment changes testing behaviour (p = 0.018 on forge), but the review loop mostly short-circuits on this model (F3), the plan check is not instructed (F4), and the stop-hook is invisible under Codex (F5).
   - *Proposal:* yes, before any confirmatory run. Make review-loop verdict parsing position-tolerant as review-loop@2 (a new version, not an edit to @1), then decide on F4 and F5.
3. **Add problems or repeats first.**
   - forge solve outcomes saturate at checkpoint 2 (F2), and 2 of 5 baseline trajectories were empty refusals (F1).
   - J2 projects 16 pairs for solve_rate and 48 for S at the default δ; the one-shot projections run to hundreds of pairs.
   - *Proposal:* add a second validated multi-checkpoint problem and decide how to handle F1, either by framing the instruction or by recording it as a model property, before sizing a confirmatory run. The one-shot leg needs `blocks ≥ min_pairs` (F6).
4. **Adjust abstractions.**
   - These are candidates from the friction list:
     - wire scorer config, progression and entry_file through AssessmentSpec (F8);
     - add a run-qualified trial id to Jev decisions on the rescore path (F10);
     - treat `not_applicable` separately from `ineligible` in analysis (F7);
     - automatic container clean-up on interrupt (F13; `make harbor-cleanup` now exists, and F9 is resolved by `make experiment-assess`);
     - a gate that stored pilot Assessments still resolve on the current tree. Stale example locks are now gated (§1.1), but a code change to a scorer module still needs a manual rescore into the canonical store.
   - None requires a change to the scorer contract, the oracle plugin or the frozen UI.
5. **Promote any Jev policy to enforce.**
   - **Not possible.** No provider key means 0 Jev answers and 0 labels, against a requirement of at least 30 labels with at least 5 minority labels and a frozen threshold.
   - Both policies stay in shadow.
   - *Done 2026-10-02 (§12.3, §11.17).* Next step was: set `OPENROUTER_API_KEY`, rescore (the provisional Assessments re-ask automatically), then label decisions in the review surface and export them with `make jev-export-labels`.

---

## 9. State of the worktree

- **Committed together on `reboot-impl`** (one commit on top of `6b8e44d`):
  - the pilot's three infra fixes and their tests;
  - the analysis and review changes from the prep task;
  - the guided component files (`examples/pilot/components/guided/ruff.toml`, `skills/`);
  - the pilot locks as the pilot ran them (version 1). The provenance fix (§1.1), committed with the clean cutover, replaces them with version-2 relocks;
  - the WS-L scenario-era cutover (legacy scorers, runtime and scenario surfaces removed; `make experiment-assess` and a rewritten `raidar harbor cleanup` added);
  - this packet.
- Not committed (gitignored or local): `runs/`, Assessment stores, `experiments/`, spike work directories, `orchestrator/.coverage`.
- **Committed with the clean cutover (provenance fix, §1.1):** sectioned lock, AST source digests, `migrate-run-lock`, `experiment-locks-check` in `make quality`, relocked `examples/pilot/*.lock.json`, and their tests. The migrated `run.json` files, `executed-lock.json` copies and `runs/pilot/assessment-store-v2` are local (gitignored).
- `review_quality` and analysis find a run's lock through its execution digest, so the relocked pilot locks serve both as the runs' provenance and as the current locks.
- **Clean cutover (committed after this packet):** `AGENTS.md` is rewritten for the experiment, problem, component and scorer model, including the lock-provenance rules (relock `examples/**/*.lock.json` after a spec or scorer change; a non-execution relock keeps `lock_sha256`, so rescore rather than rerun). The review surface integrates the Quality page into navigation and run detail and uses problem/condition wording; the Playwright specs assert the same F12 signals under the new labels. The retired `scenarios/`, `matrices/`, `environments/` and `spikes/` trees are unreferenced; their `git rm` is a pending maintainer step.
- `review-surface/data/` (gitignored) holds the projection from the most recent `make review-surface-test` run; rerun the criterion 7 command to load the two pilot runs scored from `runs/pilot/assessment-store-v2`.

---

## 10. Pilot v2 treatment revisions

Status: implemented and locked, not yet run. Nothing in this section is a measured result.

The v1 pilot did not measure the treatment it meant to measure. The guided arm also carried missing task framing (F1), and its review loop usually stopped before the fix round (F3). v2 changes the treatments, and only the treatments, as follows.

| Change | v1 | v2 | Why |
|---|---|---|---|
| Starter framing, both arms | none | `prompt-wrapper@1` with `examples/pilot/components/shared/starter-framing.md`, declared once under the new `shared_components` | F1: the baseline saw an empty `/app`, asked for the repository and wrote nothing. The guided rules supplied that framing, which confounded the effect. |
| Review loop, guided arm | `review-loop@1` | `review-loop@2` (`max_review_rounds: 2`) | F3: 18 of 40 forge reviews (and 2 of 4 one-shot) parsed as `EMPTY_FEEDBACK`, because gpt-6-luna writes its feedback before `VERDICT: CHANGES`. |
| forge blocks | 5 | 16 | §2.5: the J2 projection for solve_rate. |
| one-shot blocks | 3 | 5 | F6: with 3 blocks the leg could not reach `min_pairs: 5`. |

**The framing text.** The template is the following three lines, then `{{ instruction }}`. It gives no hints about the problem:

> You are working non-interactively in /app. The directory may start empty.
> Build the program described by the specification below in /app.
> Do not ask questions: nobody will answer them during this task.

**Shared components (spec schema).**
- `shared_components` is a new top-level list in `experiment.yaml`. Its uses are put ahead of every condition's own components (`ExperimentSpec.condition()` returns this effective condition), so the arms get them byte-identically.
- The factor guard compares effective conditions. Shared uses are identical in every arm, so v2's arms still differ only in the guided components. Validation also refuses a condition that repeats a shared component.
- The lock hashes shared uses into every condition's manifest and into the arm digests.
- A spec that declares no shared components serialises and locks exactly as before.

**review-loop@2.**
- `review-loop@1` and its fingerprint files (`review_loop.py`, `builtins/workflow.py`) are unchanged, and its digest is still `f33ffb58…`.
- `@2` (`review_loop_v2.py`, `builtins/workflow_v2.py`) keeps @1's stages, prompts, diff capture, log filing, billing and probe. It changes only how a review is read:
  - A `VERDICT: APPROVE|CHANGES` marker may carry markdown emphasis or backticks, and the last marker wins.
  - For `CHANGES`, the feedback is the text before and after the marker. Any non-empty feedback is actionable and starts the fix round.
  - An unusable review (no marker, or `CHANGES` and nothing else) is re-asked once. The re-ask is a fresh reviewer session that gets the task, the diff and the unusable reply.
  - **Documented fallback:** if the re-asked review is also unusable, the loop stops with the work as it stands (`status: completed`, `termination_reason: review_unusable_after_reask`). It does not invent feedback for a fix round.
  - Every parse is recorded in `review-loop/review-loop.json` under `parses`: round, attempt, stage, verdict, feedback position (`before`/`after`/`both`/`none`), feedback length and marker count.
- **Interaction with the framing.** Harbor would apply the arm's prompt template to every inner session, which would tell the reviewer to build the program. @2 therefore applies the template itself, to the implementer's stages only. The reviewer judges against the bare step instruction. The prompt-wrapper probe still finds the framing lines in the implementer's rollout.

**Evidence (offline).**
- `orchestrator/tests/fixtures/review_loop/pilot-reviewer-replies.json` holds all 56 reviewer replies from forge-04 and oneshot-04.
- Under @2, all 20 `EMPTY_FEEDBACK` replies (18 forge, 2 one-shot) parse as actionable `CHANGES` with their feedback before the marker. Every reply @1 read as `APPROVE` or `CHANGES` reads the same way. The one one-shot `NO_VERDICT` reply stays unusable, so @2 re-asks it.
- Tests cover the re-ask, the fallback, framing scoped to the implementer, shared-component compile/lock/guard behaviour, and both pilot arms compiling with the same framing.
- `make quality` passes.

**Locks.** `examples/pilot/experiment.lock.json` and `examples/pilot/oneshot.experiment.lock.json` are relocked. `make experiment-validate` and `make experiment-plan` pass: 32 trials (forge, 16 blocks × 2 arms × 8 steps) and 10 trials (one-shot).

**Knock-on effects.**
- v1 and v2 are different experiments: the baseline arm changed too. Do not pool forge-04 or oneshot-04 with v2 runs.
- The relocked v2 locks no longer serve as the provenance of the v1 runs (§9). To re-analyse a v1 run, pass its own `executed-lock.json` as `LOCK=`.
- Not addressed here: F4 (no plan instruction, `plan_written`) and F5 (stop-hook invisible under Codex).
- Reviewer sessions remain billed to the guided arm. The re-ask adds at most one reviewer session per round.

---

## 11. Pilot v2 results (2026-10-01)

Status: measured. This section supersedes §1-§5 as the current state of the exit criteria; §1-§5 are kept unchanged as the v1 record for comparison. v1 and v2 are different experiments (§10), so their numbers are compared, never pooled.

Every real-LLM trial used codex-cli 0.156.1 + `gpt-6-luna`, reasoning effort low, on OrbStack. Jev ran in shadow, replay-only (`OPENROUTER_API_KEY` unset; out of scope until the local process is confirmed). Evidence is under `runs/pilot-v2/` (gitignored, local to this worktree).

| Artifact | Path |
|---|---|
| Forge run (8 checkpoints × {baseline, guided} × 16 blocks planned, continue) | `runs/pilot-v2/pilot-forge/forge-02` (lock `5a73637e8ac3`) |
| One-shot run (`circuit_eval.checkpoint_1` × {baseline, guided} × 5 blocks) | `runs/pilot-v2/pilot-circuit-eval-oneshot/oneshot-01` (lock `827ebb5a7ca9`) |
| Aborted forge attempt (grader uv dev-group bug, fixed in `c068e4c`) | `runs/pilot-v2/_aborted/forge-01-grader-uv-dev-group` |
| Canonical Assessment store | `runs/pilot-v2/assessment-store` |
| Assess, rescore (store B), replay and digest evidence | `runs/pilot-v2/gate-evidence/assess/` |
| Perf check logs | `runs/pilot-v2/gate-evidence/perf/` |
| Analysis reports | `runs/pilot-v2/gate-evidence/analysis/{forge,oneshot}/analysis.{json,md}` |
| Review-surface suite log, pilot-v2-only Quality projection, screenshots | `runs/pilot-v2/gate-evidence/review-surface/` |

### 11.1 Execution

| Leg | Run | Arms × blocks | Valid / infra | Wall time (UTC) | Concurrency |
|---|---|---|---|---|---|
| forge | forge-02 | 2 × 16 | 14 / 18 (b00-b06 valid in both arms; b07-b15 infra in both arms) | 11:27-16:56 (5 h 30 m), stopped when the drive holding OrbStack's data was switched off | 3 |
| one-shot | oneshot-01 | 2 × 5 | 10 / 0 | 10:24-10:49 (24 m) | 2 (concurrently with forge-01) |

- **One Raidar bug found and fixed (`c068e4c`).** The offline grader (`--network none`, `UV_OFFLINE=1`) ran the SCB entrypoint `uv run --project {app} forge.py`, and uv tried to sync the submission's `dev` dependency group before starting the program, so correct work scored 0 (forge-01 guided b00: 0.0 on all 8 checkpoints). The sandbox now sets `UV_NO_SYNC=1`. A regression test runs real uv offline with and without a lockfile. `make assess-regrade` on the aborted trial then gave 1.0, 1.0, 0.94, 0.96, 0.96, 0.91, 0.90, 0.89, and regrading all 10 one-shot trials matched their recorded grades, so oneshot-01 stands. Only the scorer implementation digests moved; `lock_sha256` is unchanged for both legs.
- **Drive switched off, not a Raidar bug or a hardware fault.** At 16:56:24 UTC (17:56 BST) the user switched off the external drive holding OrbStack's data (`/Volumes/UGreen-External/Docker`). The I/O errors in `~/.orbstack/log/vmgr.log` at that moment are the switch-off: the VM's btrfs volume went read-only and OrbStack stopped. Every forge trial in flight or queued (b07-b15, both arms) was recorded as infra-invalid. The drive is healthy (macOS reports it mounted read-write, SMART Verified). OrbStack was restarted at 18:18 BST: Docker, signoz and a test container came up with no new btrfs or I/O errors in `vmgr.log`, and `make harbor-cleanup` removed the 3 trial environments forge-02 left behind (§11.12).
- **v1 → v2, F1 resolved.** 0 trajectories in either arm or leg wrote nothing (v1: 2 of 5 forge baseline trajectories). 0 executed steps ended with an empty `/app`.
- **v1 → v2, F3 resolved.** forge guided, 56 steps: review-loop@2 parsed 0 `EMPTY_FEEDBACK` (v1: 18 of 40). Terminations: `review_approved` 23, `rounds_exhausted` 32, `loop_exception` 1 (a real 1800 s agent timeout on b03 guided checkpoint_7, graded 0.88 and counted against the arm). All 3 unusable reviews were recovered by the re-ask; none ended `review_unusable_after_reask`.

### 11.2 Assessment and rescoring

- `make experiment-assess` with all four scorers (`correctness@1 code-quality@1 resource-usage@1 process-compliance@1`) and `JEV_REPLAY_ONLY=1` into `runs/pilot-v2/assessment-store`: 40 `added` (one-shot, 10 trials) + 68 `added` (forge, the 14 valid trials and the 3 infra trials that had executed steps).
- The same command into a fresh store B (`gate-evidence/assess/store-b`): the same 108 Assessment files, **byte-identical** (`store-a.sha` = `store-b.sha`, 108 lines each).
- `REPLAY=1 JEV_REPLAY_ONLY=1` over the canonical store: 40/40 and 68/68 `replayed_identical`; the store's digests are unchanged afterwards.
- Jev shadow (`process-compliance.residue`, replay-only): 548 decisions, none enforced (532 deterministic, 16 `jev_error` "No cached decision to replay"). Those 16 leave 8 process-compliance Assessments `provisional: true` (7 forge, 1 one-shot). They resolve only when live Jev is enabled, which is out of scope for now.

### 11.3 Effects: forge (`gate-evidence/analysis/forge/analysis.md`)

Thresholds pinned by the lock: min pairs 5, max excluded share 20%, alpha 0.05; paired t intervals (fewer than 10 pairs). **Every verdict is `insufficient`: 9 of 16 planned pairs are infra-excluded (56% > 20%).** The point estimates below are from the 7 valid pairs and are not findings.

| Metric | View | n | Baseline | Guided | Δ [95% CI] | Verdict |
|---|---|---|---|---|---|---|
| solve_rate (primary) | continue | 7 | 0.179 | 0.036 | −0.143 [−0.266, −0.019] | insufficient |
| reached | strict | 7 | 1.43 | 0.29 | −1.14 [−2.13, −0.15] | insufficient |
| final_solved | continue | 7 | 0 | 0 | 0 (McNemar: 0/0/0/7, p = 1) | insufficient |
| headline S (`scb-canonical@1`) | headline | 6 | 14.16 | 3.55 | −10.6 [−22.7, 1.5] | insufficient (1 ineligible) |
| V verbosity | quality | 7 | 0.999 | 0.998 | −0.001 [−0.004, 0.002] | insufficient |
| E erosion | quality | 7 | 0.102 | 0.214 | +0.113 [0.001, 0.225] | insufficient |
| H architecture | quality | 7 | 1.000 | 0.999 | −0.001 [−0.002, 0.000] | insufficient |
| R rework | quality | 7 | 0.237 | 0.260 | +0.023 [−0.037, 0.082] | insufficient |
| G regression | quality | 6 | 0.192 | 0.052 | −0.140 [−0.302, 0.021] | insufficient (1 ineligible) |

- The one ineligible pair (headline S, G) has no G value for the `checkpoint_6 → checkpoint_7` transition; the scorer records the reason, it is not scored 0.
- **Curves.** Solved share at checkpoints 1-2 is 0.71 baseline vs 0.14 guided, and 0 in both arms from checkpoint 3 on. Strict and continue views agree. Case pass rate (strict_pass_rate) is 0.99 vs 0.95 at cp1 and converges from cp3 (cp7 0.82 vs 0.89, cp8 0.80 vs 0.75). Guided's failures are near-misses, not empty work.
- **Why guided misses checkpoint 1 (observed).** In 6 of 7 guided trajectories the only failing cp1 cases are `test_create_rejects_empty_name_string` and `test_create_rejects_empty_requirement_sets`; baseline fails the same two cases in 2 of 7. The same agent behaviour repeats across blocks, so this reads as a treatment effect on input validation, not a grader fault. It is an inference from 7 pairs, not a finding.
- **v1 comparison.** v1 forge solve_rate was 0.10 in both arms (Δ 0, inconclusive), with a bimodal baseline (empty workspaces). The v2 baseline improved because the shared framing removed the empty-workspace failure.

### 11.4 Effects: one-shot (`gate-evidence/analysis/oneshot/analysis.md`)

5 pairs, 0 excluded (meets min pairs 5, unlike v1's 3).

| Metric | View | n | Baseline | Guided | Δ [95% CI] | Verdict |
|---|---|---|---|---|---|---|
| solve_rate (primary) | continue | 5 | 0.6 | 0.8 | +0.2 [−0.84, 1.24] | **inconclusive** |
| headline S (`scb-canonical-oneshot@1`) | headline | 5 | 54.8 | 74.5 | +19.7 [−75.1, 114.5] | inconclusive |
| V verbosity | quality | 5 | 0.992 | 1.000 | +0.008 [−0.009, 0.025] | inconclusive |
| E erosion | quality | 5 | 0.093 | 0.296 | +0.203 [−0.20, 0.61] | inconclusive |
| H architecture | quality | 5 | 0.958 | 0.944 | −0.014 [−0.072, 0.043] | inconclusive |

McNemar on final_solved: 2 both, 1 baseline only, 2 guided only, 0 neither; p = 1. R and G do not apply to a one-shot problem.

### 11.5 Manipulation check

Both legs: **`not_separated`** overall.
- `tests_run_before_finish` separates: forge 0 vs 0.90 (p = 0.0003), one-shot 0 vs 1 (p = 0.008).
- `plan_written` does not separate (0 vs 0 in both legs). This is F4, which v2 did not address: no component instructs a plan file. The overall verdict therefore cannot become `separated` until F4 is resolved or the check is dropped from the lock.

### 11.6 Cost

cost_usd is Harbor's token-priced estimate; the runs used ChatGPT session auth, so it is nominal, not billed. Means are over valid trajectories.

| Leg | Arm | Mean cost / trajectory | Mean tokens | Mean agent s | Cost Δ vs baseline [95% CI] |
|---|---|---|---|---|---|
| forge | baseline | $0.0346 | 1.24 M | 596 | |
| forge | guided | $0.1695 | 7.56 M | 2,724 | +$0.135 [0.113, 0.157] |
| one-shot | baseline | $0.0042 | 99 k | 100 | |
| one-shot | guided | $0.0202 | 764 k | 321 | +$0.016 [0.012, 0.020] |

Recorded v2 spend: one-shot $0.122, forge-02 $1.569 (including steps run before the crash), aborted forge-01 $0.178. Jev spend $0.00. Guided costs about 5× baseline (v1: about 4×); review-loop@2 reviewer and re-ask sessions are billed to the arm.

### 11.7 Sample-size projection (J2)

| Leg | Metric | Pilot n | Pilot SD | δ (default) | Pairs needed | Additional | Projected USD |
|---|---|---|---|---|---|---|---|
| forge | solve_rate | 7 | 0.134 | 0.125 | 9 | 2 | $1.84 |
| forge | headline S | 6 | 11.6 | 5 | 42 | 36 | $8.59 |
| forge | rework R | 7 | 0.065 | 0.05 | 14 | 7 | $2.86 |
| forge | erosion E | 7 | 0.121 | 0.05 | 46 | 39 | $9.39 |
| forge | regression G | 6 | 0.154 | 0.05 | 75 | 69 | $15.33 |
| one-shot | solve_rate | 5 | 0.837 | 0.125 | 352 | 347 | $8.58 |
| one-shot | final_solved | 5 | 0.837 | 0.25 | 88 | 83 | $2.14 |

The forge figures come from 7 pairs of an incomplete leg; treat them as order-of-magnitude. forge final_solved has no projection (SD 0: no trajectory solved checkpoint 8 in either arm).

### 11.8 N1 overhead (`make perf-check`, real agent, post-fix tree)

oneshot-01 (concurrency 2) ran alongside the aborted forge-01 (concurrency 3), up to 5 trials at once plus review-loop sessions. forge-02 started after oneshot-01 had finished (11:27 vs 10:49 UTC) and ran alone at concurrency 3. Values are p95 in seconds; only phases that fail or changed materially from v1 are shown. All other budgeted phases pass in both legs.

| Phase | Budget | v1 forge-04 | v2 forge-02 (n) | v2 oneshot-01 (n) |
|---|---|---|---|---|
| artifact_collection | 3.5 s | **3.97 fail** | 1.28 (131) pass | 0.86 (10) pass |
| per_step_reset | 2 s | 1.81 pass | 1.12 (131) pass | 1.66 (10) pass |
| per_step_overhead | 10 s | 7.36 pass | 3.10 (131) pass | 2.32 (10) pass |
| environment_start[warm] | 20 s | 10.00 pass | **20.77 (17) fail** | 19.09 (10) pass |
| teardown_result_write[init] | 4 s | **4.62 fail** | **4.66 (17) fail** | **4.45 (10) fail** |

- The §5.1 fix holds on real agents: `artifact_collection` dropped from 3.97 s to 1.28 s and every per-step phase has wide margin.
- `teardown_result_write[init]` still fails in both legs. It is tail-driven: forge median about 2.3 s, with 2 of 17 samples over 4 s (5.71 s b01 guided, 4.40 s b02 baseline); one-shot has 2 of 10 over 4 s (4.67 s, 4.17 s). §5.1 predicted exactly this residue (pure `compose down` under host load).
- `environment_start[warm]` fails on forge only, from the b06 pair (23.3 s, 20.1 s); the other 15 samples are 3-16.5 s. forge-02 did not overlap the one-shot leg; any contention came from its own 3 concurrent trials and the `make assess-regrade` jobs run in Docker after `c068e4c`.
- `scorer_orchestration` is still not checked by `perf check` (measured in `raidar assess`, not `raidar run`).

### 11.9 Review surface

- `make review-surface-test REVIEW_RUNS_ROOT="runs/pilot-v2/pilot-forge runs/pilot-v2/pilot-circuit-eval-oneshot" REVIEW_STORE=runs/pilot-v2/assessment-store`: **55 passed** (`gate-evidence/review-surface/review-surface-test.log`). The suite's Quality data held 84 runs (42 synthetic + all 42 pilot-v2 trials) and the review projection 93 runs across 19 experiments.
- Pilot-v2-only Quality projection (`make review-surface-quality-data QUALITY_RUNS=... QUALITY_STORE=...`, `qp-pilot-v2.json`): 42 runs across 2 experiments, not labelled synthetic.
- Screenshots (`gate-evidence/review-surface/*.png`, served by `make review-surface-serve` with the same roots and store): run detail with the Quality panel for forge b00 baseline (S 21.0, per-checkpoint C/V/E/H/R/G/I/S), forge b05 guided and one-shot b01 guided; `/quality` for both experiments (component curves, paired Δ "7 / −9" pairs, the 18 infra-excluded forge trials listed as "Left out ... invalid trajectory (infra-excluded)"); and the forge b00 guided Quality run view. No console or page errors.
- The 18 infra-invalid forge trials render as `Unscored` in the run list, which is the intended behaviour.

### 11.10 Exit criteria re-evaluated on v2

| # | Criterion | v1 status | v2 status | v2 evidence |
|---|---|---|---|---|
| 1 | All pilot trajectories complete with ≤1 infra exclusion per arm | Met | **Not met (forge); met (one-shot)** | one-shot 5+5 valid, 0 infra. forge 7+7 valid, 9+9 infra, all from the drive being switched off mid-run (§11.1). **Update (§11.13): met after the b07-b15 rerun, 16+16 valid, 0 infra.** |
| 2 | Context-isolation and no-test-leak checks green on every trajectory | Met | **Met** | 122/122 executed steps on valid trajectories (112 forge + 10 one-shot) have a clean `raidar-reset.json` (no error, no residual, no failed reset) and `pre-agent-leak.json` `clean: true`. `no_oracle_leak` passes on 24/24 valid trajectories. |
| 3 | Every component's discovery probe passes | Met (codex-cli) | **Met (codex-cli)** | `component_probes` passes on 24/24 valid trajectories. Offline `probe_step` re-run: context-reset and prompt-wrapper@1 56/56 forge baseline steps; context-reset, prompt-wrapper@1, rules-file@1, skills@1, lint-config@1, stop-hook@1 and review-loop@2 56/56 forge guided steps; 5/5 for every component in both one-shot arms. Same caveats as v1 (Claude Code not run; lint-config presence-only; stop-hook fired-only under Codex). |
| 4 | Offline rescoring reproduces deterministic observations byte-identically | Met | **Met** | §11.2: 108/108 Assessment files byte-identical across two fresh stores; 108/108 `replayed_identical`. |
| 5 | G2 golden parity against the SCB fork passes | Met | **Met** | Unchanged code path; part of `make quality`, which passes on this tree. |
| 6 | N1 budgets hold (`raidar perf check`) | Not met | **Not met** | §11.8: `teardown_result_write[init]` 4.66 s (forge) and 4.45 s (one-shot) against 4 s; `environment_start[warm]` 20.77 s (forge) against 20 s. The v1 `artifact_collection` miss is fixed on real agents. |
| 7 | Frozen review-surface suite green; Quality element live | Met | **Met** | §11.9: 55 passed with the pilot-v2 runs and store; run-detail Quality panel and `/quality` render pilot-v2 data (screenshots). |
| 8 | The analysis gives an honest verdict (`inconclusive` is acceptable) | Met | **Met** | one-shot primary **inconclusive** (5 pairs). forge primary **insufficient** because the exclusion share exceeds the lock's 20%; the analysis refuses to call an effect from a crash-truncated leg, which is the intended behaviour. No overrides; thresholds pinned by the locks. |

**Summary:** 6 of 8 met on v2. Criterion 1 fails on forge only, because the drive holding OrbStack's data was switched off mid-run (resolved in §11.13); criterion 6 fails on teardown tail latency (both legs) and one forge env-start pair.

### 11.11 E2E local process confirmed?

**Verdict: yes for the process; no for the forge experiment's data.** Every stage ran on real Luna trials through public make targets, and each stage consumed the previous stage's output without manual repair:
- the one-shot leg is a complete, clean end-to-end pass (10/10 valid, assessed, rescored byte-identically, analysed to an `inconclusive` verdict, rendered in the review surface);
- the forge leg exercised every stage on 14 valid multi-checkpoint trajectories, but the experiment is incomplete (criterion 1) because the drive holding OrbStack's data was switched off mid-run (completed in §11.13);
- criterion 6 is a performance budget miss, not a broken stage.

The exact sequence a user runs (forge leg shown; for one-shot use `examples/pilot/oneshot.experiment.yaml`, `CONCURRENCY=2`, `RUN_ID=oneshot-01`):

```sh
# 0. Prerequisites (once): OrbStack running on healthy storage, Codex auth, agent image
orbstack-data-status                       # expect external_available=True; docker --context orbstack ps must work
make codex-auth-setup
make pack-build

# 1. Validate
make experiment-validate EXPERIMENT=examples/pilot/experiment.yaml

# 2. Lock (and check it is current)
make experiment-lock EXPERIMENT=examples/pilot/experiment.yaml
make experiment-lock EXPERIMENT=examples/pilot/experiment.yaml LOCK_CHECK=1

# 3. Plan (dry run)
make experiment-plan EXPERIMENT=examples/pilot/experiment.yaml CONCURRENCY=3

# 4. Execute (real codex-cli + gpt-6-luna low trials)
make experiment-execute EXPERIMENT=examples/pilot/experiment.yaml CONCURRENCY=3 RUNS_DIR=runs/pilot-v2 RUN_ID=forge-02
make harbor-cleanup RUN=runs/pilot-v2/pilot-forge/forge-02     # only after an interrupted run

# 5. Assess (all four scorers, Jev replay-only shadow), then prove determinism
make experiment-assess RUN=runs/pilot-v2/pilot-forge/forge-02 STORE=runs/pilot-v2/assessment-store \
  SCORERS="correctness@1 code-quality@1 resource-usage@1 process-compliance@1" JEV_REPLAY_ONLY=1
make experiment-assess RUN=runs/pilot-v2/pilot-forge/forge-02 STORE=runs/pilot-v2/assessment-store \
  SCORERS="correctness@1 code-quality@1 resource-usage@1 process-compliance@1" REPLAY=1 JEV_REPLAY_ONLY=1
make perf-check RUN=runs/pilot-v2/pilot-forge/forge-02

# 6. Analyze
make experiment-analyze RUNS="runs/pilot-v2/pilot-forge/forge-02" STORE=runs/pilot-v2/assessment-store \
  ANALYSIS_OUT=runs/pilot-v2/gate-evidence/analysis/forge

# 7. Review
make review-surface-test REVIEW_RUNS_ROOT="runs/pilot-v2/pilot-forge runs/pilot-v2/pilot-circuit-eval-oneshot" \
  REVIEW_STORE=runs/pilot-v2/assessment-store
make review-surface-quality-data QUALITY_RUNS="runs/pilot-v2/pilot-forge runs/pilot-v2/pilot-circuit-eval-oneshot" \
  QUALITY_STORE="runs/pilot-v2/assessment-store runs/pilot-v2/assessment-store"
make review-surface-serve REVIEW_RUNS_ROOT="runs/pilot-v2/pilot-forge runs/pilot-v2/pilot-circuit-eval-oneshot" \
  REVIEW_STORE=runs/pilot-v2/assessment-store
```

**Open before calling the forge leg done:**
- Rerun forge blocks b07-b15 (or the full leg as a new run id) and repeat steps 5-7. OrbStack is running again and forge-02's leftover environments are cleaned up (§11.12). Before rerunning only b07-b15 into a second run, confirm how `raidar analyze` pairs a block that is infra-invalid in one run and valid in another; this has not been checked. **Done (§11.13):** b07-b15 rerun as forge-03; the replacement rule pairs them, and criterion 1 is met.
- Decide N1 (§5.1 options): cap trial concurrency for `perf check` runs, or have the budget owner re-baseline `teardown_result_write[init]` (and possibly `environment_start[warm]`) for concurrent operation. Nothing is re-baselined here. **Decided (§5.2):** the user chose a fixture-enforced gate (`make perf-gate`) with report-only `perf-check` on real runs and no re-baseline.
- Known fidelity gap: a submission that declares runtime `dependencies` cannot install them in the offline grader (native SCB grades with network). No v2 snapshot hit it.
- Scorer identity moved with `c068e4c`: oneshot-01's `run.lock.json` assessment section carries the pre-fix scorer digests, so `LOCK_CHECK` reports it as needing a rescore. Its Assessments in `runs/pilot-v2/assessment-store` were scored on the current tree.

### 11.12 Independent walkthrough on a fresh experiment (2026-10-01)

To confirm the process independently of the pilot runs, the README walkthrough was followed as written on a new experiment: `examples/pilot/oneshot.experiment.yaml` copied with its `components/` to `.tmp/e2e-luna/experiment.yaml`, with `id: e2e-luna-oneshot` and `blocks: 2` (codex-cli 0.156.1 + `gpt-6-luna`, reasoning low, OrbStack). Only public `make` targets were used, and each stage consumed the previous stage's output without manual repair.

| Step | Command (README section) | Result |
|---|---|---|
| Install | `cp orchestrator/.env.example orchestrator/.env`, `make env-setup` | ok; Codex used file-backed ChatGPT auth (`CODEX_AUTH_MODE` empty means `auto`) |
| 1 | `make problem-list`, `make problem-import PROBLEM=scb/circuit_eval.checkpoint_1@38d627ecf668`, `make pack-build` | ok; problem validated and verified against its manifest |
| 2 | `make experiment-init EXPERIMENT=.tmp/e2e-init/rules-trial/experiment.yaml`, then validate | ok; the starter matches the README example apart from the description and the explicit `headline_policy` line |
| 3 | `make experiment-validate`, `make experiment-lock`, `make experiment-plan` | ok; lock `ca1169ab23f1`, 4 trials planned at concurrency 2 |
| 4 | `make experiment-execute ... RUN_ID=first` | ok; 4/4 valid, 0 infra, 9 min wall (17:21-17:30 UTC); 4/4 steps with a clean reset and leak record; `no_oracle_leak` and `component_probes` pass on all 4; review-loop@2 parsed `CHANGES` in both rounds of both guided trials (`rounds_exhausted`) |
| 4 | `make perf-check RUN=runs/e2e-luna-oneshot/first` | **exit 2**: every phase `insufficient` (4 samples < `MIN_SAMPLES` 10). With `MIN_SAMPLES=4` all 11 applicable budgeted phases pass (env start warm p95 4.24 s, teardown 2.12 s). README now says this. |
| 5 | `make experiment-assess ... SCORERS="correctness@1 code-quality@1 resource-usage@1 process-compliance@1"` | ok; 16 `added`, none provisional (every Jev residue decision was deterministic) |
| 5 | the same with `REPLAY=1`, and into a fresh store | 16/16 `replayed_identical`, store unchanged; fresh store byte-identical (16 files) |
| 6 | `make experiment-analyze ...` | ok; primary `solve_rate` 1.0 vs 0.5, **insufficient** (2 pairs < 5, pinned by the lock): the honest verdict for 2 blocks. Manipulation check `insufficient_data`. Cost $0.0038 vs $0.0182 per trajectory. |
| 7 | `make review-surface-quality-data`, `make review-surface-serve` | ok; 4 Quality runs across 1 experiment; the server's `/api/runs` lists the 4 runs |
| 7 | `make review-surface-test REVIEW_RUNS_ROOT=runs/e2e-luna-oneshot REVIEW_STORE=stores/e2e-luna-oneshot` | 55 passed (Quality data 42 synthetic + 4 real runs) |

Doc fixes from this walkthrough (README): `perf-check` needs `MIN_SAMPLES` for runs under 10 trials; `process-compliance@1` stores a `provisional` Assessment when Jev cannot answer; `make review-surface-test` rewrites `review-surface/data`, so project again before serving. In §11.8, the claim that forge-02 overlapped the one-shot leg was wrong (oneshot-01 finished before forge-02 started) and is corrected.

The §11.1-§11.10 figures were rechecked against the artifacts: valid and infra counts, checkpoint states, 122/122 clean steps, `no_oracle_leak` and `component_probes` 24/24, costs and wall times from the run records; effects, curves, manipulation check and J2 rows from `analysis.md`; 108 store digests recomputed equal to `store-a.sha` and `store-b.sha`; Jev audit 548 decisions (532 deterministic, 16 `jev_error`, 0 enforced) and 8 provisional Assessments (7 forge, 1 one-shot); perf p95 values from the perf logs; 55 passed in the review-surface log. All matched.

**Verdict: the local end-to-end process with Luna is confirmed.** The forge leg's data is still incomplete (criterion 1) and N1 is still open (criterion 6); neither is a broken stage.

### 11.13 Forge leg completed: b07-b15 rerun (2026-10-01/02)

**Replacement path (no driver change).** `raidar run` refuses an existing run directory, so forge-02 cannot be extended in place. The supported path is a second run of the same lock, analysed together with the first: `raidar analyze` keeps one record per trial id, a valid trajectory replaces an invalid one, the replaced ids are listed in `replaced_trajectories`, and two valid records for one trial are refused (`analysis/sources.py` `select_records`, tested in `test_analysis_command.py`). Every b07-b15 trial was invalid in both arms of forge-02, so both arms of each rerun block come from the new run and every block still pairs within one run. The lock was current (`make experiment-lock ... LOCK_CHECK=1`: `5a73637e8ac3`, the lock forge-02 executed under), and the planned trial hashes matched forge-02's.

```sh
make experiment-execute EXPERIMENT=examples/pilot/experiment.yaml CONCURRENCY=3 \
  BLOCK="7 8 9 10 11 12 13 14 15" RUNS_DIR=runs/pilot-v2 RUN_ID=forge-03
make experiment-assess RUN=runs/pilot-v2/pilot-forge/forge-03 STORE=runs/pilot-v2/assessment-store \
  SCORERS="correctness@1 code-quality@1 resource-usage@1 process-compliance@1" JEV_REPLAY_ONLY=1
make experiment-assess ... REPLAY=1 JEV_REPLAY_ONLY=1
make experiment-analyze RUNS="runs/pilot-v2/pilot-forge/forge-02 runs/pilot-v2/pilot-forge/forge-03" \
  STORE=runs/pilot-v2/assessment-store ANALYSIS_OUT=runs/pilot-v2/gate-evidence/analysis/forge-complete
make perf-check RUN=runs/pilot-v2/pilot-forge/forge-03
make perf-check RUN="runs/pilot-v2/pilot-forge/forge-02 runs/pilot-v2/pilot-forge/forge-03"
```

**Execution.** forge-03: 18/18 valid, 0 infra, 18:33-01:05 UTC (6 h 32 m), concurrency 3, codex-cli 0.156.1 + `gpt-6-luna` low on OrbStack (`external_available=True` before launch). Every validity check passed on all 18 (including `no_oracle_leak` and `component_probes`). Spend $1.674 (baseline $0.326, guided $1.348).

**Assessment.** 72 `added` into `runs/pilot-v2/assessment-store`; the 108 existing files are byte-identical before and after (add-only; `gate-evidence/assess/store-a-{pre,post}-forge-03.sha`). Replay: 72/72 `replayed_identical`, store unchanged. Rescore into store B: 180 files byte-identical to the canonical store. 5 process-compliance Assessments are `provisional` (Jev replay-only, no cached decision), as in §11.2.

**Exit criterion 1: met.** After replacement, 16/16 valid per arm, 0 infra (the 18 forge-02 b07-b15 records are listed as replaced).

**Effects (`gate-evidence/analysis/forge-complete/analysis.md`, 16 pairs, 0 excluded; bootstrap intervals).**

| Metric | View | n | Baseline | Guided | Δ [95% CI] | Verdict |
|---|---|---|---|---|---|---|
| solve_rate (primary) | continue | 16 | 0.156 | 0.031 | −0.125 [−0.188, −0.063] | **worse** |
| reached | strict | 16 | 1.25 | 0.25 | −1.0 [−1.5, −0.5] | **worse** |
| final_solved | continue | 16 | 0 | 0 | 0 (McNemar 0/0/0/16, p = 1) | inconclusive |
| headline S (`scb-canonical@1`) | headline | 15 | 12.77 | 2.85 | −9.92 [−15.58, −4.27] | **worse** (1 ineligible) |
| V verbosity | quality | 16 | 0.9986 | 0.9990 | +0.0004 [−0.0012, 0.0020] | inconclusive |
| E erosion | quality | 16 | 0.114 | 0.242 | +0.128 [0.067, 0.186] | improved |
| H architecture | quality | 16 | 1.000 | 0.9991 | −0.0009 [−0.0015, −0.0004] | worse |
| R rework | quality | 16 | 0.256 | 0.271 | +0.015 [−0.013, 0.047] | inconclusive |
| G regression | quality | 15 | 0.183 | 0.042 | −0.141 [−0.210, −0.072] | worse (1 ineligible) |

The 7-pair direction of §11.3 holds on 16 pairs: guided solves checkpoints 1-2 in 2/16 trajectories vs 10/16 for baseline, and neither arm solves checkpoint 3 or later. Guided's case pass rate is higher from checkpoint 6 (cp7 0.889 vs 0.838). Component directions are the scorer manifest's (each in [0, 1], 1 best).

**Manipulation check: `not_separated`.** `tests_run_before_finish` separates (0 vs 0.947, p = 5e-05); `plan_written` does not (0 vs 0; F4 is still open).

**Cost.** Mean per trajectory: baseline $0.0355 (1.28 M tokens, 596 agent s), guided $0.1584 (7.03 M tokens, 2,397 agent s); Δ +$0.123 [0.114, 0.133]. Spend on the 32 analysed trajectories is $3.10; recorded forge spend is $3.24 (forge-02 $1.569 including steps before the switch-off, forge-03 $1.674).

**J2 projection (16 pairs).** solve_rate SD 0.129, δ 0.125 → 9 pairs (0 more); reached 9 (0 more); rework R 13 (0 more); headline S SD 11.0, δ 5 → 38 pairs (23 more, $7.35); erosion E 51 (35 more, $9.89); regression G 65 (50 more, $12.57); final_solved has no projection (SD 0).

**review-loop@2 (128 guided steps).** Terminations: `review_approved` 63, `rounds_exhausted` 64, `loop_exception` 1 (the forge-02 b03 checkpoint_7 agent timeout, §11.1). Parses: `CHANGES` 159 (all with feedback text), `APPROVE` 63 (no feedback), `NO_VERDICT` 4, all 4 recovered by the re-ask. 0 `EMPTY_FEEDBACK`; none ended `review_unusable_after_reask`. forge-03 alone: 72 steps, 40 approved, 32 rounds exhausted, 1 re-ask.

**Baseline stalls.** 0 baseline steps ended with an empty `/app`, and 0 baseline steps timed out. 1 baseline step left the snapshot unchanged (forge-02 b04 checkpoint_5, 18 s of agent time, graded 0.75 on the carried-forward code). 1 baseline step scored 0.0 case pass rate after changing the code (forge-03 b07 checkpoint_8); that is a broken submission, not a stall.

**N1 overhead (report-only `make perf-check`), p95 seconds.** Raidar's own trial concurrency never exceeded 3 (perf-check peak 3 with and without overlapping runs; per-trial windows show 1-3 open trials at every environment start and teardown). Other Docker activity was present: another project's `slop-code` agent and evaluator containers ran on the same OrbStack VM throughout forge-03, 3-11 at a time (sampled every 30 s; `perf check` cannot detect this). The drive was healthy throughout.

| Phase | Budget | forge-02 valid b00-b06 (n=14) | forge-03 (n=18) | completed forge, 32 analysed trials |
|---|---|---|---|---|
| environment_start[warm] | 20 s | 21.26 fail | 29.74 fail | 25.25 fail (p50 9.59, max 42.06) |
| teardown_result_write[init] | 4 s | 4.86 fail | 5.12 fail | 5.10 fail (p50 2.44, max 8.04) |
| per_trial_fixed_overhead[init] | 27 s | — | 33.08 fail | 27.74 fail |
| per_step_reset | 2 s | | 1.32 pass | |
| artifact_collection | 3.5 s | | 1.32 pass | |
| per_step_overhead | 10 s | | 3.42 pass | |

Every other budgeted phase passes in forge-03. `make perf-check` over both runs (50 trajectories, including the 18 replaced infra records) gives env start 24.61 s and teardown 4.94 s, both warnings, exit 0.

**Decision (3) applied.** Every teardown and environment-start sample was taken at ≤3 concurrent Raidar trials, so the condition for a concurrency-scaled budget (a miss at ≥4 concurrent trials) did not occur. **Budgets are unchanged and nothing is re-baselined**; `overhead-baseline.md` §5 is not edited. Per the decision, the miss at ≤3 concurrent on a healthy drive is reported as a regression to investigate:
- `teardown_result_write[init]`: tail-driven. Median 2.4-2.7 s; 3 of 18 forge-03 samples are over 4 s (b13 baseline 8.04 s, b14 baseline 4.61 s, b14 guided 4.22 s; b13 guided 3.97 s is just under), and all three finished while 7-10 external containers were running. The enforced no-LLM gate (`make perf-gate`) passes at concurrency 1 and 3 on its latest run, but failed this phase at concurrency 3 (4.85 s) on the run before forge-03 (§5.2).
- `per_trial_fixed_overhead[init]`: over budget in forge-03 (33.08 s) and over the 32 analysed trials (27.74 s); it is the sum of the per-trial phases, so it follows the environment-start and teardown tails.
- `environment_start[warm]`: also tail-driven (median about 10 s). The forge-03 outliers are b15 guided 42.06 s and b08 baseline 27.57 s, with 5-8 external containers running.
- The external load is a confound that this run could not remove, so these numbers do not separate a Raidar regression from host contention. A clean answer needs a real-agent leg with no other Docker workload on the VM.

**Verdicts (forge, 16 pairs, thresholds pinned by the lock).** The guided treatment is **worse** on the primary `solve_rate` (−0.125 [−0.188, −0.063]), on `reached` and on headline S (−9.92 [−15.58, −4.27]); **worse** on H and G; **improved** on E; inconclusive on V, R and `final_solved`. These are pilot-scale results on one problem and one model at low reasoning effort, not confirmatory findings. One-shot stays **inconclusive** (§11.4).

### 11.14 Exit criteria, current status (after the forge leg completed, 2026-10-02)

Data: forge-02 + forge-03 (forge, 16+16 valid) and oneshot-01 (5+5 valid), canonical store `runs/pilot-v2/assessment-store` (180 Assessments).

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | All pilot trajectories complete with ≤1 infra exclusion per arm | **Met** | forge 16/16 valid per arm, 0 infra after replacing the 18 forge-02 b07-b15 records with forge-03 (listed in `replaced_trajectories`; `select_records` refuses a trial valid in two runs). one-shot 5/5 per arm, 0 infra. |
| 2 | Context-isolation and no-test-leak checks green on every trajectory | **Met** | 266/266 executed steps on valid trajectories (forge-02 112, forge-03 144, one-shot 10) have a clean `raidar-reset.json` (no error, residual or failed reset) and `pre-agent-leak.json` `clean: true`. `no_oracle_leak` passes on 42/42 valid trajectories. |
| 3 | Every component's discovery probe passes | **Met (codex-cli only)** | `component_probes` passes on 42/42 valid trajectories. The offline `probe_step` re-run (§11.10) covers forge-02 and one-shot; it was not repeated on forge-03, whose evidence is the validity check. Caveats unchanged: Claude Code never ran; lint-config is presence-only; stop-hook is fired-only under Codex. |
| 4 | Offline rescoring reproduces deterministic observations byte-identically | **Met** | 180/180 Assessment files byte-identical between the canonical store and a fresh store B; forge-03 replay 72/72 `replayed_identical` (forge-02 and one-shot 108/108, §11.2). The 108 earlier files were unchanged by adding forge-03. |
| 5 | G2 golden parity against the SCB fork passes | **Met** | Part of `make quality`, which passes on the commit that adds this section. |
| 6 | N1 budgets hold | **Met under the enforced gate; open regression** | By the user's decision (§5.2) the criterion is judged on `make perf-gate`: its latest run passes every budget at concurrency 1 and 3. Caveats: the previous complete gate run failed `teardown_result_write[init]` at concurrency 3 (4.85 s), and report-only `perf-check` on the real forge runs warns on `environment_start[warm]` (25.25 s), `teardown_result_write[init]` (5.10 s) and `per_trial_fixed_overhead[init]` (27.74 s) at ≤3 concurrent trials, with another project's containers on the VM. No budget changed. |
| 7 | Frozen review-surface suite green; Quality element live | **Met** | `make review-surface-test REVIEW_RUNS_ROOT=runs/pilot-v2 REVIEW_STORE=runs/pilot-v2/assessment-store`, rerun after forge-03: 55 passed; the projections include forge-02 and forge-03. |
| 8 | The analysis gives an honest verdict | **Met** | forge primary **worse** at 16 pairs with no exclusions; one-shot primary **inconclusive** (5 pairs). Thresholds pinned by the locks, no overrides. `make experiment-analyze` reproduces `gate-evidence/analysis/forge-complete` byte for byte. |

**Summary:** all 8 criteria are met on pilot v2 data, criterion 3 on the substituted AgentSpec only, and criterion 6 only under the fixture-enforced model, with a real-run regression still to investigate.

### 11.15 Decisions now open for the user

1. **Phase 2 entry.** Every exit criterion is met (§11.14). *Proposal:* proceed, with the N1 regression investigation (2) as a tracked item rather than an entry blocker, because the enforced gate passes. Say so if the unstable c3 teardown margin should block instead.
2. **N1 regression investigation.** The real-run misses happened at ≤3 concurrent trials on a healthy drive, so decision (3) treats them as a regression, but external Docker load (another project's containers) confounds every real-run sample and one fixture gate run. *Proposal:* rerun one real-agent forge leg (or the c3 fixture several times) with no other Docker workload on the VM. If the misses persist on a quiet host, profile `compose down` and environment start; if they vanish, decide whether the gate must be run on a quiet host.
3. **The treatment made forge worse.** guided solves checkpoints 1-2 in 2/16 trajectories against 10/16 for baseline, mostly by failing two input-validation cases at checkpoint 1 (§11.3). Decide whether to investigate the guided components (rules, skills, review-loop@2) for this effect before any confirmatory run, or accept it as the measured result.
4. **Manipulation check (F4).** `plan_written` is 0 in both arms, so the overall verdict cannot be `separated`. Add a plan instruction to the treatment, or drop `plan_written` from the lock's requested checks.
5. **Sample size.** At 16 pairs, `solve_rate`, `reached` and R need no more pairs at the default δ; headline S needs 23 more ($7.35), E 35 more ($9.89) and G 50 more ($12.57). one-shot `solve_rate` needs about 350 pairs. Decide which metrics a confirmatory run must power.
6. **S2-SS-1: the definition of C. Resolved 2026-10-02 (§11.16).** C now follows the SCB fork: the mean per-checkpoint case pass rate. *Correction:* this item first said every headline S in this packet used the mean case pass rate. That was wrong. Only the G2 golden path (`scorers/code_quality/arithmetic.py`) did. Analysis (`analysis/assessed.py`) and the review projection fed solved/K into S, so the headline S figures in §11.3, §11.4, §11.7 and §11.13 are solved/K-based. §11.16 gives the corrected values.
7. **S2-SS-2: S when C = 0. Resolved 2026-10-02 (§11.16).** The fork marks S ineligible when any quality component is missing, even at C = 0, and `@1` keeps that rule. The new `scb-canonical@2` and `scb-canonical-oneshot@2` score S = 0 whenever C = 0 and are ineligible only when C > 0 and a component is missing. Both pilot experiments now declare `@2`.
8. **Jev.** Still shadow only. Live calls now run (key from Infisical): 15,516 provider calls for $2.33 over the whole pilot, and 0 of the 44 new `process-compliance@1` Assessments are provisional (§12.3). **Update (§11.17):** `code-quality.rubric-smells` now has 33 model-made labels and passes the label, minority and sufficiency gates, but it was not promoted. `make jev-verify-promotion` fails on the required `code-quality@1.1` scorer bump, and the frozen threshold does worse than never flagging on the evaluation split.


### 11.16 Scoring-definition alignment (S2-SS-1, S2-SS-2; 2026-10-02)

User decision (2026-10-01): "go with what you think best". The recommendation was applied: follow the SCB fork for C, and S = 0 whenever C = 0.

**What the fork computes (read from `slop-code-bench` `src/slop_code/metrics/scoring`).**
- `schema.py` `FORMULAS["correctness"] = "C_p=sum(C_p,k)/K_p"`. `_calculate_problem` takes the plain mean of the per-checkpoint rates.
- `live_evidence._correctness`: `rate = Σ pass_counts / Σ total_counts` over all test groups, including Regression. Skipped tests count in neither (`evaluation/report.py` `add_test_result`). Raidar's `strict_pass_rate` already matches this.
- An unproduced checkpoint (no inference result) gets `rate 0`. A produced checkpoint with `total = 0` is ineligible (`canonical_test_denominator_zero`).
- Any produced checkpoint without quality evidence makes the whole generation ineligible, whatever its correctness. The fork has no C = 0 exception.

**What Raidar did before.** `scb-canonical@1` itself did not define C. `spec.policies.checkpoint_correctness` (solved/K) fed S in analysis, the review projection and the problem-validation preview. Only the G2 golden path used the fork's mean. Because nearly every forge checkpoint is a near miss (case pass rates 0.8-0.99, solved only at checkpoints 1-2), solved/K pushed forge S towards 0.

**What changed.**
- `spec/policies.py`:
  - `problem_correctness` (the fork's `C_p`) and `checkpoint_pass_rate` are the single definition of C. Analysis, the review projection, the Quality view and the validation preview all use it, with exact passed/graded counts where the scorer records them.
  - The policy definition now records C, so the `@1` digests changed.
  - `checkpoint_correctness` is renamed `checkpoint_solve_rate`. It stays the primary outcome `solve_rate`, which is unchanged.
- New `scb-canonical@2` and `scb-canonical-oneshot@2`: `@1` plus `zero_correctness`.
- Analysis reports C as its own metric (`correctness`).
- `examples/pilot/{experiment,oneshot.experiment}.yaml` declare `@2`. After the relock, `lock_sha256` is unchanged (`5a73637e8ac3`, `827ebb5a7ca9`), so forge-02, forge-03 and oneshot-01 still match their locks.
- ADR-001 §2.4 is corrected. `requirements.md` has a change-log entry; the frozen F15 text is untouched.
- Boundary tests:
  - `test_score_policy_behaviors.py`: C = 0 with G missing, G ineligible or all quality missing gives S = 0 under `@2` and ineligible under `@1`. C > 0 with a gap is ineligible under `@2`. An unmeasured C, or an unsupported K, stays ineligible. The fork mean is checked against solved/K.
  - `test_analysis_outcomes.py`: the same cases through `build_outcome`, plus a reached checkpoint with no graded case giving an ineligible C (never 0).

**Rescore (add-only).** Only `code-quality@1`'s identity moved, because its transitive source includes `spec/policies.py`. `correctness@1` and `resource-usage@1` are unchanged.
- `make experiment-assess SCORERS="code-quality@1"` over forge-02, forge-03 and oneshot-01 into `runs/pilot-v2/assessment-store` gave 45 `added`.
- The 180 earlier files are byte-identical before and after (`gate-evidence/assess/store-a-{pre,post}-scoring-alignment.sha`; 0 changed, 45 new).
- All 45 new results equal their predecessors observation for observation; only the scorer identity differs.
- The rescore and analysis ran from a clean export of HEAD plus this change only, because other workstreams' in-flight `jev` edits in the worktree also move `process-compliance@1`'s identity.

**Re-analysis (`gate-evidence/analysis/{forge-complete,oneshot}-scb2/`; the earlier `forge-complete` and `oneshot` outputs are kept).**

| Leg | Metric | n | Baseline | Guided | Δ [95% CI] | Verdict | Before (§11.13 / §11.4) |
|---|---|---|---|---|---|---|---|
| forge | headline S (`scb-canonical@2`) | 15 | 76.87 | 78.42 | +1.55 [−0.12, 3.33] | inconclusive (1 ineligible) | 12.77 vs 2.85, −9.92, **worse** |
| forge | correctness C (new) | 16 | 0.912 | 0.923 | +0.011 [−0.013, 0.035] | inconclusive | — |
| forge | solve_rate (primary) | 16 | 0.156 | 0.031 | −0.125 [−0.188, −0.063] | **worse** | unchanged |
| one-shot | headline S (`scb-canonical-oneshot@2`) | 5 | 90.25 | 92.48 | +2.23 [−1.56, 6.01] | inconclusive | 54.8 vs 74.5, +19.7, inconclusive |
| one-shot | correctness C (new) | 5 | 0.989 | 0.994 | +0.006 [−0.023, 0.034] | inconclusive | — |

- The primary outcome and every quality component are unchanged (`solve_rate`, `reached`, `final_solved`, V/E/H/R/G as in §11.13 and §11.4).
- The one forge S exclusion is the same G gap as before (`checkpoint_6 → checkpoint_7`, C > 0), so it stays ineligible under `@2`.
- No trajectory has C = 0 in either leg, so the zero-correctness rule did not fire. On this data `@2` gives the same S as `@1` would with the corrected C.
- J2 for headline S at the default δ 5: forge 4 pairs (SD 3.56; 0 more), one-shot 3 pairs (SD 3.05; 0 more), down from 38 and n/a.
- **The forge headline verdict changes from `worse` to `inconclusive`.** Guided's lower checkpoint-solve rate comes from a few failed cases per checkpoint (§11.3), and the fork's C weighs those as near misses. The §11.13 verdict list now reads: guided is worse on `solve_rate`, `reached`, H and G, improved on E, and inconclusive on headline S, C, V, R and `final_solved`.

### 11.17 Jev calibration and promotion attempt (2026-10-02)

**Outcome: not promoted. Both policies stay `shadow`.** Evidence is in `runs/pilot-v2/gate-evidence/jev-labelling/` (labelling rounds, fixed split) and `runs/pilot-v2/gate-evidence/jev-promotion/` (dry runs, what-if, verify output).

**Labels are model-made.** All 33 labels on `code-quality.rubric-smells` are blind labels with reviewer kind `model`, model id `claude-opus-5-5`, imported in two rounds with `make jev-import-blind REVIEWER=model REVIEWER_MODEL=claude-opus-5-5`. There are 0 human labels. The labeller was given only blind exports (state and questions). Selection used Jev's answers only for aggregate pool enrichment: `round-2/select.py` read them from the audit to build the pools and printed only aggregate counts. The same agent ran selection and labelling, so the artifacts cannot prove that agent never saw an answer (§12.4). Jev's decision log was unchanged by both imports (sha256 checked). Sampling was enriched towards Jev's provisional positives so that the minority class could reach 5. Metrics on these labels are therefore not population estimates.

**`code-quality.rubric-smells` (question set `scb-rubric@2`).** `make jev-promote ... CALIBRATION=... EVALUATION=... DRY_RUN=1` was run on the split fixed in labelling round 1 (`jev-labelling/split.json`). That split has 21 calibration and 21 evaluation trials, whole trials, disjoint and stratified by problem and arm.

| Gate or measure | Value | Result |
|---|---|---|
| Labelled decisions (≥30) | 33 (calibration 18, evaluation 15) | pass |
| Minority labels (≥5) | 9 reference positives (calibration 6, evaluation 3) | pass |
| Input sufficiency (≥0.8) | 0.9993 | pass |
| Threshold, fitted on calibration only and frozen | 0.65 | — |
| Calibration precision / recall / agreement | 0.7143 / 0.8333 / 0.8333 | reported |
| **Evaluation** precision / recall / agreement | **0.2 / 0.3333 / 0.6** | reported |
| Regression corpus sha256 | `da2c672734c84be0623ea6c3de47d0ae693c6da9956a2a2ecfa02bcb32b5dc21` | — |
| Reviewers | `{model: 33}`, models `[claude-opus-5-5]` | recorded |
| `make jev-verify-promotion` | fails: "scorer code-quality is at 1; the promotion ships with code-quality@1.1, so bump its manifest (and registrations) first" | **fail** |

How the verify result was obtained: `make jev-verify-promotion` on the repo policy reports "has no recorded promotion", because nothing is recorded until a promotion is written. To check what the verifier would say after a promotion, the promotion was written to a scratch copy of the policy directory (`jev-promotion/what-if-policies/`) and verified against that copy. The assessment store was byte-identical before and after.

Why the policy was not promoted:
1. **The verifier gate fails.** A promotion ships with the next scorer minor version (design memo §3.5). `code-quality@1.1` does not exist, and `code-quality@1` does not consume `code-quality.rubric-smells` decisions at all; only `process-compliance@1` is bound to a Jev policy (`scorers/catalog.py` `BUILTIN_STORE_IMPLEMENTATIONS`). Writing `mode: enforce` now would leave a repo policy that `make jev-verify-promotion` rejects. It would also change no Assessment, so a rescore would show nothing.
2. **The frozen threshold does not hold out.** On evaluation, never flagging agrees with 12 of 15 labels (0.8), but the threshold agrees with 9 of 15 (0.6). It catches 1 of 3 positives with 4 false flags. The provisional 0.5 cut does no better there (0.6 agreement, precision 0.3333, recall 1.0). The gate as coded has no metric floor, so this is a judgement, not a gate failure.

What enforce would change in the shadow audit (inference from the what-if; nothing was enforced): of 17,248 Jev-answered decisions, 1,522 would be flagged at 0.65, against 2,449 at the provisional 0.5. The audit holds 41,405 decisions; the other 24,157 were settled deterministically. 0 decisions are enforced today.

**`process-compliance.residue` does not qualify.** The dry run fails with "0 labelled decisions < 30", "0 minority labels < 5", "input sufficiency 0/62 < 0.8" and "a split needs labelled decisions from at least 2 trials". The 28 `jev_error` decisions and the 34 partial-input decisions keep sufficiency below 0.8 whatever the labels are.

**What promotion would need (proposal):**
- a `code-quality@1.1` that emits a Jev-tier rubric metric bound to this policy, with registrations and a new epoch;
- a human spot-check of the two stances that 7 of the 9 positives rest on: mkdir in persistence code counts as auto-correction or silent resource creation (5), and raw string tags count as stringly typed (2);
- more evaluation positives (only 3 today), so that held-out precision and recall rest on more than a handful of cases;
- a re-fit on the same split, then `make jev-promote` and `make jev-verify-promotion`.

## 12. Phase 1 close-out (2026-10-02)

This section closes Phase 1. Each part names its evidence. Everything under `runs/` is gitignored and local to this worktree.

### 12.1 Secrets via Infisical

- Raidar's secrets live in the Infisical project `mac-os-cf`, environment `dev`, bound by `orchestrator/.infisical.json`. Every make target that runs agents or Jev injects them through `infisical run --env=dev` (`SECRETS=auto|infisical|dotenv`, default `auto`). The Makefile prints only the source (`secrets: Infisical (env dev)` or `secrets: orchestrator/.env`), never a value.
- Behaviour, checked independently through the real Makefile: logged in → Infisical. Not logged in or CLI missing → falls back to `orchestrator/.env` and exits 0 in under a second, with no interactive prompt. `SECRETS=infisical` while not logged in → fails with an `infisical login` message. `SECRETS=dotenv` → `.env`.
- Leak check: the actual values of `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY` and `GEMINI_API_KEY` were compared byte for byte against 228,755 files (the whole worktree including `runs/`, `stores/`, `.tmp/` and `docs/`, plus the session scratchpad). There were 0 hits. A pattern grep of tracked files matched only two test fixtures, neither a real key.
- `orchestrator/.env` still exists. Its ANTHROPIC, OPENAI, GEMINI and `CODEX_AUTH_MODE` entries are empty, so it is now only a fallback. **Deleting it needs your approval** (§12.7).

### 12.2 Claude Code readiness on `claude-haiku-4-5`

- **Result: blocked on account funding, not on Raidar.** Run `runs/smoke/20261002T030840Z` used `make agent-smoke HARNESS=claude-code`: claude-code 2.1.285, `anthropic/claude-haiku-4-5`, no reasoning effort, OrbStack. The key reached the agent from Infisical, and the agent reached the Anthropic API, which answered `Credit balance is too low`. The trial ended `infra_error` (0 valid), and no tokens were spent.
- A presence-only credential preflight (`execution/credentials.py`) runs in `raidar run` before any trial starts. With empty credentials (`SECRETS=dotenv`), `make agent-smoke HARNESS=claude-code` exits 2 with "credential preflight failed; no trial was started". `docker ps -a` showed no new container and no new run directory. The preflight checks presence only, so an unfunded key still gets through to a trial, as happened above.
- The Claude mappings of the built-in components are still unit-tested only. **Next action (yours):** fund the Anthropic organisation behind `ANTHROPIC_API_KEY` in `mac-os-cf/dev`, then run `make smoke-agents SMOKE_ARM=claude`.

### 12.3 Live Jev (shadow) and its cost

Pinned model `typesafe/jev-1.13-20260917`, mode `shadow`, `counts_toward_verdicts: false` everywhere, 0 decisions enforced. Store: `runs/pilot-v2/assessment-store`.

| Policy | Decisions | Jev-answered | Fresh provider calls | Cache hits | Cost (USD) | Evidence |
|---|---|---|---|---|---|---|
| `code-quality.rubric-smells@2` (`scb-rubric@2`), 42 trials, 266 checkpoints | 41,405 (24,157 carried forward deterministically) | 17,248; question, symbol and criterion coverage 100%; 12 partial-input, 0 missing | 15,499 | 1,749 | 2.3221 | `gate-evidence/jev-rubric-live/` |
| `process-compliance.residue` (`process-residue@1`), forge-02, forge-03, oneshot-01 | 1,702 on 2026-10-02 (1,656 deterministic) | 34 | 17 | 17 (replay pass) | 0.0037 | `gate-evidence/jev-live/` |
| **Total** | | | **15,516** | | **≈ 2.33** | |

- The `process-compliance@1` rescore added 44 Assessments (17 forge-02, 17 forge-03, 10 oneshot-01). **0 are provisional.** The replay pass gave `replayed_identical` for every one and left the store unchanged (`store-assessments-{pre,post-live,post-replay}.sha`).
- 12 `jev_error / cache_miss` rows were logged at 01:05Z on 2026-10-02. They come from a replay-only rescore made before the key existed, and the live run superseded them.
- Every Jev answer is `uncalibrated`: no threshold is in force, and no answer changes a verdict or an S value.

### 12.4 Labelling provenance and readiness

- 33 blind labels on `code-quality.rubric-smells`, all with reviewer kind `model` and model id `claude-opus-5-5` (round 1: 18, round 2: 15). There are 0 human labels. They cover 33 distinct requests and cache keys, with at most one label per trial-request and no replay duplicates. An independent re-derivation of every label from the labeller's answers against Jev's audited answers at the 0.5 cut gave 0 mismatches.
- What the labeller received: `blind-round-{1,2}.jsonl` carry only `policy`, `question_set`, `questions`, `request_id`, `state`, `subject` and `trial`. They contain no answers, verdicts, cache keys, costs or model ids.
- **Limit on the provenance claim.** Pool selection was enriched towards Jev's provisional positives so that the minority class could reach 5. To build the pools, `round-2/select.py` read Jev's answers from the audit; it printed only aggregate counts and shuffled the output. The same agent ran selection and labelling. The data handed to the labeller is provably blind, but the artifacts cannot prove that the agent itself never saw an answer. *Proposal:* a separate agent or person runs selection in future rounds, and the labeller receives only the blind export.
- Readiness (`code-quality.rubric-smells`): labelled 33 ≥ 30, minority 9 ≥ 5, input sufficiency 0.9993 ≥ 0.8, all pass. The labels are enriched and model-made, so metrics on them are not population estimates. `process-compliance.residue` has 0 labels and sufficiency 0/62, so it does not qualify.

### 12.5 Promotion outcome

- **Not promoted. Both policies stay `shadow`** (details in §11.17). On the fixed 21/21-trial split the frozen threshold is 0.65. Calibration P/R/agreement is 0.7143/0.8333/0.8333; evaluation is 0.2/0.3333/0.6, worse than never flagging (0.8). `make jev-verify-promotion` would fail on the missing `code-quality@1.1` bump, and `code-quality@1` does not consume this policy's decisions.
- **Tooling fix in this close-out.** `make jev-promote` used to write `mode: enforce` whenever the audit re-derived. It did so even when the scorer's minor bump the promotion names did not exist yet, which left a repo policy that `make jev-verify-promotion` rejects. `raidar jev promote` now refuses to write in that case: it exits 1 and reports `"blocked_on_scorer_bump": true` and `"written": null`. `--allow-pending-scorer-bump` (`make jev-promote ... ALLOW_SCORER_BUMP=1`) writes anyway, for the deliberate sequence "promote, then bump the scorer". A dry run reports the flag and exits 0. Covered by `test_promote_writes_enforce_only_when_the_audit_verifies`.
- What promotion would need is listed at the end of §11.17: a `code-quality@1.1` bound to the policy, a human spot-check of the two stances behind 7 of the 9 positives, more evaluation positives, and a re-fit.

### 12.6 Scoring alignment (S2-SS-1, S2-SS-2)

- C follows the SCB fork (`~/Projects/inflight/slop-code-bench` at `3357617`). It is the plain mean of per-checkpoint case pass rates. Skipped cases leave the denominator, an unreached checkpoint scores 0, and a checkpoint with 0 graded cases is ineligible. The mean runs at Decimal precision 50 with ROUND_HALF_EVEN. An independent read of the fork confirmed the match (§11.16).
- `scb-canonical@2` and `scb-canonical-oneshot@2` set S = 0 when C is used and exactly 0. Both pilot experiments declare `@2`. Re-analysis moved the forge headline from `worse` to `inconclusive`, and the primary `solve_rate` is still `worse` for guided (§11.16).
- **Coverage gap closed in this close-out.** No pilot trajectory has C = 0 under the new C, so pilot data never exercises the C = 0 → S = 0 path. A new end-to-end test, `test_v2_scores_c_zero_trajectories_zero_end_to_end_while_v1_leaves_them_unscored` (`tests/test_analysis_command.py`), writes trials, rescores them with the real scorers and runs `raidar analyze` with code-quality never assessed. Under `@2` the two C = 0 pairs score S = 0 and are paired, and the three C > 0 trajectories stay unassessed. Under `@1` all five are unassessed.
- Minor, not fixed: `_checkpoint_rate` falls back to `Decimal(str(float))` when an observation has no numerator or denominator, which may differ from the fork's exact ratio in the last digits. Current scorers record exact counts, so no pilot value is affected.

### 12.7 Cleanup and what remains for you

- Done: scenario-era code paths, the frozen fixture, `--legacy-root` and their import contracts are removed, and the review projection is `schema_version 2` with problem and arm naming (commit `1cf9d2f`). Generated `.coverage` files are untracked. No Raidar trial container is left on OrbStack (`docker ps -a` shows only other projects' containers and signoz).
- Unchanged by design (a separate decision is pending with you): the experiment folder layout (`examples/`), experiment `kind`, and the runtime-compat gate.
- **Needs your decision or input:**
  1. **Delete the plaintext `orchestrator/.env`.** Infisical works, and every credential entry in `.env` is empty, but it remains the fallback when Infisical is not logged in. Deleting it makes `SECRETS=auto` fail closed, with no credentials, instead of silently using `.env`. This was not done because it needs your approval.
  2. **Fund the Anthropic organisation** behind `ANTHROPIC_API_KEY` in `mac-os-cf/dev`. Claude Code readiness (§12.2) waits on it.
  3. **Jev promotion path**: whether to build `code-quality@1.1` with a Jev-tier rubric metric, and who does the human spot-check (§11.17).
  4. The open §11.15 items: Phase 2 entry, the N1 regression investigation, the guided-arm forge effect, the manipulation check (`plan_written`) and the confirmatory sample size.

## 13. Schema v2: Harbor vocabulary, experiment kinds and the runtime-compatibility gate (2026-10-02)

User decisions A-E (2026-10-02) were implemented as one change. The requirements change log has the glossary, the "hosted service" clarification (cloud sandboxes through Harbor environment types are an in-scope option, planned, not implemented) and the research-loop capability (planned). Evidence is in `jobs/pilot-v2/gate-evidence/schema-v2/`.

**What changed.**
- Vocabulary: job (was run), trial (was trajectory), step result (was checkpoint attempt), attempts (was blocks), verifier (was oracle), agent name (`codex`, was harness `codex-cli`), `environment.type` (was `runtime.environment`), headline metric (was headline policy; ids unchanged). The README Concepts table and the CHANGELOG map every name. Old make variables fail with their new name.
- Kept byte for byte, so the pilot's execution identity stays provable: the fingerprint sources of released components (`raidar/oracle/snapshot.py` stays at that path), the grader plugin, the pack image recipe, and the verifier evidence names inside trial directories (`raidar-oracle/`, `oracle_leak`). `tests/test_job_migration.py` fails if a pilot component digest moves.
- Layout and kinds: `experiments/<kind>/<id>/experiment.yaml`, ids `<runtime>__<subject>__<intent>` (`NAME1`, `NAME2`), `kind: ab-test | benchmark | smoke` (`research-loop` reserved; `KIND0`-`KIND3`). The pilot legs are `experiments/ab-test/python__forge__guided-pilot/` and `experiments/ab-test/python__circuit_eval-cp1__guided-pilot/`; the smoke is `experiments/smoke/python__circuit_eval-cp1__harness-health/`; the agent smoke is the benchmark `experiments/benchmark/python__circuit_eval-cp1__agents/`.
- Gate: `RT0`-`RT6` in `build_lock` (every issue at once, through `--set`, `path:` problems, one-shot refs and the smoke targets), `RT6` pack drift at plan/run, `AS1` at `assess`. The facts travel in `ResolvedReference.binding`, which no lock records.
- Lock version 3. Relocking moved every `lock_sha256` (the design is spelled differently): forge `5a73637e8ac3` → `015c57e4f59c`, one-shot `827ebb5a7ca9` → `b54c3605ced9`.

**Pilot jobs migrated, not edited.** `runs/` was renamed `jobs/`. `make experiment-migrate-job` moved forge-02, forge-03 and oneshot-01 onto the v2 locks after checking that each executed lock's execution section, translated field by field, equals the v2 lock's (design, problems, files, problem and component references, conditions, recomputed arm digests, versions, tools). The legacy files are kept in each job's `legacy-v1/` with their sha256 in `job.json` (35, 21 and 13 files). The jobs now live at `jobs/pilot-v2/python__forge__guided-pilot/{forge-02,forge-03}` and `jobs/pilot-v2/python__circuit_eval-cp1__guided-pilot/oneshot-01`. Harbor trial directories were not touched.

**Rescore (add-only).** The scorers' implementation digests moved with the renamed imports, so analysis looks Assessments up under new keys. `make experiment-assess ... JEV_REPLAY_ONLY=1` added 68 + 72 + 40 = 180 Assessments to `jobs/pilot-v2/assessment-store`; the store's earlier 15,787 files are unchanged apart from the two append-only logs (`store-pre-rescore.sha`, `store-post-rescore.sha`). `REPLAY=1` gave 180/180 `replayed_identical`. Each new Assessment equals its predecessor for the same trial and scorer in every result field (`assessment-equality.txt`: 180/180), and none is provisional (every Jev decision replayed from the cache). An aborted first rescore had added 5 Assessments and 21 log lines; they were removed and the store hashed back to its pre-migration state before the recorded rescore.

**Analysis reproduces the numbers exactly.** `make experiment-analyze` over the migrated jobs gives, value for value, the committed `gate-evidence/analysis/forge-complete-scb2` and `oneshot-scb2` reports (`analysis-*-vs-*.txt`, `compare_analysis.py`): every count, mean, delta, interval, p-value, curve, manipulation check and sample-size projection is identical; only the id, lock digest, report schema and two metric labels differ. Forge, 16 pairs: `solve_rate` 0.1562 vs 0.0312, Δ −0.125 [−0.1875, −0.0625] **worse**; `reached` 1.25 vs 0.25 **worse**; `final_solved` 0 vs 0 inconclusive; headline S 76.87 vs 78.42, Δ +1.55 [−0.12, 3.33] inconclusive (15 pairs); C 0.912 vs 0.923 inconclusive; E improved; H and G worse; V and R inconclusive. These are the §11.13 values for every metric §11.16 left unchanged, and the §11.16 values for S and C. One-shot, 5 pairs: `solve_rate` 0.6 vs 0.8 inconclusive, S 90.25 vs 92.48 inconclusive, as in §11.4/§11.16.

**Checks.** `make quality` and `make review-surface-test REVIEW_JOBS_ROOT="jobs/pilot-v2/python__forge__guided-pilot jobs/pilot-v2/python__circuit_eval-cp1__guided-pilot" REVIEW_STORE=jobs/pilot-v2/assessment-store` (55 passed; the specs assert the same signals under the new labels).

**Known consequences.**
- Jev labels and the calibration split of §11.17 name trials by their old qualified ids (`pilot-forge.forge-02.b00-p0-baseline`). The audit keeps them; decisions audited after the migration use the new experiment id. Re-labelling or a split over both id forms is needed before a promotion uses them.
- The pilot v1 jobs (`jobs/pilot/`), `jobs/e2e-luna-oneshot`, `jobs/smoke` and `jobs/pilot-checks` are not migrated; v2 readers refuse them with the migration command.

## 14. Pilot refinement (2026-10-02)

Priority set by the user: make the Phase 1 pilot a fully working process before any Phase 2 work. No other runtime pack was started. This section covers what was refined on top of §13, the Jev relabelling, the fault catalog with its proposed next revision (r3), and the ruff A/B test. Everything under `jobs/` and `stores/` is gitignored and local to this worktree.

### 14.1 Vocabulary, layout, kinds and the gate (refinements to §13)

An independent verification pass over §13 confirmed the layout and kind rules, the runtime-compatibility gate (every bypass vector refused: `--set`, `plan --set`, a hand-edited lock, `assess --rescore` with a foreign bundle), the pilot v2 reproduction of §11.13/§11.16 and `make quality`. It also found gaps, fixed here:

| Finding | Fix | Evidence |
|---|---|---|
| **Step-evidence race.** Under 3 concurrent real-trial test runs, `test_context_isolation_between_steps_in_a_real_harbor_trial` failed 3/3: step 2's `raidar-reset.json` was missing. | Cause found and fixed outside every digest (below). | 6/6 concurrent runs pass (2 rounds × 3, `31 passed` each); the test now also asserts step 1's report predates step 2's. |
| `SECRETS=dotenv` with no `orchestrator/.env` printed `.env` as the source and went on. | It fails with a message. Under `auto` with neither source the target prints `secrets: none` and continues with the shell environment, because file-backed Codex auth needs no secret; the credential preflight still refuses an arm without a credential. README no longer tells you to copy `.env.example`. | `test_secrets_dotenv_without_an_env_file_fails_before_running_anything` |
| Renamed make variables were ignored when set in the environment. | They fail from the environment too. | `test_renamed_make_variables_fail_from_the_environment_too` |
| `NAME1` only applied to `.../experiments/<kind>/<id>/<file>`. | Any file below an `experiments/` directory, at any depth, must be `experiments/<kind>/<id>/experiment.yaml`. Outside `experiments/`, only `NAME2` applies, so scratch copies stay possible. | `test_name1_applies_at_any_depth_below_an_experiments_root`, `test_name1_requires_the_experiment_file_name` |
| UI strings still said run ("Run not found", "Failed to load run", "No projected run", "Runs", "run time", ...). | Swept to trial across the pages, components and wireframes. Routes (`/runs/:runId`) and `data-run-id` stay internal, like the projection's JSON names. | `make review-surface-test` (14.6) |
| The smoke id still said `harness-health`. | Now `python__circuit_eval-cp1__agent-health`, relocked `e6e0988b09f9`. | |
| Scorer subject `trajectory`, evidence input `oracle.cases`, the `@1` scorer descriptions, the problem store's `oracle/` directory. | **Documented as frozen, not renamed.** The first three are hashed into every Assessment key, so renaming them orphans the 180 rescored Assessments of §13. The fourth sits inside each problem's pinned digest. README and AGENTS.md list them. Two scorer docstrings that are not part of any identity now say trial; the scorer identities were checked unchanged before and after. | |

**The step-evidence race, root cause (observed).** Between steps, Harbor *moves* the mounted `/logs/agent` contents into `steps/<step>/agent` on the host. On OrbStack's bind mount the container can keep a cached directory entry for a moved name for a short time. A write to that name then lands in the moved file: step k+1's `setup.sh` overwrote step k's archived `raidar-reset.json`, and step k+1 had none. A minimal probe reproduced it with no Raidar code: a container writes `r.json`, the host moves it, and the container writes it again. The second write landed in the moved file in 7/60 and 2/60 iterations. Removing the name first (`rm -f`, and `rmdir` for a directory) gave 0/60 in both probes, and so did writing a temporary file and renaming it.

**Fix (no digest change).** `step_reset.sh` is a fingerprint source of every built-in component, so changing how it writes would move every pilot component digest (§13). The fix therefore sits in `RunScopedDockerEnvironment`, which is execution plumbing. Before each step's `bash <workdir>/setup.sh`, it removes as root every top-level name that an earlier step archived and the host mount no longer holds. Removing by name a name the host does not have only drops the stale entry, because the host answers "not found". Directories use `rmdir`, never a recursive delete, so archived evidence is never touched. This costs one extra `docker exec` per step from step 2 on, inside the `per_step_reset` gap. **Recorded jobs were not affected.** An audit of the pilot v2 jobs and `ruff-lint-02` (391 step directories) found no duplicated or out-of-order reset report. The only missing ones were 3, all in the aborted `forge-01-grader-uv-dev-group`.

### 14.2 Jev relabelling and refinement (`code-quality.rubric-smells`, `scb-rubric@2`)

**Why relabel.** The 33 labels of §12.4 were selected and labelled by the same agent, so their blindness could not be shown, and they named trials by pre-v2 ids (§13). They were **superseded, not deleted**: 33 append-only `LabelSupersession` records in `jev-audit/<policy>/ledger/`, with that reason. The new `raidar.jev.ledger` makes every calibration reader (promotion, calibration, blind export, label export, review_quality) use `current_labels`, which skips superseded labels. New targets: `make jev-supersede-labels`, `make jev-record-split`, and `make jev-export-blind ... REQUEST_IDS=`.

**Protocol.**
1. The split `relabel-round-1` was recorded before any new label: 22 calibration and 20 evaluation trials, stratified by problem group × arm, with both arms of an attempt kept on one side.
2. Three relabel rounds followed. A selector agent chose the decisions and labelled nothing. Two independent blind labellers (`labeller-1`, `labeller-2`) each answered every question from the blind export, which carries state and questions only. Each round's `blind.jsonl` sha256 matches its manifest.
3. Only consensus labels were imported. Decisions where the labellers disagreed, or one marked uncertain, were held back with no label.

The selector, both labellers and this agent are all `claude-opus-5-5`.

**Result.**
- The store holds 71 label records: 33 superseded and 38 current, none of them on a superseded decision. The 38 current labels are 26 `correct` and 12 `false_positive`, all from reviewer kind `model`. 7 decisions are held back.
- Readiness passes (`ready_for_enforce: true`): 38 labelled, 13 minority, input sufficiency 0.9993.
- With the threshold fitted on the calibration side (0.75), evaluation gives precision 0.64, recall 1.00 and agreement 0.81, against never-flag agreement of 0.67. Under §12.5's model-made labels, evaluation was worse than never flagging (0.6 vs 0.8). On these labels it is better.
- **Still not promoted.** Both policies stay `shadow`. Every label is model-made, selection was enriched towards Jev positives (which inflates recall), and `code-quality@1.1` does not exist.

**Where Jev and the labellers disagree.** 12 Jev false positives remain. Most of them, and most positives, rest on two contested stances that a person has to decide (`docs/reboot/jev-spot-check.md` §1):
- **Stance A:** CLI command-string dispatch counts as `stringly_typed_logic`.
- **Stance B:** a silent `mkdir(parents=True, exist_ok=True)` counts as `silent_resource_creation`. Jev answers ≥ 0.75 on this question for 121 decisions across the store.

Rejecting both stances moves evaluation to precision 0.80, recall 0.80 and agreement 0.90 at a refitted threshold of 0.89.

**Refinement tried (scratch only, not in the repo).**
- The candidate `scb-rubric@3` wording narrows `stringly_typed_logic` (boundary strings are not the pattern) and `validation_mismatch`. It was replayed on the 45 relabel decisions: the 38 labelled plus the 7 held back.
- Replay noise of the pinned `@2` itself is small: mean |Δ| per question 0.007, and 5 of 90 decision classes flip at 0.75.
- At its refitted threshold of 0.80, `@3` gives evaluation precision 1.00, recall 0.71 and agreement 0.90, against 0.64 / 1.00 / 0.81 for `@2`. It removes most stance-A false positives and loses 2 true positives.
- **This is not a valid held-out result.** The wording was written after reading these labels and is scored on the same labels. It needs fresh labels, a new policy version and a new epoch before it counts.

### 14.3 Fault catalog and the proposed r3 (`docs/reboot/pilot-v2-fault-catalog.md`)

The `fault-catalog` skill (`.agents/skills/fault-catalog/`, a first version that needs refining) analysed all 32 valid forge trials (256 step results) of pilot v2 into 8 conceptual fault categories:

| ID | Category | Class | Owner | Status |
|---|---|---|---|---|
| FCR-001 | Verifier asserts error-detail content or output format the specification does not determine | Measurement | problem / verifier | open |
| FCR-002 | Specification constraint prose copied verbatim into error details | Behavioral | component (blocked by the hidden-test firewall) | open |
| FCR-003 | The condition requires verification tools the environment lacks (pytest, ruff) | Process | component | targeted by r3 |
| FCR-004 | Condition components delivered but not consumed (skill opened in 3/128 steps) | Process | component | targeted by r3 |
| FCR-005 | CLI and argument-parser failures escape the error contract | Integration | component | targeted by r3 |
| FCR-006 | A cross-cutting contract change is not propagated to earlier commands | Structural | component | targeted by r3 |
| FCR-007 | A third-party dependency (PyYAML) is unavailable to the offline verifier | Measurement | Raidar verifier | open |
| FCR-008 | The plan step is neither instructed nor detectable, so `plan_written` cannot separate | Process | component + analysis | targeted by r3 |

**What it answers.**

*Why is guided worse on the primary outcome?*
- **Observed:** the whole gap sits at checkpoints 1-2, and it comes from two verifier cases that assert a phrase the specification never states (FCR-001).
- **Observed:** guided implementers copy the specification's sentence verbatim more often: 12 of 16 cp1 snapshots verbatim plus 2 near-verbatim, against 5 of 16 for baseline (Fisher p = 0.003; FCR-002).
- **Inference:** guided does not validate worse. It reproduces the specification's wording more literally, and the verifier asserts a different wording.

*Why does neither arm solve checkpoint 3 or later?* 21 cases fail in all 32 trials. Under `continue` that caps `solve_rate` at 0.25 and makes `final_solved` 0 by construction.

*Exploratory view (exclusions chosen after seeing the data):* excluding those 24 unspecified cases reverses the direction. Adjusted C is 0.957 vs 0.980 for guided, Δ +0.023, 95% CI [−0.002, +0.047]. The independent verification reproduced FCR-001, FCR-002 and FCR-007 exactly. For FCR-003 it got the same order of magnitude but not the exact count (60 vs 65 of 128 guided steps).

**r3 proposal** (`experiments/ab-test/python__forge__guided-r3/`, `rationale.md`). It is an ab-test whose baseline arm is the pilot's guided condition: byte-identical files, condition digest `73cc19faeeb0`. The candidate changes components only:
- C1-C4 are a revised `AGENTS.md`: write `PLAN.md` first; follow the error contract on every failure path; propagate cross-cutting changes; use `unittest`, because pytest and ruff are absent.
- C5 drops `skills@1`, `lint-config@1` and `stop-hook@1`, which were inert under Codex. `review-loop@2` is unchanged.
- Everything else is fixed at the pilot's values. The verification confirmed this field by field, and the lock is current (`12f2b2fb05fc`).
- Hidden-test knowledge is kept out on purpose. Nothing targets FCR-001, FCR-002 or FCR-007, whose right answers are known only from the hidden tests.
- **Predicted:** `solve_rate` inconclusive (capped by FCR-001); any effect shows up in C and S at checkpoints 3-8. The overall manipulation check will be `not_separated` by construction, because both arms run tests.
- **Cost:** about $5 and about 8 h at concurrency 3 for 16 attempts. **Not executed: it needs your approval.**

### 14.4 Ruff A/B test (`experiments/ab-test/python__forge__ruff-lint/`, job `ruff-lint-02`)

This is a functional test of the component revision path and of `lint-tool@1`.
- **Arms:** r1 has the shared framing only. r2 adds `lint-config@1` (the pilot's `ruff.toml`) and `lint-tool@1`, a hash-pinned Ruff 0.8.6 installed per trial outside the agent image. No text tells the agent that Ruff exists.
- **Run:** forge, `codex` 0.156.1, `gpt-6-luna` low, 5 attempts. Lock `cc2a4b147487`.
- **Validity:** 10/10 trials valid, 0 infra errors.
- **Cost:** $0.0351 vs $0.0387 per trial, about $0.37 in total.

| Measure | r1 baseline | r2 ruff | Verdict |
|---|---|---|---|
| `solve_rate` (primary) | 0.20 | 0.05 | Δ −0.15 [−0.32, 0.02], inconclusive |
| C (case pass rate) | 0.915 | 0.919 | inconclusive |
| Headline S (`scb-canonical@2`) | 78.71 | 78.37 | insufficient (4 pairs) |
| E, V, H, R, G | | | inconclusive or insufficient |
| Manipulation check | | | `not_separated` (no plan or test signal in either arm) |

**The tool worked; the agents did not use it** (observed, from the provision records and Codex sessions):
- **Provisioning:** 40/40 r2 step records show install ok, check ok, `ruff 0.8.6`, the pinned target and resolved config `/app/ruff.toml`. The verification found the probe functional: it runs the binary and checks the resolved config.
- **Config use:** r2 agents read `ruff.toml` in 16 of 40 steps.
- **Tool use:** the provisioned `ruff` was invoked in 0 steps. One step (b04, checkpoint 6) tried `python -m ruff` and got `No module named ruff`, because the tool is a binary on `PATH`, not a Python module.

**Exploratory lint density.** At checkpoint 8, with r2's `ruff.toml` rule set applied to both arms by the host's Ruff, findings per kLOC of production code are 96.2 for baseline and 56.2 for r2 (5 trials each, unpaired). The scorer's own isolated SIM-rule density is equal (0.22 vs 0.22). **Inference:** the drop comes from agents reading the config, mostly line length and style, not from running Ruff. With n = 5 and no pre-registered lint outcome, this is a lead, not a result.

**Open hardening (not done).** The probe records `path_ruff` but does not assert it, and it does not probe `python -m ruff`. Either change alters `lint-tool@1`'s fingerprint. That would make `ruff-lint-02`'s lock stale for a component used only in this test, so it waits for your call (14.7).

### 14.5 Skills for the research loop

`.agents/skills/fault-catalog/` analyses a job into conceptual fault categories (category, owner, mechanism, evidence, pre-registered resolution criteria) and proposes component changes as the next revision, as it did for r3. `.agents/skills/experiment-observations/` reports what a completed experiment observed (per-arm outcomes from the job, verifier records, the Assessment store and `analysis.json`), with fault evidence reconciled through `fault-catalog`. Both are first versions. In this pass the fault catalog's counts were reproduced except FCR-003's exact count, and its evidence map classifies failures by regex. These are known refinement points.

### 14.6 Checks

- `make quality`: exit 0. 1154 passed and 3 skipped, integration 82 passed, coverage 97.08%, lizard clean, every lock current. The spec-rule change refreshed each lock's toolchain section only: every `lock_sha256` is unchanged (forge `015c57e4f59c`, one-shot `b54c3605ced9`, r3 `12f2b2fb05fc`, ruff `cc2a4b147487`, agents `1fef5da2c46a`), and the renamed smoke is `e6e0988b09f9`.
- `make review-surface-test REVIEW_JOBS_ROOT="jobs/pilot-v2/python__forge__guided-pilot jobs/pilot-v2/python__circuit_eval-cp1__guided-pilot" REVIEW_STORE=jobs/pilot-v2/assessment-store`: 55 passed.
- Concurrency: 2 rounds × 3 concurrent runs of `tests/test_component_step_reset.py tests/test_component_lint_tool.py` on OrbStack, all `31 passed`, with no Raidar compose project left behind.

### 14.7 Decisions for you

1. **Run r3?** About $5 and about 8 h on OrbStack (`experiments/ab-test/python__forge__guided-r3/rationale.md` §7). After the run, the fault-catalog runbook reconciles FCR-003 to FCR-006 and FCR-008 against their pre-registered criteria.
2. **Jev spot-check.** Decide stances A and B and the 7 held-back decisions (`docs/reboot/jev-spot-check.md`). Human labels there are the only way to get a non-model reference. Then decide whether to pursue `scb-rubric@3` (fresh labels, new epoch) and `code-quality@1.1`.
3. **FCR-001 (unspecified verifier literals).** Report it upstream to SCB? And should solve-based outcomes get a pre-registered treatment of "wall" cases? Until then the forge primary outcome measures only checkpoints 1-2.
4. **FCR-007 (PyYAML missing in the offline verifier).** Give the verifier dependency parity with native SCB grading. This changes the verifier bundle's environment, not any component.
5. **`lint-tool@1` probe hardening** (14.4). Make it a `lint-tool@2`, or change `@1` in place before anything other than `ruff-lint-02` uses it.
6. **Labelling isolation.** From the next round on, the labeller gets only the blind export, in a directory with nothing else (README Jev row). Model labellers' file access cannot be shown after the fact: the relabel rounds' `selector/selection.json` (which carries Jev scores) sat beside `blind/`, so "the labellers did not read it" rests on their own reports.

### 14.8 Spec-determinacy audit and problem errata (FCR-001; 2026-10-02, commit `140603d`)

This answers decision 14.7.3 on the measurement side. No problem file was changed.
- **Audit.** `make problem-validate` now runs a spec-determinacy audit (`raidar.problems.determinacy`, `variants`, `audit`). Static rules classify each error-text and output-byte assertion against the specifications up to its origin checkpoint. The reference solution is also graded under two spec-conformant variants: neutral error text, and re-serialised JSON. A case is flagged when it breaks on a variant and its assertion is undetermined. The audit never changes the validation verdict, and forge is still `validated`.
- **Forge result.** `scb/forge@38d627ecf668` has 25 flagged cases: 2 at cp1, 6 at cp3, 13 at cp6 and 4 at cp7. They are the fault catalog's 23 plus 2 `duplicate_assignment` cases at cp3. Each passes on the reference and fails on a variant. None is `unconfirmed` or `sensitive_unexplained`. `circuit_eval.checkpoint_1` has none, so it has no errata and its locks did not move.
- **Errata.** `problems/registry/scb/forge/38d627ecf668.errata.yaml` (sha256 `9a6dec369dea`) lists the 25 cases as `exclude-from-solved`, each with a reason and evidence. The lock pins the errata's sha256, and the plan refuses an errata that changed after locking. The verifier records the errata-applied verdict beside the raw grade, and Harbor rewards are unchanged. `correctness@1` adds `solved_errata`, `solved_checkpoints_errata` and `reached_errata`. The analysis reports `solve_rate_errata`, `reached_errata` and `final_solved_errata` beside the raw outcomes. **The raw outcomes stay primary.** No experiment has pre-registered an errata outcome yet.
- **Relocks.** guided-pilot `015c57e4f59c` → `15c1eb4e3a1b`, guided-r3 `12f2b2fb05fc` → `d4e2c9171a28`, ruff-lint `cc2a4b147487` → `f870d2c6ff73` and ruff-lint-r2 `5d4b2f59b74a` → `bb4b7edf5996`. Each execution section differs only by `metadata.errata`. Pilot jobs `forge-02` and `forge-03` adopted the errata through `raidar migrate-job`, and their executed lock is kept under `lock-history/`. `ruff-lint-02` was not migrated, so it is analysed raw under its own `job.lock.json`.
- **Upstream.** A report is drafted at `docs/reboot/upstream/scb-forge-unspecified-assertions.md` but **not filed**. It lists each case with test and spec line references, against test files checked byte-identical to the pinned SCB source.

### 14.9 Verifier dependency parity (FCR-007)

This answers decision 14.7.4.
- **Finding:** the SCB fork's environment config pins no PyYAML. Native grading gets packages from the network (`uv add -r requirements.txt`, then `uv run` syncs `pyproject.toml`).
- **Fix:** the grader image carries a pinned offline `PyYAML==6.0.3` for problems whose SCB configuration lists it (`test_dependencies`). A snapshot sees it only when it declares the package. Grading stays `--network none`. Declared packages Raidar cannot provide are recorded as `unprovided`.
- **Regrade** of the two FCR-007 checkpoint-8 snapshots: b06 guided 0.00 → 0.888 and b07 baseline 0.00 → 0.861. In r3 (14.11), 0 steps hit `ModuleNotFoundError`.

### 14.10 Pilot v2 forge under the errata

Same 16 pairs, guided vs baseline (`jobs/pilot-v2/gate-evidence/errata/`). The raw analysis reproduces `gate-evidence/schema-v2/analysis/forge` exactly: 0 JSON differences and identical markdown.

| Outcome | Raw (primary, unchanged) | Errata-applied (secondary) |
|---|---|---|
| `solve_rate` | 0.156 vs 0.031, Δ −0.125 [−0.1875, −0.0625], **worse** | 0.352 vs 0.445, Δ +0.094 [−0.023, 0.211], **inconclusive** |
| `reached` | 1.25 vs 0.25, **worse** | 2.81 vs 3.56, Δ +0.75 [−0.19, 1.69], **inconclusive** |
| `final_solved` | 0 vs 0 | 0 vs 0 |

**Reading (inference):** with the 25 cases the spec does not determine set aside, the pilot's "guided is worse" result disappears and leans the other way, but it is not significant.

### 14.11 r3 results (`experiments/ab-test/python__forge__guided-r3/`, lock `d4e2c9171a28`)

This answers decision 14.7.1: r3 was run.
- **Execution.** codex + `gpt-6-luna` low. 16 attempts, so 32 trials (b00-b15 × incumbent/candidate). The trials span four jobs: `r3-01`, `r3-04`, `r3-05` and `r3-06`. `r3-05` and `r3-06` sit under `jobs/python__forge__guided-r3/python__forge__guided-r3/`.
  - In `r3-01`, two trials failed on infra (`NonZeroAgentExitCodeError`): b06-incumbent at cp1 and b10-candidate at cp8.
  - Those two were rerun as `r3-05` and `r3-06`. The interrupted b15 pair was rerun as `r3-04`.
  - The analysis lists the 2 failed `r3-01` trials as `replaced_trials`.
  - Interrupted attempts `r3-02` and `r3-03`, and the first b15 try, are under `_aborted/`, and nothing reads them.
  - Each of the 32 trials has exactly one valid record, all on lock `d4e2c9171a28`.
- **Assessment.** The store holds **136 Assessments**: 120 + 8 + 4 + 4 across the four jobs, all added and then replayed identical. That is 128 for the selected trials plus 8 for the 2 replaced `r3-01` trials, which were assessed too. The analysis was reproduced independently: `analysis.json` is equal and `analysis.md` has no diff (`jobs/python__forge__guided-r3/analysis/r3-final/`).
- **Arms:** they share agent, shared components, protocol, scorers and metric. They differ only in condition: the condition hashes are `73cc19…` and `af671b…`, and the lock, problem, agent and protocol hashes are identical.

| Measure | Incumbent (pilot guided) | Candidate (r3) | Δ [95% CI] | Verdict |
|---|---|---|---|---|
| `solve_rate` (primary, raw) | 0.039 | 0.031 | −0.008 [−0.070, 0.055] | inconclusive |
| `reached` (raw) | 0.31 | 0.25 | −0.06 [−0.56, 0.44] | inconclusive |
| `solve_rate_errata` (secondary) | 0.375 | 0.414 | +0.039 [−0.086, 0.164] | inconclusive |
| `reached_errata` (secondary) | 3.00 | 3.31 | +0.31 [−0.69, 1.31] | inconclusive |
| C (case pass rate) | 0.9247 | 0.9270 | +0.002 [−0.006, 0.011] | inconclusive |
| Headline S (`scb-canonical@2`, 15 pairs) | 77.75 | 77.90 | +0.15 [−1.16, 1.54] | inconclusive |
| E erosion component | 0.236 | 0.103 | −0.133 [−0.198, −0.070] | **worse** |
| R rework component | 0.255 | 0.330 | +0.075 [0.004, 0.148] | **improved** |
| H architecture component | 0.9995 | 1.000 | +0.0005 [0.00002, 0.0011] | improved (negligible size) |
| V, G | | | | inconclusive |
| `final_solved` (both views) | 0 | 0 | | inconclusive |

E, R and H are `scb-canonical@2` component scores. Their direction (higher is better) comes from the scorer manifest. The E drop holds at every checkpoint, by −0.10 to −0.18.

**Manipulation check: `not_separated` overall, as predicted** (rationale §6). `plan_written` separates: 0 vs 1.0, p = 5e-05. `tests_run_before_finish` ties: 0.967 vs 0.992, p = 0.11. Raidar's overall verdict needs every default check to separate.

**Fault reconciliation against the pre-registered criteria** (scratchpad `r3/fcr-final.json`):

| Fault | Criterion | Incumbent | Candidate | Met? |
|---|---|---|---|---|
| FCR-008 plan (C1) | `plan_written` ≥ 0.8 cand, ≈ 0 inc | 0 | 1.0; `PLAN.md` touched in 128/128 steps | yes |
| FCR-005 error contract (C2) | `cli_surface_missing` new-at-checkpoint ≤ 50% of incumbent | 34 | 0 | yes |
| FCR-006 propagation (C3) | cp7 regression breaks ≤ incumbent | 6 at cp7 (17 overall) | 0 at cp7 (1 overall, at cp6) | yes |
| FCR-003 test tools (C4) | pytest/ruff not-found ≤ 5% of steps; `tests_run_before_finish` ≥ 0.9 | 80/128 impl steps not found | 0/128; unittest in 128/128; 0.992 | yes |
| FCR-004 consumption (C5) | each candidate component used in ≥ 90% of steps | skill opened 5/128 | `AGENTS.md` 128/128, review stage 128/128 | yes |

- **Unchanged, as designed (no hidden-test knowledge):**
  - FCR-001 wall: 21 cases fail in every trial of both arms.
  - FCR-002: the cp1 wording pair fails in 13/16 trials per arm.
- **Not separately counted:** the candidate's new-at-checkpoint `rejected_valid_input` failures are 81, against 30 for the incumbent.
- **Reading (inference):** every process target was hit, but none of it moved correctness or solve outcomes. The candidate's code scores lower on E and higher on R. Why E dropped is not yet explained. A fault-catalog pass on r3 is the next step.
- **Cost:** mean $0.154 (candidate) vs $0.166 (incumbent) per trial, about $5.1 for the 32 selected trials. Mean agent time was 2,061 s vs 2,556 s.

**Sample size (J2), δ at defaults or configured:**
- **Already enough pairs at 16:** C (7), `solve_rate` (9), `reached` (9), V (1), H (1).
- **Short by 1 pair:** headline S needs 16 pairs, 1 more than the 15 eligible (about $5.1 projected for the full set).
- **Short by more:** `solve_rate_errata` and `reached_errata` need 36 (20 more, about $11.5). E needs 60 (44 more), R 75 (59 more) and G 62 (47 more).

### 14.12 Ruff A/B with `lint-tool@2` (job `ruff-lint-r2-01`, lock `bb4b7edf5996`)

This answers decision 14.7.5: `lint-tool@2` was built, and `@1` is unchanged.
- **`lint-tool@2`.** It reuses @1's install and adds a root-owned `.pth` per image interpreter, so the pinned Ruff 0.8.6 answers as `ruff`, `python -m ruff`, `python3 -m ruff` and `uv run ruff`. Nothing goes into the agent image. Step 1 enforces all four invocations plus the config. Later steps report without failing, so an agent's own actions cannot invalidate a trial.
- **Run.** forge, 5 attempts at concurrency 2: 10/10 trials valid, 0 infra errors, about $0.36.
- **Probes.** In 40/40 r2 step records all four forms answered `ruff 0.8.6`, `path_ruff` was `/usr/local/bin/ruff` and the config resolved to `/app/ruff.toml`.
- **Use (ATIF, 589 exec calls).** Ruff was invoked in **1 of 40 r2 steps**: a bare `ruff check forge.py` at b02 cp5. It worked, and the agent fixed nothing. Every other form was used 0 times, and the baseline arm had 0. r2 agents read `ruff.toml` in 17/40 steps.
- **Verdicts.**
  - Raw `solve_rate`: 0.20 vs 0.05, Δ −0.15 [−0.32, 0.02], inconclusive. As in `ruff-lint-02`, the deficit comes from the two FCR-001 cp1 literals.
  - C: 0.923 vs 0.903, inconclusive. S: insufficient (4 pairs).
  - Manipulation check: `not_separated`.
  - Errata-applied (secondary, not pre-registered): `solve_rate_errata` 0.225 vs 0.475, Δ +0.25 [0.06, 0.44], improved. This is n = 5, so it is a lead.
- **Lint density (exploratory, host Ruff with r2's config on both arms):** r2 is lower at every checkpoint, for example cp8 at 101.2 vs 51.3 per kLOC, mostly from line length and B rules. The only trial that ran Ruff had r2's highest density. **Inference:** the drop comes from reading the config, not from running Ruff.
- **Conclusion:** the tool path is closed. Agents do not run Ruff unprompted. A lint effect would need a condition that names the tool or the check, with a pre-registered lint outcome.
- **Jev:** every residue decision (160) was settled by trace rules, Jev was asked 0 times, and the deterministic answers agree with an independent parse 160/160.

### 14.13 Jev evaluation verdict (`docs/reboot/jev/evaluation-2026-10-02.md`, profiles under `docs/reboot/jev/profiles/`)

Both processes were evaluated against the evaluate-jev skill (R1-R5). The run was read-only, with no Jev calls and no label-store changes.
- **`code-quality.rubric-smells` (`@2` / `scb-rubric@2`): next iteration. Do not promote, and do not refit a threshold on `@2`.**
  - Of the 14 false positives at 0.75: 8 come from undecided policy (stance A, and near-twins labelled oppositely), 3 from question design (the CLI-boundary check) and 3 from Jev itself.
  - Three criteria drive 1,020 of the 1,122 decisions flagged at 0.75. 26 of 45 criteria never reach 0.5. 80% of ΣP mass comes from answers below 0.5.
  - Recall is unmeasured, because the label pool was enriched towards Jev positives.
- **`process-compliance.residue` (`@1`): keep the test check with band routing, and redesign the plan check.**
  - The plan check fails 0/3, because a generic "inspect, then implement" preamble passes as a plan.
  - The deterministic router never sees an `edit` event, because Codex edits arrive as exec calls containing patch text.
  - Recorded input sufficiency is 0/17, because any clipped event marks the case partial. That alone fails the promotion gate.
- **Operational health:** coverage 17,248/17,248, $2.32 for 15,499 calls, p50 latency 298 ms.
- **Recommended next step before new agent trials:** a Jev replay over the existing pilot v2 snapshots and traces under new versions (`scb-rubric@3` / policy 3, `process-residue@2`), at about $2.3. It is preceded by the user-written policy (14.15) and followed by per-criterion blind labels.

### 14.14 First-pass labels (`docs/reboot/jev/labels/firstpass-2026-10-04.{jsonl,md}`)

There are 68 agent labels (`claude-opus-5-5[1m]`): 51 rubric decisions and 17 residue states, all from `jobs/pilot-v2/assessment-store`. They are a proposal and **were not written to the label store**. The store still has 71 records (38 current), and nothing from 2026-10-04 is in it.
- **What was labelled:** the 7 held-back decisions, the 12 store false positives, 6 contested-stance cases, 22 seeded-random fresh decisions, 4 seeded stance-B decisions and the 17 residue states.
- **Blindness:**
  - Only the 26 fresh decisions are blind to Jev's answers.
  - The 25 evaluation cases and the 17 residue states were labelled after reading the evaluation doc, so they are informed relabels.
  - Stances were written at 01:42:14Z, labels at 01:50:33Z, and Jev's answers were first opened at 01:51:06Z (self-recorded). The blind exports carry no answer or score fields.
- **Positions applied (proposals for the user to confirm):**
  - Stance A is rejected with a boundary rule: argv and spec wire values are clean, and internal kind or mode literals that drive domain branching are a smell.
  - Stance B is rejected for the program's own storage on a write path.
  - Residue readings P1-P3: every session counts; a suite that runs no tests does not count; a generic preamble is not a plan.

| View | Threshold | TP | FP | FN | TN | Precision |
|---|---|---|---|---|---|---|
| Rubric, adopted labels | 0.75 | 6 | 16 | 1 | 28 | 0.27 |
| Rubric, state-only labels (1 uncertain excluded) | 0.75 | 6 | 15 | 1 | 28 | 0.29 |
| Rubric, literal stances A and B | 0.75 | 14 | 8 | 1 | 28 | 0.64 |
| Residue (`complied` positive) | 0.5 | 7 | 4 | 0 | 6 | 0.64 |

- **Literal-view caveat:** `literal_stance_label` is set only for multi-branch A1 dispatch (C25, C28, C39, C40), C44 and the stance-B rows. It is not set for the single argv checks C08, C16 and C18. Setting it there would give 14/25.
- **Rubric false negative:** C19 at 0.63, a smell this labeller found by reading the snapshot.
- **Residue at 0.75:** R09 and R14 become false negatives.
- **Fresh blind sample, 22 random decisions:**
  - 1 smell: F01, a TP.
  - 1 FP at 0.75: F09.
  - F17 and F22 are FPs only at 0.5.
  - **Inference:** on unenriched decisions, flags are rare and about half are wrong. That is consistent with the evaluation.
- **Stance-B sample:** 3 of 4 (B2-B4) are FPs under the B1 rule.
- **Per-question agreement at 0.5:** 269 of 294 rubric questions and 13 of 17 residue questions (282 of 311 combined).
- **Disagreements with earlier labels,** all from the same model family, are listed in the method note and not resolved: C07, C15, C17, C44 and C19 are smells here, and C25, C28, C33 and C39 are clean here.

### 14.15 Decisions for you (supersedes 14.7)

**Status of 14.7:**
- Done: 14.7.1 (r3 was run, 14.11), 14.7.4 (FCR-007 fixed, 14.9) and 14.7.5 (`lint-tool@2`, 14.12).
- Partly done: 14.7.3. The errata and the audit exist, but the upstream report is not filed.
- Open: 14.7.2 and 14.7.6, which carry over below.

**Open decisions:**
1. **Jev policy.** Decide stances A and B and residue rules P1-P3. You can adopt the first-pass positions in 14.14, amend them or replace them. The `@3` / `process-residue@2` replay waits on this.
2. **First-pass labels.** Import none, some (for example, only the 26 blind fresh rows) or all into the label store under a new split. Or keep them as a reference only. They are model-made and partly informed, and the store is unchanged until you decide.
3. **FCR-001 upstream.** File `docs/reboot/upstream/scb-forge-unspecified-assertions.md` with SCB?
4. **Errata as an outcome.** Pre-register `solve_rate_errata` (or make it primary) in the next forge lock? Raw `solve_rate` is capped by the 21-case wall. In pilot v2, r3 and ruff-lint-r2 it measures mostly the two cp1 literals.
5. **r3 follow-up.**
   - Accept r3's process changes as the new guided incumbent? They met every pre-registered target, with neutral outcomes and E worse.
   - Run the fault-catalog pass on r3 first, to explain the E drop and the rise in `rejected_valid_input` failures.
   - Headline S needs 1 more pair. Errata outcomes need 20 more (about $11.5).
6. **Lint.** Drop the ruff line, or design a condition that names the check and pre-register a lint outcome.
7. **Labelling isolation** (carried over from 14.7.6).
