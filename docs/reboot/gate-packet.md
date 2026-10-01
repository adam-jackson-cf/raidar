# Phase 1 gate packet (pause-and-review)

- **Date:** 2026-09-30
- **Plan reference:** `docs/reboot/implementation-plan.md` §3 (1.P pilot and the Phase 1 exit criteria) and §4 (this packet and the gate decisions).
- **Branch:** `reboot-impl`. The wave 1-4 work is committed up to `6b8e44d`. The pilot's infra fixes, this packet, **and the WS-L scenario-era cutover** (251 tracked files, about −47k lines, including scorer package files) are committed together on top of it (see §9). The WS-L cutover landed after the pilot ran and after the pilot store and locks were built; its consequences for the pilot evidence are in §1.1.
- **AgentSpec substitution (user directive):** plan 1.P names Claude Code as the pilot's single AgentSpec. By user directive (ADR-001 §2.7), every real-LLM trial in this pilot used **codex-cli 0.156.1 + `gpt-6-luna`, reasoning effort low**. No Claude Code trial has run, so the Claude mappings of the built-in components are unit-tested only.
- **Jev:** `OPENROUTER_API_KEY` is not set. Jev ran in shadow with replay and fixtures only; every live call is reported below as blocked.

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
| 6 | N1 budgets hold (`raidar perf check`) | **Not met** | forge-04: 10 of 12 phases pass. `artifact_collection` p95 was 3.97 s against a 3.5 s budget, and `teardown_result_write[init]` p95 was 4.62 s against 4 s. one-shot: every phase is `insufficient` (6 trajectories, fewer than the 10-sample minimum), but two point estimates are already over budget: `per_step_reset` 3.28 s against 2 s and `artifact_collection` 3.54 s against 3.5 s. The no-LLM `make perf-fixture` rerun also fails `artifact_collection` (p95 3.65 s against 3.5 s) and passes teardown (3.38 s). See §5. **Update (§5.1):** all three misses are fixed in code and every budget passes on the no-LLM fixture at concurrency 1 and 3; the criterion stays Not met until one real-agent leg is re-run on the fixed tree. |
| 7 | Frozen review-surface suite green; Quality element live | **Met** | `make review-surface-test REVIEW_RUNS_ROOT="runs/pilot/pilot-forge runs/pilot/pilot-circuit-eval-oneshot" REVIEW_STORE=runs/pilot/assessment-store-v2`: **55 passed**. At the cutover commit the one store now serves both roots, so the suite's Quality data holds 58 runs (42 synthetic + the 16 pilot runs); before that fix the target silently left the pilot runs out of the suite's Quality data when the root and store counts differed, and it now fails loudly on any other mismatch (`lock-fix/review-surface-test.log`; the suite grew from 40 while the review surface was being integrated in the same worktree, see §9), with 67 runs across 19 experiments in the review projection. The 16 pilot runs resolve their migrated locks and their Assessments; 14 are scored and the 2 baseline trajectories without code-quality values (`forge-04` `b00`, `b03`) are Unscored with per-component `ineligible` reasons, which the observations (unchanged since the pilot) determine. The Quality projection over the two pilot runs with the canonical store (`lock-fix/qp-store-v2.json`) gives 16 runs across 2 experiments, 662 ok cells and no "lock unavailable" note, the same counts as the pilot-time projection. The earlier screenshots (`quality*.png`) predate this rerun. |
| 8 | The analysis gives an honest verdict (`inconclusive` is acceptable) | **Met** | forge primary `solve_rate`: Δ 0, 95% CI [−0.22, 0.22], **inconclusive**. one-shot primary: **insufficient** (3 pairs, below the lock's minimum of 5). No override was used; the thresholds come from the locks (`pinned_by_lock=true`, min_pairs 5, alpha 0.05, max_exclusion_fraction 0.2). Reproduced on the current tree from the canonical store (`lock-fix/analysis-{forge,oneshot}`): the only field that differs from the pilot-time reports is `lock_sha256`, which is now the execution digest of the migrated lock (§1.1). |

> **Pilot v2 (2026-10-01):** this table is the v1 record. The criteria re-evaluated on pilot v2 data are in §11.10, and the end-to-end process verdict is in §11.11.

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

**Next action** (the Phase 2 entry condition proposed in §8):
- Re-run one real-agent leg on the fixed tree at pilot concurrency, then `make perf-check` it. The forge leg has 80 per-step samples; the one-shot leg needs at least 10 trajectories for any verdict.
- If `teardown_result_write[init]` still exceeds 4 s there, it is pure `compose down` under host load. The options would then be to cap trial concurrency for `perf check` runs, or for the budget owner to re-baseline teardown for concurrency at 5 s. This is not applied.

---

## 6. Jev shadow audit

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
   - Next step: set `OPENROUTER_API_KEY`, rescore (the provisional Assessments re-ask automatically), then label decisions in the review surface and export them with `make jev-export-labels`.

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
| forge | forge-02 | 2 × 16 | 14 / 18 (b00-b06 valid in both arms; b07-b15 infra in both arms) | 11:27-16:56 (5 h 30 m), stopped by host failure | 3 |
| one-shot | oneshot-01 | 2 × 5 | 10 / 0 | 10:24-10:49 (24 m) | 2 (concurrently with forge-01) |

- **One Raidar bug found and fixed (`c068e4c`).** The offline grader (`--network none`, `UV_OFFLINE=1`) ran the SCB entrypoint `uv run --project {app} forge.py`, and uv tried to sync the submission's `dev` dependency group before starting the program, so correct work scored 0 (forge-01 guided b00: 0.0 on all 8 checkpoints). The sandbox now sets `UV_NO_SYNC=1`. A regression test runs real uv offline with and without a lockfile. `make assess-regrade` on the aborted trial then gave 1.0, 1.0, 0.94, 0.96, 0.96, 0.91, 0.90, 0.89, and regrading all 10 one-shot trials matched their recorded grades, so oneshot-01 stands. Only the scorer implementation digests moved; `lock_sha256` is unchanged for both legs.
- **Host failure, not a Raidar bug.** At 16:56:24 UTC the external drive holding OrbStack's data (`/Volumes/UGreen-External/Docker`) returned I/O errors, the VM's btrfs volume went read-only and OrbStack stopped (`~/.orbstack/log/vmgr.log`). Every forge trial in flight or queued (b07-b15, both arms) was recorded as infra-invalid. The drive dropped off and remounted at 17:56 BST (macOS reports it mounted read-write, SMART Verified). OrbStack was restarted at 18:18 BST: Docker, signoz and a test container came up with no new btrfs or I/O errors in `vmgr.log`, and `make harbor-cleanup` removed the 3 trial environments forge-02 left behind (§11.12).
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
| 1 | All pilot trajectories complete with ≤1 infra exclusion per arm | Met | **Not met (forge); met (one-shot)** | one-shot 5+5 valid, 0 infra. forge 7+7 valid, 9+9 infra, all from the OrbStack drive failure (§11.1). Needs forge b07-b15 rerun after OrbStack is healthy. |
| 2 | Context-isolation and no-test-leak checks green on every trajectory | Met | **Met** | 122/122 executed steps on valid trajectories (112 forge + 10 one-shot) have a clean `raidar-reset.json` (no error, no residual, no failed reset) and `pre-agent-leak.json` `clean: true`. `no_oracle_leak` passes on 24/24 valid trajectories. |
| 3 | Every component's discovery probe passes | Met (codex-cli) | **Met (codex-cli)** | `component_probes` passes on 24/24 valid trajectories. Offline `probe_step` re-run: context-reset and prompt-wrapper@1 56/56 forge baseline steps; context-reset, prompt-wrapper@1, rules-file@1, skills@1, lint-config@1, stop-hook@1 and review-loop@2 56/56 forge guided steps; 5/5 for every component in both one-shot arms. Same caveats as v1 (Claude Code not run; lint-config presence-only; stop-hook fired-only under Codex). |
| 4 | Offline rescoring reproduces deterministic observations byte-identically | Met | **Met** | §11.2: 108/108 Assessment files byte-identical across two fresh stores; 108/108 `replayed_identical`. |
| 5 | G2 golden parity against the SCB fork passes | Met | **Met** | Unchanged code path; part of `make quality`, which passes on this tree. |
| 6 | N1 budgets hold (`raidar perf check`) | Not met | **Not met** | §11.8: `teardown_result_write[init]` 4.66 s (forge) and 4.45 s (one-shot) against 4 s; `environment_start[warm]` 20.77 s (forge) against 20 s. The v1 `artifact_collection` miss is fixed on real agents. |
| 7 | Frozen review-surface suite green; Quality element live | Met | **Met** | §11.9: 55 passed with the pilot-v2 runs and store; run-detail Quality panel and `/quality` render pilot-v2 data (screenshots). |
| 8 | The analysis gives an honest verdict (`inconclusive` is acceptable) | Met | **Met** | one-shot primary **inconclusive** (5 pairs). forge primary **insufficient** because the exclusion share exceeds the lock's 20%; the analysis refuses to call an effect from a crash-truncated leg, which is the intended behaviour. No overrides; thresholds pinned by the locks. |

**Summary:** 6 of 8 met on v2. Criterion 1 fails on forge only, from a host hardware failure; criterion 6 fails on teardown tail latency (both legs) and one forge env-start pair.

### 11.11 E2E local process confirmed?

**Verdict: yes for the process; no for the forge experiment's data.** Every stage ran on real Luna trials through public make targets, and each stage consumed the previous stage's output without manual repair:
- the one-shot leg is a complete, clean end-to-end pass (10/10 valid, assessed, rescored byte-identically, analysed to an `inconclusive` verdict, rendered in the review surface);
- the forge leg exercised every stage on 14 valid multi-checkpoint trajectories, but the experiment is incomplete (criterion 1) because the host's Docker drive failed mid-run;
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
- Rerun forge blocks b07-b15 (or the full leg as a new run id) and repeat steps 5-7. OrbStack is running again and forge-02's leftover environments are cleaned up (§11.12). Before rerunning only b07-b15 into a second run, confirm how `raidar analyze` pairs a block that is infra-invalid in one run and valid in another; this has not been checked.
- Decide N1 (§5.1 options): cap trial concurrency for `perf check` runs, or have the budget owner re-baseline `teardown_result_write[init]` (and possibly `environment_start[warm]`) for concurrent operation. Nothing is re-baselined here.
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
