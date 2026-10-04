# python__forge__guardrails: rationale and pre-registration

- **Status:** validated and locked (`experiment.lock.json`); **not executed**. This file was written before any job of this experiment. Sections 3 to 6 are the pre-registration. Later additions go in section 9 and never edit sections 3 to 6.
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

Not run yet.
