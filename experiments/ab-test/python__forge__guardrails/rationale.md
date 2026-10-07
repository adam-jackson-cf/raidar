# python__forge__guardrails: rationale and pre-registration

- **Status:** validated and locked (`experiment.lock.json`). Smoke run (`smoke-01`, section 9); **main job not yet run** (`main-01` was aborted, section 9). This file was written before any job of this experiment. Sections 3 to 6 are the pre-registration. Later additions go in sections 9 and 10 and never edit sections 3 to 6. **Section 10 (explore iteration 2, 2026-10-04) amends the design before `main-02`:** r1 gets a shadow gate, the lock is `28c51440f74a`. **Section 12 (explore iteration 3, 2026-10-05) amends it again before any iteration-3 job:** the gate runs the agent's own tests (`python-guardrails@3`), snapshots each fix's starting tree (`guardrail-gate@3`), and both arms share `continue-on-stall@1`; the lock is `bbefb1c79a7c`.
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

## 12. Amendment: explore iteration 3 (2026-10-05, pre-registered before any job under its lock)

Written before the lock of this iteration and before any job under it. `smoke-01`, `smoke-02` and `main-02` stay under their locks and are never pooled with jobs under this one. The objective stays **explore**: 1 trial per arm first, then at most 4 attempts per arm, signals not conclusions. Sections 3 to 6 are unchanged except where this section says it supersedes a statement.

### 12.1 What changed in the design

| | r1-baseline | r2-guardrails |
|---|---|---|
| Shared | `prompt-wrapper@1` starter framing **and `continue-on-stall@1`** | same |
| Pack | **`python-guardrails@3`**, `workspace_configs: false` | **`python-guardrails@3`**, `workspace_configs: true` |
| Gate | **`guardrail-gate@3`**, `mode: shadow` | **`guardrail-gate@3`**, `mode: commit`, 3 fix rounds, 300 s |
| Scorers | `process-compliance@3`, `gate-effort@4` (both arms) | same |

- **`python-guardrails@3`: the gate also runs the agent's own tests**, like a real repository's commit check. It is `@1`'s gate, rules, pins (Ruff 0.8.6, mypy 2.3.1) and protected configs byte for byte, plus a `tests` check:
  - `unittest` loads every test file, always; pytest (9.1.1) runs the files with pytest-style tests (module-level `test_*` functions, non-`TestCase` `Test*` classes, a `pytest` import or a `conftest.py`) with its unittest plugin off, so no test runs twice;
  - both run under coverage (7.16.2), Python subprocesses included, with the project's interpreter, in a throwaway copy of the workspace: nothing the tests write reaches `/app`, so r1's shadow gate stays invisible;
  - `TEST.missing` (block): the change touches production Python and no test ran. "Touches" is git's view when the root is a git work tree with a commit (`git diff HEAD` plus untracked Python files); otherwise every production file counts as changed. A step with no Python change and no tests passes the check. This is what forces tests;
  - `TEST.failed` (block): a test failed or errored, or the run exceeded 180 s;
  - `TEST.coverage-minimum` (block): owned line coverage below **85%**. The threshold is the original rule catalogue's (`git show f61d81b:orchestrator/tests/fixtures/python_guardrails/enaible-rules.json`, rule `TEST.coverage-minimum`: `threshold_percent: 85`, `owned_slice_required: true`). The owned slice is the workspace's production files; test files are not measured. Evaluated when at least one test ran;
  - never the hidden verifier's tests (F4): the gate reads only its root, the hidden tests are never in the container during the agent phase, and a root under `/tests` or `/logs/verifier` is a gate error.
  - `PT009`/`PT027` stay **off** (a decision of this iteration, not the `@2` plan of section 9): unittest is still a first-class runner (discovery always runs), so unittest-style assertions are not a defect. The other `PT` rules stay warn.
- **Per-trial install** (`raidar.components.provision_v2`): the pack installs once per trial **at environment start** (uploaded payload, root-owned tree, hash-pinned locks; the install also records the gate's version, a smoke as the agent user and a digest of the whole installed tree). Each step's check runs no tool: it verifies the link, ownership, writability, protected configs and the tree digest against the install's. Nothing is materialised per step. A changed tree after step 1 is the integrity flag `protected-tree-modified`.
- **`guardrail-gate@3`**: `@2`'s loop, modes, prompts and git evidence, plus a snapshot of `/app` before each fix session (the verifier's artifact mechanism and excludes), recorded with its tree digest in the gate record. The snapshots are graded offline beside the step's final snapshot (`make experiment-assess` grades them into the store before scoring). A shadow gate starts no fix session and takes none.
- **`continue-on-stall@1`** (shared, identical in both arms; the K3 no-stall rule): when a session changes no file in `/app` and its final message asks a question or for clarification or confirmation (fixed patterns: a sentence ending in `?`, "please clarify/confirm", "let me know", "need your input", "before I proceed", "should I", "would you like"...), one continuation session of the same agent starts: no human is available, proceed with the specification and reasonable assumptions, record them in `ASSUMPTIONS.md`. It is a session wrapper, so it runs inside the gate: a stalled work session is continued before the gate runs. Every session's detection is recorded. Nothing in it names a problem.
- **Factor guard:** the arms still differ only in `condition`; the shared components are identical.

### 12.2 Hypothesis and outcomes (pre-registered)

- **Primary (unchanged):** `solve_rate_errata`, raw `solve_rate` beside it. With at most 4 pairs against `min_pairs` 5, it reads `insufficient` by design.
- **Hypothesis of this iteration:** enforcing the agent's own tests in the gate prevents fix-round regressions: in r2, fix rounds do not lower the case pass rate the round started from.
- **Secondary (pre-registered):**
  - **`gate.fix_round_correctness_delta`** (`gate-effort@4`): per step with a fix round, the final case pass rate minus the pass rate of the tree the first fix session started from; per trial, the mean over such steps. r2 only: r1's shadow gate has no fix round, so it is `not_applicable` and there is no paired comparison. The signal is its sign and size against 0, read beside iteration 2's unexplained negative correctness direction (section 11).
  - **`process.stall_rate`** (`process-compliance@3`): the share of reached steps in which a session stalled; paired across arms. Expected low in both arms (main-02: 3 no-edit, question-ending steps of 64), with the continuation resolving them (`stall_resolved`).
  - **Coverage and tests:** `gate.mean_coverage_at_step_end`, `gate.mean_first_run_coverage`, `gate.first_run_tests_pass_rate`, `gate.tests_missing_rate`, `gate.tests_pass_at_step_end_rate` (paired; r1's are what its unconstrained code would have scored).
  - **Gate effort:** every `gate-effort@3` outcome, now under `gate-effort@4` (`TEST.*` findings count in the `tests` family).
  - **Cost:** $ per trial, tokens, agent seconds; fix sessions and continuations are billed to their arm.
- **Manipulation check (unchanged):** `gate_clean_at_step_end`, `min_share` 0.8, decided by r2's share alone.
- **Expectations (stated before data):** r2's first commit is refused at step 1 for `TEST.missing` in nearly every trial (no forge job so far wrote a test); fix sessions then add tests, and later steps may fail `TEST.failed` or `TEST.coverage-minimum` (forge is a CLI; tests that run it as a subprocess are measured). r2's cost per trial rises above iteration 2's ($0.072). r1's shadow `tests_missing_rate` is expected near 1.0. The sign of `fix_round_correctness_delta` is not predicted.

### 12.3 Rule verification (`make guardrails-verify`)

`make guardrails-verify` now proves both payloads in the pack image, each with its own install script: `python-guardrails@1` (the payload `@1` and `@2` install) and `@3`. The three config entries that had no canary now have one in both: `lint.per-file-ignores` (`RUFF.LINT.per-file-ignores`), `explicit_package_bases` and `disable_error_code` (`MYPY.*`); no pack config entry is uncovered. `@3` adds seven tests-check canaries: `TEST.missing`, `TEST.no-change-exempt`, `TEST.failed`, `TEST.pytest-runner`, `TEST.coverage-minimum`, `TEST.subprocess-coverage` and `TEST.isolated-run` (the test run writes nothing to the workspace). The negative control adds a mutant with the coverage minimum at 0 for `@3`. Results are recorded in 12.5.

### 12.4 Running iteration 3

```sh
make experiment-lock EXPERIMENT=experiments/ab-test/python__forge__guardrails/experiment.yaml
# Process check first: 1 trial per arm (attempt 0), detached.
make experiment-execute EXPERIMENT=experiments/ab-test/python__forge__guardrails/experiment.yaml \
  ATTEMPT=0 CONCURRENCY=2 JOB_ID=smoke-03 DETACH=1
make experiment-status JOB=jobs/python__forge__guardrails/smoke-03   # poll until state: finished
# Grades the pre-fix snapshots into the store, then scores.
make experiment-assess JOB=jobs/python__forge__guardrails/smoke-03 STORE=stores/python__forge__guardrails \
  SCORERS="correctness@1 code-quality@1 resource-usage@1 process-compliance@3 gate-effort@4"
# Then, if the process is clean: main-03 with at most 4 attempts per arm, detached.
```

### 12.5 Verification before any job (recorded at lock time)

Lock `bbefb1c79a7c` (earlier locks of this iteration, `81186f03517f`, `e061cd8ff363`, `4257867a05bd` and `6a15e04580e6`, were replaced before any real-LLM job: behaviour-preserving refactors of the tests check for the complexity gate, and two fixes found by `make quality`: the tests check consults git only when the workspace is the top of its own work tree, never a folder of an outer repository, and its coverage steps ignore a caller's own `COVERAGE_*`/`COV_CORE_*` settings). No real-LLM job has run under it.

- **Rule tests** (`make guardrails-verify`, OrbStack, 2026-10-05): `python-guardrails@1` 76 of 76 rules proven (73 plus the three new config canaries); `@3` 83 of 83 (76 plus the seven tests-check canaries); each pack's mutant fails exactly its disabled rules (`@3`'s includes the coverage minimum set to 0).
- **Real two-step trials, no LLM** (`tests/test_guardrails_v3_trial.py`, pack image on OrbStack): r2's condition refused step 1 for `TEST.missing`, then for `TEST.coverage-minimum` (75%), then passed (two pre-fix snapshots, digests matching the trees); step 2's work session stalled on a question, was continued, and the gate then passed first time, with both sessions billed. r1's condition ran the agent's tests in the shadow gate without writing `/app` and continued its stalled step.
- **`per_step_reset`** (no-LLM `nop` jobs of this experiment, attempt 0, both arms, concurrency 2, OrbStack; jobs under `.tmp/perf-jobs/`):

| Job | Lock | `per_step_reset` p95 (n=16) | Step 1 | Steps 2-8 | `environment_start` |
|---|---|---|---|---|---|
| `perf-before-nop` | `28c51440f74a` (`python-guardrails@2`) | **7.32 s (fail)** | 7.14, 7.84 s | 2.22-2.46 s | 3.7-3.8 s |
| `perf-after-nop` | `81186f03517f` (`python-guardrails@3`) | **1.44 s (pass)** | 1.13, 1.00 s | 1.11-1.45 s | 10.9-11.5 s |

The install now runs at environment start (budget 20 s) and no tool runs per step, so every step is inside the 2 s budget, including step 1. The pack's probe passed at all 16 steps of both jobs with no integrity flag (the `nop` agent runs no wrapper, so the prompt-wrapper, gate and stall probes fail by construction in both jobs). `main-02`'s real-LLM `per_step_reset` was p95 5.04 s (step 1 about 5 s, steps 2-8 about 2.2 s).

### 12.6 Known limits of iteration 3

- **r1's shadow repository has no commit.** It is fresh at each step, so the tests check's "no Python change since the last commit" exemption never applies in r1; a no-edit r1 step without tests reads `TEST.missing`. `gate-effort@4` reports the first-run tests outcomes of a no-edit step as `not_applicable` (as `first_pass_clean`), so the pairing is unaffected.
- **`fix_round_correctness_delta` describes r2 only** (a shadow gate has no fix round). It uses the tree the first fix session started from; later rounds' snapshots are recorded but not scored.
- **The test run's interpreter** is the project's (`.venv/bin/python`, else `python3`); the gate's pytest and coverage are appended to its `PYTHONPATH`, after the project's own packages. Subprocess coverage relies on a start-up hook on that path; a test that clears the environment of its subprocess is not measured there.
- **Stall detection is pattern-based and needs no file change.** A stall that edits a scratch file, or asks without a recognised pattern, is not continued; a no-edit session that ends with a question it does not need answered is continued once. Both are recorded.

### 12.7 Process check: `smoke-03` and `smoke-03-r2` (recorded 2026-10-06, after the runs)

Process check only (1 trial per arm under lock `bbefb1c79a7c`). Signals, not conclusions.

- **Runs.** `smoke-03` (both arms, attempt 0, launched 22:19:20 UTC on 2026-10-05, 30 s after this section's commit) and `smoke-03-r2` (r2 only, a rerun after `smoke-03` r2 failed). r1 was valid. **No r2 trial was valid**: `smoke-03` r2 failed checkpoints 1, 4 and 6, and `smoke-03-r2` failed checkpoint 4, each with `stage_failed ApiOverloadedError` (Codex: "Selected model is at capacity"; one of them after the session had edited `/app`). Both r2 trial records fail the `agent_infra` validity check. `main-03` was **not started**.
- **Process defect C1 (gate scope).** In `smoke-03` r2 the agent wrote the program as an extensionless `/app/forge` (a `#!/usr/bin/env python3` script) with `/app/forge.py` a symlink to it. `python-guardrails@3`'s scope (byte-identical to `@1`'s) counts only regular `*.py` files and skips symlinks, so every gate run of that trial checked **0 files**: no finding, no test required, every commit passed. A re-scan of every earlier job of this experiment (`smoke-01`, `smoke-02`, `main-02`, `smoke-03` r1, `smoke-03-r2`) found no other extensionless script or `.py` symlink, so earlier data is unaffected.
- **Process defect (stall exposure).** `continue-on-stall@1` wraps every session the gate starts, so it continues **at most once per session, not once per step** (this section said "one continuation" per stalled session; with `max_rounds` 3 an r2 step had up to 4 chances against r1's 1). No step stalled in either job (0 continuations), so the data is unaffected, but `stall_rate` exposure was not equal across arms.
- **Detection gap (K3).** `smoke-03` r2 checkpoints 2 and 3 were no-edit sessions that ended in a refusal, not a question ("I can't build a compatible `forge` CLI without that context"; "I can't implement blueprint lookup and allocation persistence without making those choices up"). `@1`'s patterns matched neither, so neither was continued.
- **What worked (`smoke-03-r2` r2, 7 gated steps).** The tests check refused every first commit (7 of 7: `TEST.missing` at checkpoint 1, `TEST.coverage-minimum` at 72.7-76.7% owned coverage at checkpoints 2, 3 and 5-8, plus style and complexity findings); every refusal was clean after 1 fix round, coverage at step end 87.6-92.1%, tests grew from 7 to 22. The 7 pre-fix snapshots were graded, each digest matching its grade. `fix_round_correctness_delta` was **−0.0018** (per step: 0, 0, −0.0099, 0, +0.0047, +0.0199, −0.0271): fix rounds neither clearly helped nor hurt case pass rate. The pytest and coverage tools never reached the agent ("pytest is not installed", r2 at checkpoints 3 and 6-8). `per_step_reset` stayed at 1.0-1.3 s (one breach, 2.17 s at checkpoint 8).
- **r1 (`smoke-03`, valid).** Solved checkpoints 1-2 (`solve_rate` 0.25); case pass rate 1.0, 1.0, then 0.94, 0.96, 0.96, 0.89, 0.88, 0.85. Shadow gate: 243 first-run blocking findings (typing 88, complexity 73, style 67, tests 8, defects 7), `tests_missing_rate` 1.0 (r1 wrote no test, as expected in 12.2). $0.041 per trial.
- **Cost.** r2's `smoke-03-r2` session costs sum to about $0.130 (fix sessions included); the invalid trial's `resource-usage` total is null because a failed session reported no usage.

Decision: fix the scope, the stall exposure and detection, and the provider drops as new versions under a new lock (section 13), then rerun the 1-trial-per-arm process check before any main job.

## 13. Amendment: explore iteration 3b (2026-10-06, pre-registered before any job under its lock)

Written after `smoke-03`/`smoke-03-r2` (12.7) and before any job under lock `8d8ad43b53d8`. Jobs under `bbefb1c79a7c` are never pooled with jobs under this lock. The objective stays **explore**: `smoke-04` (1 trial per arm) first, then `main-03` with at most 4 attempts per arm only if `smoke-04` is clean. Section 12's design, hypothesis and outcomes stand except where this section says otherwise.

### 13.1 What changed (new versions; released versions untouched)

| | Iteration 3 (`bbefb1c79a7c`) | Iteration 3b (`8d8ad43b53d8`) |
|---|---|---|
| Pack (both arms) | `python-guardrails@3` | **`python-guardrails@4`** |
| Shared stall wrapper | `continue-on-stall@1` | **`continue-on-stall@2`** |
| Shared retry wrapper | none | **`transient-retry@1`** |
| Gate | `guardrail-gate@3` | unchanged |
| Gate-effort scorer | `gate-effort@4` | **`gate-effort@5`** |

- **`python-guardrails@4`** (fixes C1): `@3` byte for byte except the scope and its consumers. A Python file is a regular `*.py` file, an extensionless regular file whose first line is a `#!` line naming `python`, or the in-workspace target of a `*.py` symlink (counted once, at the target's path; links leaving the workspace and symlinked directories stay out). Ruff is given the extensionless files explicitly beside `.`; findings reported through a link are deduplicated; the tests check's change detection sees the same files. Two new canaries (`SCOPE.shebang-script`, `SCOPE.py-symlink`) prove it in the pack image; the `smoke-03` r2 layout now yields 1 checked file, `F401` at `forge` and `TEST.missing`.
- **`continue-on-stall@2`** (fixes the exposure; K3): only the **step's first session** is eligible, decided from the step's own logs (no earlier continue-on-stall record under the step's agent logs dir), so a step gets at most one continuation in every arm; later sessions (r2's fix sessions) are recorded (`eligible: false`, `would_stall`) and never continued. A no-edit session also stalls when its final message refuses: first-person "can't/cannot/unable to ... without", "not enough/insufficient/missing context/information/details/requirements/specification", or "... isn't/is not defined/present/specified/provided/available" and "doesn't define/specify/say" (curly apostrophes included). Both `smoke-03` messages match. The continuation prompt names both cases.
- **`transient-retry@1`** (new, shared, innermost: gate → stall → retry → native): a session that raises Harbor's `ApiOverloadedError`, `ApiConnectionClosedError`, `ApiResponseStalledError`, `ApiInternalServerError` or `ApiRateLimitError` is run again after 60 s, then 180 s, at most twice, with a fixed preface ("cut off by the model provider ... the workspace keeps every change"); any other error, and the last retry's, is raised unchanged (still an infra failure). Every attempt's logs, error class, wait and usage are recorded; the session's cost sums every attempt.
- **`gate-effort@5`** (C2, text only): `@4`'s values and statuses, with each gate run's evidence summary counting `TEST.*` findings in the `tests` family (`@4` printed them as `other`).
- **Factor guard:** the arms still differ only in `condition`; the pack, both wrappers and the scorers are identical in both arms.

### 13.2 Hypothesis and outcomes

Unchanged from 12.2 (primary `solve_rate_errata` with raw `solve_rate` beside it; secondary `gate.fix_round_correctness_delta`, `process.stall_rate`, coverage and tests, gate effort, cost). `process.stall_rate` now has equal exposure across arms (one eligible session per step) and counts refusals as well as questions. **Added, descriptive only:** retries per trial and recovered sessions (from `transient-retry@1` records), reported beside cost; a retried session's tokens and cost stay in its arm.

### 13.3 Pre-registered process criteria for `smoke-04` (before `main-03`)

`smoke-04` is clean when: both trials are valid; every component probe passes; r2's gate checks a non-zero file count at every step that has Python files; no step is lost to a transient provider error that `transient-retry@1` did not recover (at most 2 retries per session); and no step has more than one continuation. A failed criterion is a process defect: fix it and rerun small; do not start `main-03`.

### 13.4 Running iteration 3b

```sh
make experiment-lock EXPERIMENT=experiments/ab-test/python__forge__guardrails/experiment.yaml   # 8d8ad43b53d8
make experiment-execute EXPERIMENT=experiments/ab-test/python__forge__guardrails/experiment.yaml \
  ATTEMPT=0 CONCURRENCY=2 JOB_ID=smoke-04 DETACH=1
make experiment-status JOB=jobs/python__forge__guardrails/smoke-04
make experiment-assess JOB=jobs/python__forge__guardrails/smoke-04 STORE=stores/python__forge__guardrails \
  SCORERS="correctness@1 code-quality@1 resource-usage@1 process-compliance@3 gate-effort@5"
# If 13.3 holds: main-03, 4 attempts per arm, detached, then assess and analyse as above.
make experiment-execute EXPERIMENT=experiments/ab-test/python__forge__guardrails/experiment.yaml \
  CONCURRENCY=2 JOB_ID=main-03 DETACH=1
```

### 13.5 Verification before any job (recorded at lock time)

- `tests/test_guardrails_v4_trial.py` (pack image on OrbStack, no LLM): an r2 trial whose step-1 work session raises `ApiOverloadedError` once is retried and recovers (both attempts billed); the retry writes the `smoke-03` layout, which `@4` checks (1 file, `F401` at `forge`, `TEST.missing`); the first fix session asks without editing and is recorded, not continued; the second fix passes; step 2's no-edit refusal is continued and the gate passes.
- Host tests: `tests/test_component_python_guardrails_v4.py` (`@3` checks the `smoke-03` layout as 0 files, `@4` as 1; an extensionless script is measured through its subprocess test; out-of-workspace links stay out), `tests/test_session_wrappers_v2.py`, `tests/test_gate_effort_v5_scorer.py`.
- `make guardrails-verify` (OrbStack, 2026-10-06): `@1` 76 of 76 rules, `@3` 83 of 83, `@4` 85 of 85 (`@3`'s plus `SCOPE.shebang-script` and `SCOPE.py-symlink`); each pack's mutant fails exactly its disabled rules (`@4`'s mutant has `@3`'s scope, so both `SCOPE.*` canaries fail).

### 13.6 Known limits of iteration 3b

- A first session that stalls by editing a scratch file, or refuses in words outside the fixed patterns, is still not continued. Later sessions of a step are never continued, even when they would stall (recorded as `would_stall`).
- `transient-retry@1` retries after the session's own partial work; the retry sees the workspace as the failed attempt left it. A provider outage longer than about 4 minutes still fails the step (an infra exclusion, as before). A failed attempt that reported no usage makes the session's cost unknown (never a partial sum).
- Ruff given `.` still follows a `*.py` symlink that leaves the workspace (as in `@1`-`@3`); the scope (mypy, bespoke checks, tests check) does not.

### 13.7 Process check `smoke-04` (recorded 2026-10-06, after the run; not pooled, never cited)

`smoke-04` ran under `8d8ad43b53d8` (1 trial per arm, attempt 0, detached; finished 2026-10-06 02:21 UTC, 2 of 2 trials valid) and meets all five criteria of 13.3: both trials valid, every probe passed at all 16 steps, r2's gate checked 1-2 files at every step (C1 fixed), 0 transient errors and 0 retries, 0 continuations. Signals (n=1, exploratory): `solve_rate_errata` r1 0.25, r2 0.5 (raw `solve_rate` 0.25 both); headline score 78.3 vs 82.6; cost $0.039 vs $0.203 per trial, of which r2's fix sessions were $0.153 (the cp7 fix session alone 1563 s, 114 tool calls, $0.090). The review found six measurement or design defects, fixed in section 14 before any further job:

- **O1** the gate demands tests, coverage and formatting, but `pytest`, `coverage`, `ruff` and `mypy` were not on the agent's PATH in either arm ("pytest: command not found", "No module named pytest" at cp1, cp3, cp6, cp7 of r2); FORMAT refused every first gate run, so `gate.first_pass_clean_rate` could not rise above 0 (also seen in `smoke-03-r2`, 12.7).
- **O2** `process-compliance@3`'s `tests_run_before_finish` counted a test command that never ran (r2 cp6, `python -m pytest -q` → "No module named pytest") and did not count `guardrail-check`, which ran 15 tests.
- **O3** `gate.total_first_run_blocking` is not comparable across gate modes: r1's shadow gate re-counts the accumulated tree at every step (31, 49, 58, 79, 84, 134, 187, 225: sum 847, final tree 225); r2's starts each step clean (sum 47).
- **O4** the typing family counts call sites: one untyped `def` gives `MYPY.no-untyped-call` at every call (r1 cp8: 139 of 225 blocking).
- **O5** `first_run_tests_pass_rate` mixes failing tests with missed coverage, and `TEST.failed` counts once per failing test (r2: 9 at cp7) while a coverage miss counts once.
- **O6** the analysis tables did not say which direction is better (erosion E is 1 minus the complex-function mass share: higher is better; r1's single `run()` with cc 75 is the worse arm).

The verifier's per-test coverage pass (G's evidence) took about 1077 s of each trial (8 steps), more than r1's whole agent time (611 s), on the trial's critical path.

## 14. Amendment: explore iteration 3b.2 (2026-10-06, pre-registered before any job under its lock)

Written after `smoke-04` (13.7) and before any job under lock `2b4c23f42ac9`. Jobs under `8d8ad43b53d8` are never pooled with jobs under this lock. The objective stays **explore**: `smoke-05` (1 trial per arm) first, then `main-03` with at most 4 attempts per arm only if `smoke-05` is clean. Sections 12 and 13 stand except where this section says otherwise. The agent stays `codex` with `gpt-6-luna` at low effort (user decision 2026-10-06).

### 14.1 What changed (new versions; released versions untouched)

| | Iteration 3b (`8d8ad43b53d8`) | Iteration 3b.2 (`2b4c23f42ac9`) |
|---|---|---|
| Pack (both arms) | `python-guardrails@4` | **`python-guardrails@5`** |
| Verifier coverage pass | after every step, in the trial | **offline, at assessment** (`protocol.verifier_coverage: offline`) |
| Code-quality scorer | `code-quality@1` | **`code-quality@2`** |
| Process scorer | `process-compliance@3` | **`process-compliance@4`** (+ `extra_test_patterns` for `guardrail-check`) |
| Gate-effort scorer | `gate-effort@5` | **`gate-effort@6`** |
| Shared wrappers, gate | `continue-on-stall@2`, `transient-retry@1`, `guardrail-gate@3` | unchanged |

- **`python-guardrails@5`** (O1; mechanical rung): `@4`'s gate, rules, pins, scope and tests check byte for byte except the version strings, plus the pack's own tools on the agent's PATH in **both** arms: `/usr/local/bin/pytest` and `/usr/local/bin/coverage` (wrappers that run `python -m <tool>` with the project's interpreter, `./.venv/bin/python` else `python3`, and the pack's site-packages appended to the end of `sys.path`, as the gate's tests check runs them) and `/usr/local/bin/ruff` and `/usr/local/bin/mypy` (the pinned binaries). The tools read the configuration they discover (r2: the workspace copies; r1: their defaults); the gate itself still runs only with the protected configs. The install records the four versions and an agent-user smoke of each (pytest, `coverage run -m pytest`, `ruff format --check`); the probe requires them and checks every step where each name resolves on the agent's PATH and where each link points. No rules text names the tools. Factor-neutral: the same component in both conditions.
- **`protocol.verifier_coverage: offline`** (user decision 2026-10-06): the verifier grades each step without its coverage pass (`record.json` `coverage.enabled: false`; the verdict run is unchanged; Harbor's verifier phase timeout drops from 2 × 900 + 600 s to 900 + 600 s). `make experiment-assess` with `code-quality@2` first makes each step's coverage run from the stored step snapshot (snapshot and bundle digests checked; the step's own bundle, grader timeout and grader image), checks its parity against the trial's own grading run (read back from `evidence.json`) and writes the parity verdict and the fork-shaped G input (the same payload the in-trial pass writes as `g-kernel-input.json`) to the Assessment store, never the job (`raidar.verifier.coverage_offline`). A parity failure (for example a rebuilt grader image: `environment_mismatch`) makes G `ineligible`, never 0. Left at its default (`trial`) the field is omitted from every dump, so no other experiment's lock moved (toolchain relocks only).
- **`code-quality@2`**: `code-quality@1` in every metric, status, reason and evidence item, except that a step whose trial deferred the coverage pass reads G's two run evidences from the store's offline run; a run not produced yet is `unavailable` and the Assessment provisional (completed by a later rescore). Validation refuses `code-quality@1` under `verifier_coverage: offline`. Analysis and the review surfaces read `@2` in the quality slot where a lock declares it.
- **`process-compliance@4`** (O2): `@3` except that a test command counts as a test run only when its recorded output does not say its runner was not found (`<runner>: command not found`/`not found`, `No such file or directory`, uv's `Failed to spawn`, `No module named <module>` for `python -m`); a call without recorded output keeps `@3`'s reading. Adds `test_commands_not_run`/`total_test_commands_not_run`. The experiment's `scorer_config` counts `guardrail-check` (not `--version`/`--help`) as a test run in both arms (the gate runs the project's tests). `process.compliance_rate` and `process.total_test_commands_not_run` join the process outcomes.
- **`gate-effort@6`** (O3-O5): `@5` with a failed tests check counted once in every blocking count (`first_run_failed_tests` keeps the number of failing tests); per-step `step_end_blocking` and `new_first_run_blocking` (first-run blocking minus the previous gated step's end; a shadow gate's sum telescopes to its final tree) with `total_new_first_run_blocking` and `final_step_end_blocking`, comparable across modes; the `MYPY.no-untyped-call` cascade reported beside the typing family (`total_first_run_untyped_call`, `final_step_end_untyped_call`); and `first_run_tests_failed_rate` and `first_run_coverage_met_rate` separately. `total_first_run_blocking` is still reported, unchanged in meaning.
- **Analysis presentation** (O6): every effect and arm table has a `Better` column (the direction the metric's scorer declares) and a legend; erosion is stated as higher-is-better.

### 14.2 Hypothesis and outcomes

Unchanged from 12.2 and 13.2 (primary `solve_rate_errata` with raw `solve_rate` beside it; secondary `gate.fix_round_correctness_delta`, `process.stall_rate`, coverage and tests, gate effort, cost). Gate-effort comparisons across arms read `gate.total_new_first_run_blocking` and `gate.final_step_end_blocking`; `gate.total_first_run_blocking` stays reported but is not compared across modes. Exposing the tools changes what both arms can do; it is part of the shared environment, not the treatment, so iteration 3b.2's arms are not pooled with 3b's.

### 14.3 Pre-registered process criteria for `smoke-05` (before `main-03`)

`smoke-05` is clean when 13.3 holds and also: every step's probe shows the agent's `pytest`, `coverage`, `ruff` and `mypy` linked to the pack's in both arms; no `pytest`, `coverage`, `ruff` or `mypy` command in either arm fails with "command not found"; every verifier record has `coverage.enabled: false`; and `make experiment-assess` makes all 16 offline coverage runs with 0 errors, each parity verdict eligible or `ineligible` with typed reasons. A `python -m pytest` that fails with "No module named pytest" (the system interpreter has no pytest; 14.6) is reported with `process.total_test_commands_not_run`, not a defect. A failed criterion is a process defect: fix it and rerun small; do not start `main-03`.

### 14.4 Running iteration 3b.2

```sh
make experiment-lock EXPERIMENT=experiments/ab-test/python__forge__guardrails/experiment.yaml   # 2b4c23f42ac9
make experiment-execute EXPERIMENT=experiments/ab-test/python__forge__guardrails/experiment.yaml \
  ATTEMPT=0 CONCURRENCY=2 JOB_ID=smoke-05 DETACH=1
make experiment-status JOB=jobs/python__forge__guardrails/smoke-05
make experiment-assess JOB=jobs/python__forge__guardrails/smoke-05 STORE=stores/python__forge__guardrails \
  SCORERS="correctness@1 code-quality@2 resource-usage@1 process-compliance@4 gate-effort@6"
# If 14.3 holds: main-03, 4 attempts per arm, detached, then assess and analyse as above.
make experiment-execute EXPERIMENT=experiments/ab-test/python__forge__guardrails/experiment.yaml \
  CONCURRENCY=2 JOB_ID=main-03 DETACH=1
```

### 14.5 Verification before any job (recorded at lock time)

- Host and fixture tests: `tests/test_component_python_guardrails_v5.py` (`@4`'s payload byte for byte except version strings, plus the two wrappers and their runner; the probe fails when a tool is missing from the PATH or its smoke failed; the wrappers run the pack's pytest and coverage with the project's interpreter), `tests/test_process_compliance_v4.py`, `tests/test_gate_effort_v6_scorer.py`, `tests/test_code_quality_v2.py` (on the fork's golden `synthetic_shop` case: a deferred trial whose offline runs are in the store gives every observation `code-quality@1` gives over the same trial with the in-trial pass; an unproduced run is `unavailable` and the Assessment provisional until a rescore completes it), `tests/test_coverage_offline.py` (a deferred pass is made into the store, never the job; the offline run reproduces a trial's own pass, parity verdict and `g-kernel-input.json`, exactly), `tests/test_verifier_coverage_protocol.py`, `tests/test_iteration_3b2_consumers.py`.
- `tests/test_guardrails_v5_trial.py` (pack image on OrbStack, no LLM; r1 and r2): a scripted agent writes a module and its test in `/app` and runs `pytest`, `coverage run -m pytest`, `ruff format --check` and `mypy --version` there; all pass with the pinned versions (`/usr/local/bin/pytest`; pytest 9.1.1, coverage 7.16.2, ruff 0.8.6, mypy 2.3.1) and the probe passes on `@5`'s pin.
- `make guardrails-verify` (OrbStack, 2026-10-06): `@1` 76 of 76 rules, `@3` 83 of 83, `@4` 85 of 85 and `@5` 85 of 85 (`@4`'s canaries, run against `@5`'s payload and its install, agent tool links and smokes included); each mutant fails exactly its disabled rules.
- `make quality` passes (1562 tests, coverage 96.5%).
- Released identities: every registered scorer's manifest and implementation digest and every component's fingerprint is unchanged from `0e82446`; only `python-guardrails@5`, `code-quality@2`, `process-compliance@4` and `gate-effort@6` are new. Only this experiment's `lock_sha256` moved (`8d8ad43b53d8` → `2b4c23f42ac9`); the other five locks are toolchain relocks.
- **Offline coverage reproduces the in-trial pass on `smoke-04`** (2026-10-06, `make assess-regrade JOB=<trial> COVERAGE_CHECK=1`, both trials concurrently on OrbStack, grader image `874d2b90b9ad` as recorded): 16 of 16 steps `match` against the trial's own pass (same parity verdict, eligible at every step; same parity differences; `g-kernel-input.json` equal). `code-quality@2` reads exactly that payload and applies `@1`'s rules to it, so its G values over a deferred trial equal `@1`'s over the in-trial pass (shown value for value on the golden fixture above). The offline runs took 1108 s (r1) and 1132 s (r2) per trial against about 1077 s and 1095 s in the trial.

### 14.6 Known limits of iteration 3b.2

- The coverage work is moved, not removed: about 18 minutes per trial of grader time now runs at `make experiment-assess` (once per step snapshot, kept in the store) instead of inside the trial. Trial wall time and the agent's step-to-step wait drop by that amount; the job's verifier phase is the grading run alone.
- Offline parity needs the trial's grader image: a rebuilt or pruned grader image makes every offline parity check `environment_mismatch` and G `ineligible` (never 0). Keep the image until the job's offline runs are made.
- `python -m pytest` with the system interpreter still fails with "No module named pytest": `@5` puts the tools on the PATH and does not change the agent image's interpreter. `process-compliance@4` reports such commands as not run. Making `python3 -m pytest` work would mean installing into the image's interpreter (a broader change to what the agent's own code can import) and is not done here.
- The tools on the PATH read the configuration they discover; in r1 (no workspace copies) `ruff format --check` uses Ruff's defaults, which can differ from the gate's protected `ruff.toml`. r1's gate is a shadow and never refuses, so this changes nothing r1 is scored on.

### 14.7 Process check `smoke-3b-2` (recorded 2026-10-07, after the run; not pooled, never cited)

The process check that 14.3 and 14.4 name `smoke-05` ran as job `smoke-3b-2` under `2b4c23f42ac9` (1 trial per arm, attempt 0, concurrency 2, detached; 2026-10-06 22:40 to 23:28 UTC, 2 of 2 trials valid). It meets every criterion of 13.3 and 14.3: every probe passed at all 16 steps with the agent's `pytest`, `coverage`, `ruff` and `mypy` linked to the pack's in both arms; no tool command failed with "command not found"; every verifier record has `coverage.enabled: false`; `make experiment-assess` made 16 of 16 offline coverage runs (0 errors, every parity verdict eligible) and 8 pre-fix grades (0 errors); 0 transient errors, retries, stalls or continuations; gate error rate 0. Signals (n=1, exploratory): `solve_rate_errata` r1 0.25, r2 0.5 (raw `solve_rate` 0.25 both); headline 79.2 vs 84.46; erosion (higher is better) 0.05 vs 0.72; cost $0.027 vs $0.190 per trial, of which r2's fix sessions were $0.149 (78%; cp7 $0.071, cp8 $0.027); `gate.fix_round_correctness_delta` +0.0049. The review found five measurement or design defects, fixed in section 15 before any further job:

- **O1** `PLR0904` (too-many-public-methods, at most 20) fired on r2's `unittest` test class (`tests/unit/test_forge.py`, 22 > 20) at cp4, cp6 and cp7, never on production code. The pack's test-file per-file-ignores listed only `D`, `S101` and `PLR2004`. cp7's fix session ($0.071, 482 s) split the class into four, aliased test methods into subclasses and set `__test__ = False` on the base, after which `pytest -q` collected nothing (the gate still ran 59 tests through `unittest`). Requiring tests while limiting a test class's size makes the fix session reshape tests instead of code.
- **O2** `make experiment-assess` made the offline coverage runs and the pre-fix grades one trial after another: about 44 minutes for 2 trials (about 37 of them coverage), about 2.5 to 3 hours for `main-03`'s 8.
- **O3** `gate.first_pass_clean_rate` was 0 in both arms and cannot move: the work session is not told what the gate checks and `FORMAT` (fixed by the gate's own `--fix`) refused every first run. Without `FORMAT`, 6 of 8 r2 first runs would still be refused, mostly for `TEST.coverage-minimum` and `C901`.
- **O4** `process.compliance_rate` (r1 0, r2 0.5) measured the gate's presence: `plan_written` failed at 16 of 16 steps although neither condition asks for a plan, and r2's `tests_run_before_finish` passed only through `guardrail-check` runs in the gate's fix sessions (the work session itself ran `unittest` at cp4, cp5, cp6 and cp8 only).
- **O5** the cross-mode comparison (`gate.total_new_first_run_blocking`, `gate.final_step_end_blocking`) still counts the `MYPY.no-untyped-call` cascade (r1's final 190 includes 134 of it; r2's 0), so the arm difference is mostly call-site multiplicity.

## 15. Amendment: explore iteration 3b.3 (2026-10-07, pre-registered before any job under its lock)

Written after `smoke-3b-2` (14.7) and before any job under lock `f600b96bc7db`. Jobs under `2b4c23f42ac9` are never pooled with jobs under this lock. The objective stays **explore**: `smoke-3b-3` (1 trial per arm) first, then `main-03` with at most 4 attempts per arm only if `smoke-3b-3` is clean. Sections 12 to 14 stand except where this section says otherwise. The agent stays `codex` with `gpt-6-luna` at low effort (user decision 2026-10-06, restated 2026-10-07: "stick with luna"), and the verifier's coverage pass stays offline (14.1).

### 15.1 What changed (new versions; released versions untouched)

| | Iteration 3b.2 (`2b4c23f42ac9`) | Iteration 3b.3 (`f600b96bc7db`) |
|---|---|---|
| Pack (both arms) | `python-guardrails@5` | **`python-guardrails@6`** |
| Process scorer | `process-compliance@4` | **`process-compliance@5`** (same `extra_test_patterns`) |
| Gate-effort scorer | `gate-effort@6` | **`gate-effort@7`** |
| Offline assessment | one trial after another | **`CONCURRENCY=N` trials at once** (tooling, not locked) |
| Shared wrappers, gate, verifier coverage, `code-quality@2` | as 14.1 | unchanged |

- **`python-guardrails@6`** (O1; mechanical rung, generic): `@5`'s payload byte for byte except the version strings and `config/ruff.toml`, where `PLR0904` joins the per-file-ignores of the four test-file globs (`test_*.py`, `*_test.py`, `**/tests/**`, `**/test/**`) beside `D`, `S101` and `PLR2004`. The limit (20 public methods) still applies to every other file. A new canary, `RUFF.LINT.per-file-ignores.test-public-methods`, proves it in the pack image against pinned Ruff 0.8.6: a 21-method `unittest.TestCase` is quiet under each of the four globs (one file per glob) and a 21-method class at `pkg/service.py` is refused; `@6`'s mutant (`@5`'s ignores) refuses the clean fixture. Because `@6`'s `ruff.toml` is no longer `@1`'s bytes, its config-copy integrity flags compare the workspace copies with `@6`'s own bytes (`@3` to `@5` reused `@1`'s comparison, whose configs they shipped unchanged); without that, every r2 step after the first would be flagged `workspace-config-modified` on an untouched copy. Factor-neutral: the same component in both conditions; r1's shadow gate applies the same rule set.
- **`gate-effort@7`** (O3, O5): every `@6` metric, value, status and reason, plus (a) per step and as rates over the steps that decide `first_pass_clean` (shadow gates included; not applicable on a step with no source change) `first_pass_clean_except_autofix` (every blocking code of the first run is one of the fixed auto-fixable layout codes `FORMAT`, `I001`, `W291`, `W292`, `W293`), `first_pass_clean_except_tests` (every blocking code is `TEST.*`) and `first_pass_clean_except_autofix_tests` (both: the first run had no code-design finding); and (b) the cross-mode counts without the cascade: per step `first_run_blocking_ex_cascade`, `step_end_blocking_ex_cascade` and `new_first_run_blocking_ex_cascade`, with `total_new_first_run_blocking_ex_cascade` and `final_step_end_blocking_ex_cascade`. The untyped `def` itself stays counted (`MYPY.no-untyped-def`, once per def).
- **`process-compliance@5`** (O4): every `@4` metric, value, status and reason except `compliance_rate`, plus `work_session_tests_run_before_finish` (checkpoint: `@4`'s test-run check, a test command counting only if it ran, over the step's work session only, i.e. the session that received the step's first user prompt; the gate's fix sessions do not count; not applicable when the work session made no edit) and `work_session_tests_run_rate` (trajectory). `compliance_rate` counts `tests_run_before_finish` only unless the assessment sets `plan_in_compliance_rate: true` (a condition that asks for a plan); this experiment does not, as neither condition asks for one. `plan_written` is still reported at every step.
- **Offline assessment concurrency** (O2): `make experiment-assess ... CONCURRENCY=N` (and `make assess-regrade ... COVERAGE_CHECK=1 CONCURRENCY=N`) makes the pre-fix grades and the offline coverage runs of up to N trials at once (`raidar.verifier.per_trial`); each trial's steps stay in order, each run in its own grader container, and the report lists entries in trial order. Default 1 (unchanged). It changes when the store's records are made, not what they contain (a test shows 3 trials made at concurrency 3 store the same records as at 1, and the coverage check matches).

### 15.2 Hypothesis and outcomes

Unchanged from 12.2, 13.2 and 14.2 (primary `solve_rate_errata` with raw `solve_rate` beside it; secondary `gate.fix_round_correctness_delta`, `process.stall_rate`, coverage and tests, gate effort, cost), with these readings from this iteration on:

- Gate-effort comparisons across arms read `gate.total_new_first_run_blocking_ex_cascade` and `gate.final_step_end_blocking_ex_cascade`; `@6`'s cascade-inclusive counts stay reported beside them.
- First-pass cleanliness reads `gate.first_pass_clean_except_autofix_tests_rate` (descriptive; the share of steps whose work session left no code-design finding for the gate); `gate.first_pass_clean_rate` and the two single-exclusion rates stay reported. `gate.first_pass_clean_rate` is expected to stay near 0 in both arms (14.7, O3) and is not interpreted.
- The process-behaviour signal is `process.work_session_tests_run_rate`; `process.compliance_rate` now means the step-wide test-run check alone and, in r2, still includes the gate's own runs.
- Removing the test-class limit changes what r2's gate refuses, so iteration 3b.3's arms are not pooled with 3b.2's.

### 15.3 Pre-registered process criteria for `smoke-3b-3` (before `main-03`)

`smoke-3b-3` is clean when 13.3 and 14.3 hold and also: every probe answers `python-guardrails@6`'s pinned version; no gate run in either arm reports `PLR0904` at a test-file path; no step carries a `workspace-config-modified` flag unless the trace shows the agent edited a config copy; `make experiment-assess ... CONCURRENCY=2` grades every pre-fix snapshot and makes all 16 offline coverage runs with 0 errors, each parity verdict eligible or `ineligible` with typed reasons; and every new `gate-effort@7` and `process-compliance@5` observation is `ok` or carries a typed status and reason (no `error`). A failed criterion is a process defect: fix it and rerun small; do not start `main-03`.

### 15.4 Running iteration 3b.3

```sh
make experiment-lock EXPERIMENT=experiments/ab-test/python__forge__guardrails/experiment.yaml   # f600b96bc7db
make experiment-execute EXPERIMENT=experiments/ab-test/python__forge__guardrails/experiment.yaml \
  ATTEMPT=0 CONCURRENCY=2 JOB_ID=smoke-3b-3 DETACH=1
make experiment-status JOB=jobs/python__forge__guardrails/smoke-3b-3
make experiment-assess JOB=jobs/python__forge__guardrails/smoke-3b-3 STORE=stores/python__forge__guardrails \
  SCORERS="correctness@1 code-quality@2 resource-usage@1 process-compliance@5 gate-effort@7" CONCURRENCY=2
# If 15.3 holds: main-03, 4 attempts per arm, detached, then assess (CONCURRENCY=2) and analyse as above.
make experiment-execute EXPERIMENT=experiments/ab-test/python__forge__guardrails/experiment.yaml \
  CONCURRENCY=2 JOB_ID=main-03 DETACH=1
```

### 15.5 Verification before any job (recorded at lock time)

- Host tests: `tests/test_component_python_guardrails_v6.py` (`@5`'s payload byte for byte except the version strings and the four test-file ignores; `PLR0904` ignored in exactly those globs and still selected; `@6`'s canaries are `@4`'s plus the test-class canary, every pack entry covered; install and per-step check are `@5`'s; the probe requires `@6`'s pin; an unmodified config copy raises no flag against `@6`'s bytes and `@5`'s `ruff.toml` bytes do; and, with this environment's Ruff, a 21-method test class is refused by `@5` and not `@6` under each glob while production classes are refused by both), `tests/test_gate_effort_v7_scorer.py`, `tests/test_process_compliance_v5.py`, `tests/test_per_trial.py`, `tests/test_coverage_offline.py` (3 trials at concurrency 3 store what concurrency 1 stores; the check matches), `tests/test_prefix_grade.py`, `tests/test_iteration_3b2_consumers.py` (analysis compares `@7`'s and `@5`'s new outcomes only for those versions, each with its better direction).
- `tests/test_guardrails_v6_trial.py` (pack image on OrbStack, no LLM; r2's condition): a scripted agent writes a module and a 22-method `TestCase` under `tests/unit`; `@5`'s first gate run reports `PLR0904` at the test file, `@6`'s does not; under `@6` the probe passes on its pin and the step has no integrity flag.
- `make guardrails-verify` (OrbStack, 2026-10-07): `@1` 76 of 76 rules, `@3` 83 of 83, `@4` 85 of 85, `@5` 85 of 85 and `@6` 86 of 86 (`@4`'s canaries plus `RUFF.LINT.per-file-ignores.test-public-methods`); each mutant fails exactly its disabled rules (`@6`'s mutant also refuses the test-class canary's clean fixture).
- `make quality` passes (1607 tests, coverage 96.4%).
- **The new scorers on `smoke-3b-2`** (2026-10-07, `make experiment-assess ... CONCURRENCY=2` into a scratch store, not the experiment's; the job ran under `2b4c23f42ac9`, so these values are a check of the scorers, not 3b.3 data): 0 error observations; pre-fix grades reused (0 errors). `gate-effort@7`: r1 `final_step_end_blocking_ex_cascade` 56 (against `@6`'s 190; the difference is the 134 `no-untyped-call` findings), r2 0; `total_new_first_run_blocking_ex_cascade` r1 56, r2 35; `first_pass_clean_except_autofix_tests_rate` r1 0, r2 0.25 (cp3 and cp6 refused only `FORMAT` and `TEST.coverage-minimum`). `process-compliance@5`: `compliance_rate` r1 0, r2 1.0 (the gate's runs still count step-wide); `work_session_tests_run_rate` r1 0, r2 0.571 (4 of 7 decided steps; cp3 undetermined), matching the trace review in 14.7.
- Released identities: every registered scorer's implementation digest in any lock is unchanged from `7341a53`, and `python-guardrails@1`-`@5` are untouched; only `python-guardrails@6`, `gate-effort@7` and `process-compliance@5` are new. Only this experiment's `lock_sha256` moved (`2b4c23f42ac9` → `f600b96bc7db`); the toolchain section and the other five locks are unchanged.

### 15.6 Known limits of iteration 3b.3

- Test files have no public-methods limit at all under `@6`; every other selected rule applies to them as before (the test-file ignores are otherwise `@5`'s: `D`, `S101`, `PLR2004`).
- `AUTOFIX_CODES` is a fixed, deliberately small set (layout and import order). Other codes Ruff can fix (for example `UP017`, `F401`) count as design findings in `first_pass_clean_except_autofix`; the set is part of `gate-effort@7`'s identity, so changing it is a new version.
- The work session is the session that received the step's first user prompt. A `continue-on-stall@2` continuation or a `transient-retry@1` retry runs inside that session and so counts as work; a trajectory with no user prompt is read as one session (the step-wide check).
- Offline assessment at `CONCURRENCY` above 1 shares the Docker host between grader containers; a grader timeout under contention would show as an infra failure on that run and a parity difference as `ineligible` (never a 0). `smoke-04`'s coverage check at 2 trials at once matched 16 of 16 steps; 2 is the pre-registered setting.
