# python__forge__guardrails: rationale and pre-registration

- **Status:** validated and locked (`experiment.lock.json`). Smoke run (`smoke-01`, section 9); **main job not yet run** (`main-01` was aborted, section 9). This file was written before any job of this experiment. Sections 3 to 6 are the pre-registration. Later additions go in sections 9 and 10 and never edit sections 3 to 6. **Section 10 (explore iteration 2, 2026-10-04) amends the design before `main-02`:** r1 gets a shadow gate, the lock is `28c51440f74a`.
- **Objective:** explore (AGENTS.md "Objectives"). The process is still being iterated, so the run is small: 4 attempts per arm (user decision, 2026-10-04). Results are exploratory signals, never cited as evidence of an effect.
- **Question:** on `scb/forge@38d627ecf668`, does a repository that refuses a commit until a mechanical Python guardrail gate passes change correctness and code quality, against the shared framing alone?
- **Replaces** `python__forge__ruff-lint` and `python__forge__ruff-lint-r2`. Those experiments showed the provisioned Ruff was available but almost never run unprompted (1 of 40 r2 steps). Their definitions were removed on 2026-10-04; their jobs stay on disk under `jobs/`.

## 1. Design

| | Baseline (`r1-baseline`) | Treatment (`r2-guardrails`) |
|---|---|---|
| Shared | `prompt-wrapper@1` starter framing (byte-identical to the pilot's) | same |
| Workspace at step 1 | empty | `ruff.toml` and `mypy.ini`, byte-identical copies of the protected configs |
| Tooling | none beyond the agent image | `python-guardrails@1`: `guardrail-check` with Ruff 0.8.6 and mypy 2.3.1 (hash-pinned), bespoke checks and protected configs, installed root-owned in `/opt/raidar-tools/python-guardrails` |
| Commit gate | none | `guardrail-gate@1`: after every session, `git commit` with `guardrail-check` as the pre-commit hook. A refusal starts a fix session with the gate's output, at most 3 (`max_rounds: 3`, `timeout_sec: 300`) |

These are fixed in both arms:
- **Problem:** forge, 8 checkpoints. The lock pins the forge errata (sha256 `9a6dec369dea`, 25 `exclude-from-solved` cases).
- **Agent:** `codex` 0.156.1 with `gpt-6-luna` at effort low.
- **Protocol:** `continue`, 1800 s agent timeout, 900 s verifier timeout, artifact snapshots.
- **Scorers:** `correctness@1`, `code-quality@1`, `resource-usage@1`, `process-compliance@2`, `gate-effort@1`; headline metric `scb-canonical@2`.
- **Analysis:** seed 20261004, concurrency 2, default `min_pairs` 5, max exclusion 0.2, alpha 0.05.

**The agent is not told about the gate** (user decision, 2026-10-04). No rules text mentions it. The agent sees `ruff.toml` and `mypy.ini` as in any repository, with no comments that hint at a tool, a scorer or an experiment, and learns of the gate only from a refusal.

**Fix sessions are billed to r2.** Every session's tokens and cost are summed into the step. Fix sessions run inside the step's agent phase, so they share its 1800 s agent timeout.

## 2. The treatment: python-guardrails@1

- **Rule contract.** All 92 Enaible rule IDs are resolved in `orchestrator/tests/fixtures/python_guardrails/coverage-ledger.json`. The user approved this at step 5 of the guardrails setup on 2026-10-04:
  - 73 rules are implemented, each with a passing and a violating canary.
  - 16 rules are not applicable, each approved with evidence in `not-applicable.md` beside the ledger.
  - 3 rules are not enforced in `@1`. `PRETTIER.version` and `SECURITY.dependency-audit` are deferred. `TEST.coverage-minimum` (with pytest) is **planned for `python-guardrails@2`**, which will provision pytest and coverage. It is not dropped.
- **Tiers** (`config/policy.toml`):
  - **block:** every code not listed below, including every mypy strict error code. This is subject to the smoke rule in section 6.
  - **warn:** `D`, `PT`, `PYQ102` and `PARITY.lock-version`.
  - **off:** `PT009` and `PT027`. The image has no pytest; unittest is its runner.
- **Approved adaptations:**
  - check-only (`fix = false`), with `guardrail-check --fix` as the explicit opt-in;
  - concise output;
  - `respect-gitignore = false`;
  - the snapshot's excludes;
  - mypy on production files only;
  - protected config copies: the gate runs on the protected configs, so editing a workspace copy cannot loosen it.
- **Tamper detection** (`step_flags`). A step's `probes.flags` record each of the following without failing the step or the trial:
  - an edited or deleted workspace config copy, seen at step start or at any gate run;
  - a protected config or tool tree that changed after step 1;
  - suppression comments that the gate refused;
  - trace evidence of trying to get round the hook: `--no-verify`, `commit -n`, `core.hooksPath`, `.git/hooks`, the gate's hook directory, writes to the tool tree, or shadowing `guardrail-check`.

## 3. Primary outcome (pre-registered)

**`solve_rate_errata`**: the per-checkpoint solve rate with the pinned errata applied. The raw `solve_rate` is always reported beside it with its own verdict.

Why errata-applied is primary: raw `solve_rate` on forge is capped by 21 cases whose literals the specification does not determine (FCR-001). In pilot v2, r3 and ruff-lint-r2 it measured mostly the two checkpoint-1 literals (gate packet §14.15, decision 4). With 4 attempts and `min_pairs` 5, the verdict is expected to read `insufficient` by design. The effect direction, its CI and the J2 sample-size projection are reported. The projection sizes any later confirm run.

## 4. Secondary outcomes (pre-registered)

- **Correctness:** C (case pass rate), `reached` and `reached_errata`, `final_solved` and `final_solved_errata`.
- **Quality and headline:**
  - headline S (`scb-canonical@2`);
  - its components H (`architecture`), R (`rework`) and G (`regression`).
- **Overlap by construction: E (`erosion`) and V (`verbosity`).**
  - The gate's C901 limit of 10 is E's cc > 10 threshold.
  - The gate's `SIM` selector includes all 9 SIM rules that V counts.
  - E and V therefore move by construction in r2. They are reported and pre-registered as manipulation evidence, not as independent quality outcomes.
- **Cost:** $ per trial, tokens, and agent seconds per step, with fix sessions included in r2.

## 5. Gate effort, manipulation check and integrity (pre-registered)

Gate effort is a first-class quality signal. It is read deterministically by `gate-effort@1` from each r2 step's `guardrail-gate/guardrail-gate.json` and from the gate's JSON reports (`python-guardrails.gate-report/1` in each run's report dir).

| Outcome | Definition (`gate-effort@1`) |
|---|---|
| Rounds to clean | `rounds_to_clean` per clean step; `mean_rounds_to_clean` per trial |
| First-pass clean rate | `first_pass_clean`: the first gate run, after the work session, passed; `first_pass_clean_rate` per trial |
| Unresolved rate | `unresolved`: every fix round was used and the gate still refused (rounds exhausted); `unresolved_rate` |
| Failures per round by rule category | `first_run_blocking` and `blocking_findings`, per family: complexity, typing, defects, structure, style, naming, tests, suppressions, other |
| Fix-session cost | `fix_cost_usd`, `fix_input_tokens`, `fix_output_tokens`, `fix_wall_s`; gate time `gate_wall_s` |
| Validity | `gate_error_rate`: expected to be 0. A gate error is infrastructure, never a fix round |

- **Comparing arms.** r1 has no gate, so in this design the gate-effort outcomes describe r2 alone. Paired between-arm comparisons with CIs apply when both arms are gated: a later variant arm, or a benchmark across agents under this condition. r1's code is not gated, so the share of r1 checkpoints a gate would have refused is not measured here (section 8).
- **Manipulation check:** `gate_clean_at_step_end` (`analysis.manipulation_checks`, `min_share: 0.8`). At least 80% of r2's decided checkpoints must end with the gate passing. r1 has no decided checkpoints for it, so r2's share alone decides it (`basis: threshold`).
- **Integrity:** the number of r2 steps carrying each flag kind, from `probes.flags`: `workspace-config-modified`, `protected-config-modified`, `protected-tree-writable`, `suppression-refused` and `hook-bypass-attempt`.
  - These are observations.
  - A flag never excludes a trial.
  - A `protected-*` flag after step 1 would be an infrastructure defect: stop and fix it before any further job.

## 6. Smoke rule (pre-registered, decided before the main job)

> Before the main job, run 2 r2 trials. If guardrail-gate `rounds_exhausted` occurs in ≥ 50% of r2 steps AND mypy strict annotation codes (`no-untyped-def`, `no-untyped-call`, `type-arg`, `var-annotated`, `no-any-return`) are the majority of the final-round blocking findings in those steps, move those codes to WARN in python-guardrails@1's policy before locking the main job; otherwise keep BLOCK.

How it is computed, deterministically, from the two smoke jobs:
1. **r2 steps:** every step result of the 2 smoke trials that has a gate record (`guardrail-gate/guardrail-gate.json`).
2. **Rounds-exhausted share:** steps whose `termination_reason` is `rounds_exhausted` (`gate-effort@1` `unresolved`), divided by the r2 steps. The first condition holds when the share is ≥ 0.5.
3. **Annotation share:** for each rounds-exhausted step, take the last gate run's report and its `block`-tier findings. Pool them over those steps. The second condition holds when more than half of the pooled findings have a code in {`MYPY.no-untyped-def`, `MYPY.no-untyped-call`, `MYPY.type-arg`, `MYPY.var-annotated`, `MYPY.no-any-return`}.
4. **If both hold:** add exactly those five codes to `warn` in `orchestrator/src/raidar/components/payloads/python_guardrails_1/config/policy.toml`. That is the only change. Then relock (the component digest moves, and so does `lock_sha256`). **Otherwise:** nothing changes and the main job runs under the current lock.
5. **Record the outcome** in section 9 before the main job starts: both shares, the step counts and the decision.

**Status of the smoke jobs.** They are calibration jobs. They are not part of the main analysis and are never pooled with it. `python-guardrails@1` counts as released from the main job on. The policy edit allowed in step 4 is the only change to `@1` permitted after a smoke job. Any other change is `python-guardrails@2`.

**When the rule is not evaluated.** If a smoke trial is infrastructure-invalid (a failed probe, a `gate_error`, or a `protected-*` flag), the rule is not evaluated. Fix the defect and rerun the smoke, as the explore rules require.

## 7. Expectations and how to read the result (stated before data)

- **The gate will fire in almost every step.** The dry run found new blocking findings in 117 of 120 step snapshots from earlier jobs. Every forge reference solution (checkpoints 1-8) and all 67 sampled agent final snapshots would be refused.
- **mypy strict is the main cost driver.** Untyped arms add 13-16 new mypy findings per step. `guardrail-check --fix` clears almost all formatting, import-order and pyupgrade findings. Complexity, B/SIM and typing findings need rework.
- **Cost (estimate).** r1 costs about $0.035 per trial (ruff-lint jobs). r2 is estimated at about 2-4× that because of fix sessions. The 4-attempt job is about $0.5 to $1, and the smoke about $0.2 to $0.3.
- **Possible correctness effects.** Rework under the gate may cost time or correctness. The reference author's own code fails the gate (24 substantive findings at checkpoint 8). That is part of what is measured.
- **Verdicts:** `insufficient` (pairs < 5) for paired outcomes. Read the direction, the CIs, gate effort and cost as explore signals.

## 8. Known limits

- **No pre-commit and no CI.** The gate is the only enforcement point; the pack has no separate CI to keep in parity.
- **Rules not enforced in `@1`.** `TEST.coverage-minimum`, pytest style, Prettier and the dependency audit are not enforced (section 2).
- **E and V overlap the treatment by construction** (section 4).
- **r1 is not gated.** "Would r1 have passed the gate" needs an offline shadow run of `guardrail-check` on r1 snapshots. That is not built; when it exists it is a new scorer version, and adding it relocks the assessment section only.
- **One treatment bundles several changes.** The pack, the commit hook and the fix loop ship together. A rules-text rung (telling the agent about the gate) or a WARN-only policy would be later arms in this experiment.

## 9. Running it, and outcomes appended later

Run on OrbStack (`orbstack-data-status` must report `external_available=True`) through the public targets:

```sh
make experiment-lock EXPERIMENT=experiments/ab-test/python__forge__guardrails/experiment.yaml LOCK_CHECK=1
# Smoke (section 6): two r2 trials, one job each.
make experiment-execute EXPERIMENT=experiments/ab-test/python__forge__guardrails/experiment.yaml \
  ARM=r2-guardrails ATTEMPT=0 JOB_ID=guardrails-smoke-01
make experiment-execute EXPERIMENT=experiments/ab-test/python__forge__guardrails/experiment.yaml \
  ARM=r2-guardrails ATTEMPT=1 JOB_ID=guardrails-smoke-02
# Apply the smoke rule, record it below, relock if it fired; then the main job.
make experiment-execute EXPERIMENT=experiments/ab-test/python__forge__guardrails/experiment.yaml \
  CONCURRENCY=2 JOB_ID=guardrails-01
make experiment-assess JOB=jobs/python__forge__guardrails/guardrails-01 STORE=stores/python__forge__guardrails \
  SCORERS="correctness@1 code-quality@1 resource-usage@1 process-compliance@2 gate-effort@1"
make experiment-analyze JOBS="jobs/python__forge__guardrails/guardrails-01" \
  STORE=stores/python__forge__guardrails ANALYSIS_OUT=analysis/python__forge__guardrails
```

### Smoke rule outcome

Recorded 2026-10-04, before the main job. The smoke ran as one job, `jobs/python__forge__guardrails/smoke-01` (`ARM=r2-guardrails ATTEMPT="0 1" CONCURRENCY=2`), not the two jobs shown above; the trials and the rule computation are the same.

- Rule evaluable: both trials valid, every component probe passed, 0 `gate_error`, no `protected-*` flags.
- r2 steps with a gate record: 16 (2 trials x 8 checkpoints).
- Rounds-exhausted share: 0 of 16 (0.0); condition 1 (>= 0.5) does not hold.
- Annotation share: 0 final-round blocking findings pooled (0.0); condition 2 does not hold.
- Decision: keep the mypy strict annotation codes at BLOCK. No policy change; the main job runs under lock `20f0573fc970`.
- Observed beside the rule: every step ended `gate_clean` after exactly 1 fix round (first-pass clean 0 of 16). Typing (`MYPY.*`) findings appeared in 7 of 16 first gate runs (b00 checkpoints 1, 2, 5, 6, 7 and 8; b01 checkpoint 8); complexity and style dominated, unlike the section-7 expectation.

Smoke cost: $0.159 in total, of which fix sessions were $0.0853. One gate run took 0.64-1.04 s (1.3-2.0 s per step across its two runs).

### Main job status

- **`main-01` did not run.** It was launched on 2026-10-04 at 13:14:05 UTC (both arms, attempts 0-3, `CONCURRENCY=2`). Its `make` process was terminated (SIGTERM, exit 143) about 90 s later, before any trial finished: 0 trial records, no `finished_at`. It contributes no data and is never analysed or pooled.
- Its two started trials (`b00-p0-r1-baseline`, `b00-p0-r2-guardrails`) left idle containers on OrbStack. The rerun waits for the user's decision on removing them, because Harbor's start of the same trial (same compose project) runs `down --remove-orphans` on them.
- **Rerun plan:** a fresh job id, `main-02`, under the unchanged lock `20f0573fc970`, from a detached process under `caffeinate` so that it survives the session. `main-01` stays on disk as an aborted job.

### Reading the analysis: primary outcome label

`make experiment-analyze` labels raw `solve_rate` as "Primary outcome" for `continue` progression (`outcomes.py` `primary_metric`); the label is not configurable in this lock. **The pre-registered primary is `solve_rate_errata` (section 3)**, read from its row in the Effects table. The results write-up states this explicitly; the lock is not changed to move the label.

### Deferred to python-guardrails@2

`TEST.coverage-minimum` and the pytest style rules are not dropped: python-guardrails@2 will provision pytest and coverage in the image and enforce them (user decision 2026-10-04). PT009/PT027 stay off in @1 because the @1 image is unittest-only.

## 10. Amendment: explore iteration 2 (2026-10-04, pre-registered before `main-02`)

Written and locked before any job under the new lock. No job has run under it; `smoke-01` and the aborted `main-01` stay under the old lock `20f0573fc970` and are never pooled with jobs under the new one. The objective stays **explore**. Sections 3 to 6 are unchanged except where this section says it supersedes a statement.

### 10.1 What changed in the design

| | r1-baseline (was) | r1-baseline (now) | r2-guardrails (now) |
|---|---|---|---|
| Pack | none | `python-guardrails@2`, `workspace_configs: false` | `python-guardrails@2`, `workspace_configs: true` |
| Workspace at step 1 | empty | empty: no `ruff.toml`, no `mypy.ini` | the two config copies (as before) |
| Gate | none | `guardrail-gate@2`, `mode: shadow` | `guardrail-gate@2`, `mode: commit`, 3 fix rounds, 300 s (as before) |
| Fix sessions | none | none | as before |

- **`python-guardrails@2`** installs the `@1` payload byte for byte (same gate, pins, protected configs, tiers and policy). `workspace_configs: true` is `@1` exactly; `false` puts nothing in `/app`. The smoke-rule decision of section 9 (annotation codes stay BLOCK) therefore carries over unchanged.
- **`guardrail-gate@2`** is `@1`'s loop with two additions. **Shadow mode:** the same commit check (`git add -A`, `git commit` with `guardrail-check` as the pre-commit hook) runs once at each step's end, against a git directory outside `/app` (`/tmp/raidar-review/guardrail-gate/shadow/.git`, reset each step) with `/app` as its work tree. The gate run is recorded in full, but it never refuses, never starts a fix session and never writes `/app`. **Commit evidence:** every commit attempt, in both modes, records `HEAD` before and after, `git log --oneline` and `git status --porcelain` in the gate run's `git` block. The step's `git_evidence` adds every hook exit code and every `git commit` exit code. All of it is under `/logs/agent/guardrail-gate/`, outside `/app`. A refusal is verifiable from git itself: `HEAD` does not move and the staged paths are still in the status.
- **r1's agent experience is unchanged.** It gets the same instruction and the same single work session, with nothing in `/app` before or after the gate. A no-LLM trial in the pack image proves this: the agent's `ls -A` of `/app` at both steps contains no `.git`, `ruff.toml` or `mypy.ini`, and no fix session starts (`test_the_shadow_baseline_records_the_gate_unseen_in_a_real_trial`). Three differences remain:
  - the pack is installed root-owned under `/opt/raidar-tools/python-guardrails` and linked at `/usr/local/bin/guardrail-check`, so it is visible only by exploring system paths, as in r2;
  - the agent is wrapped, so its step trajectory is a root trajectory embedding its one work session;
  - each step's agent phase gains the shadow gate's run time (1-2 s at smoke).
- **Factor guard:** the arms still differ only in `condition`.

### 10.2 Outcomes (supersedes the "Comparing arms" bullet of section 5)

- **Gate-effort scorer:** `gate-effort@2` replaces `gate-effort@1`. It keeps the same metrics and reads enforced records exactly as `@1`. For a shadow step it measures the following, and nothing else:
  - `gate_runs`;
  - `first_pass_clean`;
  - `gate_error`;
  - `first_run_blocking`, in total and per rule family.

  Every fix and enforcement outcome of a shadow step is `not_applicable`, never 0. That covers `fix_rounds`, `gate_clean_at_step_end`, `unresolved`, `rounds_to_clean`, `blocking_findings` over every run, `fix_*` and `gate_wall_s`, together with their trajectory rates and totals.
- **Paired gate outcomes (secondary, pre-registered):** `gate.first_pass_clean_rate`, `gate.gate_error_rate` and `gate.total_first_run_blocking` (total and per family) are now measured in both arms and paired by attempt. With 4 pairs the verdicts read `insufficient` by design; the direction and CIs are the signals. **What they compare:** r1's value is how its unconstrained code would have fared at each step's end. r2's is the first commit attempt after each work session, made with the configs visible and after the refusals of earlier steps. The difference is therefore the treatment's effect on first-pass compliance, learning across steps included, not a pure "same code" comparison.
- **Unchanged:** fix rounds, unresolved, rounds to clean and fix-session cost still describe r2 alone.
- **Manipulation check (unchanged):** `gate_clean_at_step_end` with `min_share` 0.8. r1's shadow gate never decides it, so r2's share alone decides it (`basis: threshold`), as before.
- **Integrity flags in r1:** the protection flags only (`protected-config-modified`, `protected-tree-writable`). Config-copy, suppression and hook-bypass flags describe a gate the agent sees, so they do not apply to r1.
- **Primary outcome label (supersedes "Reading the analysis: primary outcome label" in section 9):** the lock now carries `analysis.primary_outcome: solve_rate_errata`. The analysis report, its markdown and the review surface label it as the primary, and raw `solve_rate` is reported beside it with its own verdict. This is the pre-registered primary of section 3, unchanged.

### 10.3 Rule verification is Raidar's own

The rule set was derived from the user's `setup-repository-guardrails` skill on 2026-10-04. The skill was used once, to set the initial verification; it is not a continuous reference. Its vendored files were removed from the repository on 2026-10-04:
- the validator;
- the Enaible rule catalog;
- the coverage ledger;
- the not-applicable evidence.

They stay in git history at `f61d81b`. Section 2's citation of the ledger is historical. The pack's verification is now its own rule tests. `make guardrails-verify` runs, in the pack image and with no skill file:
- every implemented rule's violating canary must fire and its clean canary must pass (73 of 73 on 2026-10-04);
- a negative control: a mutant pack with three checkers disabled must fail exactly those three rules.

The pytest and coverage work that section 9 planned for `python-guardrails@2` is now planned for **`python-guardrails@3`**, because `@2` is the version above.

### 10.4 Lock and running `main-02`

- Lock `28c51440f74a`. Execution changed: r1's components and both arms' component versions. The analysis section adds `primary_outcome`.
- Long jobs run detached (AGENTS.md Workflows). `main-02`:

```sh
make experiment-execute EXPERIMENT=experiments/ab-test/python__forge__guardrails/experiment.yaml \
  CONCURRENCY=2 JOB_ID=main-02 DETACH=1
make experiment-status JOB=jobs/python__forge__guardrails/main-02   # poll until state: finished
make experiment-assess JOB=jobs/python__forge__guardrails/main-02 STORE=stores/python__forge__guardrails \
  SCORERS="correctness@1 code-quality@1 resource-usage@1 process-compliance@2 gate-effort@2"
make experiment-analyze JOBS="jobs/python__forge__guardrails/main-02" \
  STORE=stores/python__forge__guardrails ANALYSIS_OUT=analysis/python__forge__guardrails
```

- **Expectation (stated before data):** r1's cost per trial is as before, plus the one-time pack install and 1-2 s of shadow gate per step. Every reference solution and every sampled agent snapshot would be refused (section 7), so r1's `first_pass_clean_rate` is expected near 0. r2's first-run rate is expected to rise above r1's at later checkpoints, if refusals at earlier steps teach the agent.


### 10.5 Process check `smoke-02` (recorded after the run; not pooled, never cited)

Before `main-02`, one trial per arm (attempt 0) ran under lock `28c51440f74a`, launched with `DETACH=1` and monitored with `make experiment-status` to `state: finished` (39 min, both trials valid, every probe passed, no integrity flags).
- **Detached launch:** the supervisor ran as its own process-group leader under `caffeinate -i` and wrote `detach/exit.json` (exit 0).
- **Shadow gate (r1):** 8 of 8 steps `shadow_recorded`, 0 fix sessions. Each run's `git` block shows the refusal in the shadow repository (`HEAD` unmoved, `A  forge.py` staged), and nothing reached `/app`. r1's work-session traces never mention the gate, `ruff.toml`, `mypy.ini`, `.git/` or `core.hooksPath`.
- **Commit evidence (r2):** the refusal and the later commit are both visible in git: `HEAD` moves only on the passing run, e.g. checkpoint 1 refused (hook 1, `git commit` 1) and then committed `a5ee34d`.
- **Paired first-run outcomes:** `first_pass_clean_rate` 0 vs 0. `total_first_run_blocking` 1,336 (r1) vs 30 (r2). r1's count is dominated by mypy strict's `no-untyped-call`, reported once per call site (202 of 294 findings at checkpoint 8). The total therefore scales with r1's call graph; read the binary `first_pass_clean_rate` and the per-family counts first.
- **Signals (1 pair; `insufficient` by design):** `solve_rate_errata` Δ −0.25 (r1 solved checkpoints 1-2; r2 solved none). Cost per trial $0.034 (r1) vs $0.077 (r2), of which r2's fix sessions were $0.036. The manipulation check separated.

## 11. `main-02` outcome and the `gate-effort@3` amendment (2026-10-05, recorded after the run)

Exploratory. 4 pairs against `min_pairs` 5, so every paired verdict reads `insufficient` by design. These are signals, never evidence of an effect.

### 11.1 Run

`main-02` ran detached under lock `28c51440f74a` at concurrency 2, 20:42:16-23:15:45 UTC on 2026-10-04: 8 of 8 trials valid, no infrastructure errors, every probe passed, no leftover containers, `detach/exit.json` exit 0. Total cost about $0.42.

### 11.2 Signals (r1-baseline vs r2-guardrails, means per trial)

- **Primary** `solve_rate_errata`: 0.344 vs 0.188, Δ −0.156 [−0.70, 0.39]. Raw `solve_rate` beside it: 0.219 vs 0.156, Δ −0.063. Same direction as `smoke-02` (Δ −0.25).
- Correctness C 0.865 vs 0.753 (Δ −0.11); headline S 73.1 vs 66.3 (Δ −6.8); `final_solved` 0 in both arms.
- Quality: erosion E 0.125 vs 0.608 (Δ +0.48) and verbosity V move by construction (the gate's C901 and SIM rules are what E and V count). Rework R 0.25 vs 0.42; architecture H and regression G flat.
- **Gate effort (paired first-run outcomes):** `total_first_run_blocking` 609.8 vs 37.75 (Δ −572 [−1059, −85]); typing 478.5 vs 9.25, complexity 60.75 vs 13.25, style 56.5 vs 13.5, defects 13.5 vs 1. r2 is already lower at checkpoint 1, before any refusal (8.25 vs 24 findings per step), so seeing `ruff.toml` and `mypy.ini` alone changes first-pass output.
- **First-pass clean:** 0 of 30 decided steps in r1 and 0 of 31 in r2 (`gate-effort@3`, below). The section 10.4 expectation that r2's first-run rate would rise at later checkpoints was **not met**: r2's first-run count per step fell to about 2-3 at checkpoints 2-5 and rose again to 5-8 at checkpoints 6-8.
- **r2 rounds:** 31 of 32 steps refused their first commit. 30 were clean after 1 fix round and b00 checkpoint 7 after 2 (32 refused runs in all). Unresolved 0, gate errors 0. Manipulation check separated (`gate_clean_at_step_end` 32/32).
- **Git evidence:** every refused run left `HEAD` unmoved with the code still staged; the passing run moved `HEAD`. r1's 32 shadow runs were all refused in the shadow repository, and nothing reached `/app`. r1's agent never saw the gate (0 mentions in its session traces).
- **Cost:** $0.033 vs $0.072 per trial, of which r2's fix sessions were $0.037 (51%). Tokens 1.24M vs 2.97M. Agent time 516 s vs 1,004 s.

### 11.3 Measurement defect and `gate-effort@3`

At b03 r2 checkpoint 2 the agent changed nothing (an 11 s session ending in a question). The commit gate commits with `--allow-empty`, so the unchanged tree passed and `gate-effort@2` counted the step as first-pass clean (r2 rate 1/32). `gate-effort@3` keeps every `@2` rule and reports `first_pass_clean` as `not_applicable` when a step's production and test files equal the previous snapshot's. The experiment now declares `gate-effort@3`; the relock kept `lock_sha256` `28c51440f74a`. `main-02` was rescored with `@3`, and the replay was identical. The defect affects three steps: b03 r2 checkpoint 2, and b02 r1 checkpoints 2-3 (also no-edit steps). First-pass clean is now 0 in both arms.
