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
