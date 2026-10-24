<div align="center">

<h1>Raidar</h1>

**Controlled experiments on coding agents: does a treatment (rules, skills, hooks, review loops) change what an agent + model delivers on pinned problems, and which agent does best?**

![Status](https://img.shields.io/badge/status-active-brightgreen.svg?style=flat-square)
![Python](https://img.shields.io/badge/python-3.12-blue.svg?style=flat-square)
![Runtime](https://img.shields.io/badge/runtime-uv%20%7C%20docker%20%7C%20harbor-lightgrey.svg?style=flat-square)
![Primary CLI](https://img.shields.io/badge/cli-raidar-orange.svg?style=flat-square)

</div>

Raidar runs an **experiment**: the same pinned **problems** under two or more **arms** (a condition built from **components**, on an agent), repeated in counterbalanced **attempts**. One execution of a locked experiment is a **job**; every **trial** in it runs through [Harbor](https://pypi.org/project/harbor/) in the runtime pack's Docker image and each checkpoint is graded by a host-only **verifier**. **Scorers** turn stored trials into **Assessments** offline. The analysis reports paired effects against the baseline arm with a verdict of `improved`, `worse`, `inconclusive` or `insufficient`; a benchmark adds a ranking of its agents. The review surface shows the trials and their Quality components.

Raidar uses [Harbor's vocabulary](#concepts) wherever Harbor has a term (job, trial, step result, attempts, verifier, reward, agent name, environment type, metric), and keeps SCB's *problem* and *checkpoint* and its own *experiment*, *arm*, *condition*, *component*, *factor*, *lock* and *verdict*.

The design lives in [`docs/reboot/requirements.md`](docs/reboot/requirements.md) (frozen v1), [`docs/reboot/implementation-plan.md`](docs/reboot/implementation-plan.md), [`docs/reboot/adr-001.md`](docs/reboot/adr-001.md) and [`docs/reboot/design-memo.md`](docs/reboot/design-memo.md).

> **History.** The scenario, revision and matrix workflow was retired at the reboot cutover: no code, target or check reads `scenarios/`, `matrices/`, `environments/` or `spikes/` any more, and they have been removed from the tree. Their definitions are preserved at tag `archive/scenario-epoch`, and [`CHANGELOG.md`](CHANGELOG.md) maps each removed target to its replacement. The review surface no longer reads scenario-epoch history.

## Install

Prerequisites:

- `uv`
- Docker with `docker compose` 2.40.1 or newer, local or on another host. Each machine names its Docker targets in `~/.config/raidar/targets.toml` (`make target-init` writes a commented template); select one with `TARGET=<name>` or the file's `default`. With no file, Raidar uses the current Docker context. A remote target is reached through a supervised SSH-forwarded socket (`make target-up TARGET=<name>`, `make target-status`), and hidden tests and agent credentials go only to targets marked `trusted = true`.
- Node 18 or newer, for the review surface
- Agent credentials: file-backed Codex auth (`make codex-auth-setup`) or `OPENAI_API_KEY` for `codex`; `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` for `claude-code`
- Optional but preferred: the [Infisical CLI](https://infisical.com/docs/cli/overview), logged in once with `infisical login` in a real terminal (it is interactive)

```bash
make env-setup
```

Secrets live in the Infisical project `mac-os-cf` (env `dev`), bound by `orchestrator/.infisical.json`. The repository ships no `orchestrator/.env`. Targets that call a model provider (`experiment-execute`, the smoke targets, `experiment-assess`, `jev-shadow-rubric`, `agent-validate`, `component-probe-trials`) run under `infisical run` when the CLI is installed and logged in; each prints which source it used, never a value. Without Infisical they read `orchestrator/.env` if you created one from `orchestrator/.env.example`, and otherwise say `secrets: none` and use only the shell environment and file-backed Codex auth. `SECRETS=infisical` fails instead of falling back, `SECRETS=dotenv` skips Infisical and fails when `orchestrator/.env` is missing, and `INFISICAL_ENV=` picks another environment. Before any trial starts, `experiment-execute` and the smoke targets check that every selected arm's agent has a credential (presence only) and name the agent field that lacks one.

For Codex CLI the default auth policy is `CODEX_AUTH_MODE=auto`: it prefers file-backed ChatGPT login from `~/.codex/auth.json` and falls back to `OPENAI_API_KEY`. A login kept in the OS keyring cannot be passed into Harbor, so switch Codex to file-backed credential storage first. `make help` lists every public target.

## Concepts

| Term | What it is | Where it lives |
|---|---|---|
| Experiment | The unit of authoring: a `kind`, one runtime, problems, agents, conditions, arms, scorers and the analysis plan. Its id is `<runtime>__<subject>__<intent>` | `lab/experiments/<kind>/<id>/experiment.yaml` |
| Kind | `ab-test` (conditions are compared; `agent.*` factors may be added), `benchmark` (agents are compared under one condition: paired verdicts against a reference agent plus a ranking view with mean ± CI, pass@k, cost and tokens) or `smoke` (the same engine; reports are stamped not citable). `research-loop` is reserved. The kind is part of the lock's execution identity | `kind:` |
| Lock | Content hashes and pinned versions of everything the experiment resolves to, in `execution`, `assessment`, `analysis` and `toolchain` sections. Its `lock_sha256` covers the execution section only, and every job records it. A job refuses a lock that is out of date, and no lock is written for a design its runtime cannot run (`RT0`-`RT6`) | `experiment.lock.json` next to the experiment |
| Runtime and pack | A runtime is a language toolchain and equals a runtime pack id (`python`; `node-ts`, `go`, `rust`, `java`, `dotnet` later). The pack is the agent image (toolchain and pinned agent CLIs, no scorer tools) plus the scorer tool lock | `runtime:`; `orchestrator/src/raidar/subjects/environments/<runtime>/` |
| Problem (binding) | A pinned, verifier-validated task (one-shot or multi-checkpoint) imported against one runtime pack: `scb/<problem>` is the python binding, another runtime's binding would be `scb-<runtime>/<problem>`. Its verifier bundle stays on the host and the agent never sees it | `lab/problems/registry/`, imported into `var/problems/store/` |
| Component | A reusable treatment piece, one module each under `orchestrator/src/raidar/subjects/treatments/catalog/` (the manifest version is the `@N` data): `rules-file@1`, `skills@1`, `lint-config@1`, `stop-hook@1`, `mcp-server@1`, `harness-settings@1`, `workspace-files@1`, `prompt-wrapper@1`, `review-loop@2` (a reviewer session after each work session; verdict feedback is read before or after the `VERDICT` line, with one re-ask on an unusable review), `guardrail-gate@4` (harness-agnostic: after each session the workspace's commit gate, a single command the runtime's guardrails pack supplies, runs in the container, by default as the pre-commit hook of a real `git commit`; a failing gate refuses the commit and starts a fix session with its output, up to `max_rounds`; `mode: shadow` runs the same check once at each step's end against a git directory outside the workspace and records it, never enforcing it; every commit attempt records git evidence; a snapshot of `/app` is taken before each fix session and graded offline; `autofix_before_check` runs the pack's `autofix_command` before each enforced commit check, or on a throwaway copy in shadow mode; it records `gate_clean_at_step_end`, every session's and gate run's duration, and exports `RAIDAR_GATE_REPORT_DIR`), `lint-tool@2` (a hash-pinned Ruff installed per trial in the agent container, outside the agent image, runnable as `ruff`, `python -m ruff` and `uv run ruff`, every invocation probed before step 1), `python-guardrails@8` (the Python guardrails pack: it installs `guardrail-check` once per trial at environment start, root-owned and outside the agent image, with hash-pinned Ruff, mypy, pytest and coverage, protected configs and bespoke checks including a tests check, and puts byte-identical `ruff.toml` and `mypy.ini` copies in the workspace unless `workspace_configs: false`; `guardrail-check --autofix` applies the pack's safe auto-fixers only; the pinned tools are on the agent's `PATH`; its integrity flags (`probes.flags` on each step result) record edited config copies and hook-bypass attempts without failing the step; `make guardrails-verify` runs the pack's own rule tests in the pack image: every implemented rule's violating canary fires and its clean canary passes), `continue-on-stall@2` (a session wrapper: a step's first session that changed nothing and ended asking a question or refusing ("I can't ... without that context") gets one continuation, "no human is available, proceed on the spec and record assumptions in `ASSUMPTIONS.md`"; it composes with a workflow wrapper by running inside it), and `transient-retry@1` (a session wrapper: a session the model provider drops with a transient error, such as "model at capacity", is run again after 60 s and then 180 s, at most twice, with every attempt's logs, usage and error recorded; other errors pass through unchanged). A component is edited in place; the previous release is kept as an `archive/<name>@<N>` tag | `orchestrator/src/raidar/subjects/treatments/` |
| Condition and arm | A condition is a list of components; `shared_components` are put ahead of every condition's own, identically. An arm is one condition on one agent. The factor guard keeps arms differing only in the declared factors | `shared_components:`, `conditions:` and `arms:` |
| Agent | A Harbor agent name (`codex`, `claude-code`), provider, model, kwargs (`reasoning_effort`) and the pinned version, which must equal the pack pin | `agents:` |
| Attempts | The replicates of every arm: each attempt runs every arm once per problem, in a seeded, counterbalanced order. Analysis pairs arms by attempt index | `analysis.attempts` |
| Job | One execution of a locked experiment: its trials, their step results, validity checks and N1 phase timings | `var/jobs/<experiment id>/<job id>/` (`job.json`, `job.lock.json`, `trial-records/`) |
| Trial and step result | A trial is one arm × attempt × problem through every checkpoint (a Harbor trial, id `bNN-pN-<arm>`, where `bNN` is the attempt index); a step result is one checkpoint of it | `trials/<trial>/`, `trial-records/<trial>.json` |
| Verifier and reward | The hidden, trusted grader: strict all-cases per checkpoint, run host-side; its per-step output is the reward. Harbor's `oracle` agent is something else (it replays reference solutions) | `orchestrator/src/raidar/grading/` |
| Scorer and Assessment | A versioned scorer (`correctness@1`, `code-quality@3`, `resource-usage@1`, `process-compliance@6`, `gate-effort@10`) runs offline over stored trials. `process-compliance@6` decides only from the trace: a check it cannot decide is `insufficient_evidence` with an `undetermined: ...` reason, never a value or a zero. `gate-effort@10` reads each step's `guardrail-gate` record: rounds to clean, first-pass clean, unresolved (fix rounds exhausted) and gate-error steps, blocking findings per run by rule family (complexity, typing, defects, structure, style, naming, tests, suppressions, other) and the fix sessions' tokens, cost and time; an arm without the gate is `not_applicable`, never 0. `gate-effort@10` also measures a shadow gate (`guardrail-gate` `mode: shadow`): its first-run outcomes (first-pass clean, gate error, first-run blocking findings by family) pair across a shadow baseline and an enforced treatment, and its fix and enforcement outcomes are `not_applicable`. A step with no source change is left out of first-pass clean. It reports the tests check (first run and step end, owned coverage) and `fix_round_correctness_delta`, the final minus the pre-fix case pass rate of steps with a fix round; blocking counts that compare across gate modes (`total_new_first_run_blocking`, `final_step_end_blocking`) with the mypy `no-untyped-call` cascade apart and failing tests split from missed coverage; first-pass clean rates that ignore the auto-fixable layout codes (`FORMAT`, `I001`, `W291`-`W293`), the tests check, or both, and cross-mode counts without that cascade (`*_ex_cascade`) or with a refused suppression comment counted once (mypy's `unused-ignore` at a `SUPPRESS` line folded into it; `*_net`); `guardrail-gate`'s autofix step (passes, errors, files and lines changed, findings fixed by family), first-pass clean and blocking counts after autofix and before it, the fix sessions' cost and wall time by the kind of refusal that started them, and the autofix lines and findings each step's own work added (`new_autofix_*`), which compare across a shadow and an enforced gate. `process-compliance@6` adds `stall_rate` from `continue-on-stall`'s records, counts a test command only when its runner was found, checks the work session's own test runs (`work_session_tests_run_rate`; a gate's fix sessions do not count), counts `plan_written` in `compliance_rate` only when the condition asks for a plan, and reports whether the work session ran the tests at all (`work_session_tests_run_any`) and after its last edit to a non-test file (`work_session_tests_after_last_code_edit`). `code-quality@3`'s regression breadth (G) reads an offline coverage run when the trial deferred the verifier's coverage pass (`protocol.verifier_coverage: offline`), and leaves out at most three grader cases whose outcome flipped between the grading and the coverage run instead of reading the step as ineligible (`regression_parity_excluded_cases`). Each result is an Assessment keyed by its inputs | an Assessment store directory, outside the job |
| Headline metric | How component scores combine into the headline S (`scb-canonical@1/@2`, `scb-canonical-oneshot@1/@2`). The lock pins it | `assessment.metric` |
| Analysis | Paired deltas, confidence intervals, curves, verdicts, exclusions and a sample-size projection against the baseline arm; a benchmark adds its ranking view | the `make experiment-analyze` output directory |
| Jev | **Dormant (parked 2026-10-04).** Both policies are `off`; Jev is out of scoring, analysis and the review surface's Quality page, which shows a trial's past decisions read-only. Model judgement is used only when a needed measure cannot be made deterministically, and none qualifies today (requirements change log, F7). Un-parking is a new policy version and a new user decision. The machinery below is kept intact. Decomposed LLM-judge questions. They run in shadow until a policy is calibrated and promoted: label decisions (review surface, or blind with `make jev-export-blind`/`jev-import-blind`; each label records a `human` or `model` reviewer; a blind labeller gets the export alone, in a directory that holds nothing else, never beside a round's selection files, which carry Jev's scores), then `make jev-promote` fits and freezes the threshold on a calibration split and writes `mode: enforce` only when the audit re-derives every recorded number and, unless `ALLOW_SCORER_BUMP=1`, the scorer's minor-version bump the promotion names already exists; `make jev-verify-promotion` requires that bump | `raidar jev ...`; the audit sits in the Assessment store |

Inside each Harbor trial directory the verifier keeps its original evidence names (`raidar-oracle/steps/<step>/record.json`, reward key `oracle_leak`): they are wire names that released scorers and `make assess-regrade` read the same way for every job. For the same reason released scorers keep the observation subject `trajectory`, the evidence input `oracle.cases` and their `@1` descriptions (all hashed into Assessment keys), and the problem store keeps each problem's `oracle/` bundle directory (inside the problem's pinned digest).

## Walkthrough: experiment.yaml to review

The example is an `ab-test` comparing a baseline with a rules-file treatment on the validated one-shot problem the pilot uses. Every step is a public `make` target.

**1. Provide the problem and the pack.** A problem is pinned by reference and must be verifier-validated before any job can use it. `make problem-list` shows every registered problem with its validation and import state, and `make problem-info PROBLEM=<ref>` shows one in detail. `problem-validate` is needed only for a problem without a validation report in `lab/problems/registry/`.

Validation also runs a **spec-determinacy audit** (`spec_determinacy` in the report): verifier cases whose assertions the checkpoint's specification does not determine (an error detail's wording or exact content, the output's byte format) are flagged by static rules over the test code plus grading of spec-conformant variants of the reference solutions; `AUDIT_ONLY=1` reruns just the audit into the committed report. Flagged cases can be registered in a pinned **errata** (`lab/problems/registry/<dataset>/<problem>/<version>.errata.yaml`, drafted by `make problem-errata-draft`): `exclude-from-solved` cases leave only the errata-applied solve verdict. The lock pins the errata's sha256 (it is part of `lock_sha256`), the verifier records the errata-applied verdict beside the raw SCB grade, and `correctness@1` and the analysis report both (`solve_rate_errata`, `reached_errata`, `final_solved_errata` beside the raw outcomes, which stay primary unless the experiment pre-registers an errata-applied one with `analysis.primary_outcome`; the raw counterpart is then reported beside it).

```bash
make problem-list
make problem-import PROBLEM=scb/circuit_eval.checkpoint_1@38d627ecf668
make pack-build
```

**2. Write `experiment.yaml`.** `make experiment-init EXPERIMENT=lab/experiments/ab-test/python__circuit_eval-cp1__rules-trial/experiment.yaml` writes a starter of the shape below and a stub `components/AGENTS.md`: the id comes from the directory (or `EXPERIMENT_ID=`), the problem from `PROBLEM=` (default the one below), the agent's `version` from the pack pin, and the headline metric is left to the lock, which picks it by checkpoint count. It refuses a path that exists. Paths in `files:` are relative to the experiment file. To try another variant of a treatment later, add a condition and an arm to the same file, so the variant stays paired with the baseline arm.

```yaml
schema_version: 2
kind: ab-test                # ab-test | benchmark | smoke
id: python__circuit_eval-cp1__rules-trial   # <runtime>__<subject>__<intent>
description: Does an AGENTS.md rules file change correctness on circuit_eval?

problems:
  - ref: scb/circuit_eval.checkpoint_1@38d627ecf668

runtime: python              # one runtime pack per experiment

agents:
  - id: codex-luna
    name: codex              # Harbor agent name
    provider: openai
    model: gpt-6-luna
    reasoning_effort: low
    version: 0.156.1         # must equal the pack pin

factors: [condition]

conditions:
  - id: baseline
    components: []
  - id: rules
    components:
      - use: rules-file@1
        files: [components/AGENTS.md]

arms:
  - id: baseline
    condition: baseline
    agent: codex-luna
  - id: rules
    condition: rules
    agent: codex-luna

assessment:
  scorers: [correctness@1, code-quality@3, resource-usage@1]
  metric: scb-canonical-oneshot@1

analysis:
  baseline_arm: baseline
  attempts: 5
  seed: 20260930
  min_pairs: 5               # the lock pins these verdict thresholds
  max_exclusion_fraction: 0.2
  alpha: 0.05
```

Validation checks that the design does not contradict its kind (`KIND*`: an ab-test compares conditions; a benchmark compares agents under one condition, warns below 5 attempts and may set `analysis.rank_by`; `research-loop` is reserved) and its name (`NAME1`: a file under `lab/experiments/<kind>/<id>/` sits in the directory of its kind and id; `NAME2`: the id starts with the experiment's runtime).

**3. Validate and lock.** Validation checks the schema, the cross-references, the kind and naming rules, the factor guard, and that every arm's components compile for its agent. The lock pins content hashes and versions and runs the runtime-compatibility gate, reporting every issue at once: each problem binding must be validated (`RT0`) and bound to the experiment's runtime (`RT1`; a local `path:` task declares `[metadata] raidar_runtime`), imported against the current pack contract (`RT2`); every component must cover the runtime and support the arm's agent (`RT3`), every scorer and the headline metric must cover the runtime (`RT4`), each agent's version must be the pack pin (`RT5`), and the runtime must have a pack (`RT6`). The gate sees the effective spec, so `--set` overrides cannot bypass it. `raidar run` refuses to start if relocking would change the lock (naming each stale section) or if the pack now builds another image than the locked one (`RT6` drift). A scorer, headline-metric, threshold or toolchain change relocks to the same `lock_sha256`, so existing jobs still match: rescore them (step 5) instead of rerunning. A change to the kind, problems, agents, conditions, protocol, attempts or seed gives a new `lock_sha256`, which means a new experiment. `make experiment-locks-check`, part of `make quality`, checks every lock under `lab/experiments/`.

```bash
E=lab/experiments/ab-test/python__circuit_eval-cp1__rules-trial/experiment.yaml
make experiment-validate EXPERIMENT=$E
make experiment-lock EXPERIMENT=$E
make experiment-plan EXPERIMENT=$E     # dry run: every trial and its hashes
```

**4. Run a job.** Each trial runs through Harbor (the pack image is built first if missing). The job directory `var/jobs/<experiment id>/<JOB_ID>` records step results, validity checks (context isolation, no test leak, progression), N1 phase timings and `job.lock.json`, the lock the job executed under. Agent processes are reaped when each agent phase ends, before its work is collected and graded.

```bash
make experiment-execute EXPERIMENT=$E JOB_ID=first
make perf-check JOB=var/jobs/python__circuit_eval-cp1__rules-trial/first     # N1 overhead budgets (report-only)
```

On a real job, `perf-check` is report-only. It lists each phase's p95 against its accepted budget and prints breaches as warnings: over budget, `insufficient` (fewer than `MIN_SAMPLES` samples, default 10), or `missing`. It also prints the job's contention context: the configured concurrency, the peak number of trials running at once, and any other Raidar jobs under the same jobs root that overlapped in time. It exits 0 on breaches and non-zero only on malformed or empty input. `PERF_STRICT=1` makes breaches fail. For a small job, pass `MIN_SAMPLES=<trials>` to judge the budgets on what there is.

The enforced overhead check is `make perf-gate`. It runs the no-LLM fixture experiment (`make perf-fixture`) at concurrency 1 and then 3 and fails on any breach of the accepted budgets. It needs Docker, so it is a release/gate check and is not part of `make quality`.

A long job must outlive the shell or agent session that starts it, so launch real-LLM jobs detached and poll them to completion. `DETACH=1` starts the job under a supervisor in its own session (kept awake with `caffeinate -i` on macOS) and returns at once. The supervisor's pid, the job's log and its exit record are in `<job dir>/detach/`. `make experiment-status` reports `state: running`, `finished`, `failed` or `died`, the trials recorded against those selected, and the log's last lines. It reads any job directory and never changes it.

```bash
make experiment-execute EXPERIMENT=$E JOB_ID=second DETACH=1
make experiment-status JOB=var/jobs/python__circuit_eval-cp1__rules-trial/second
```

If a job is stopped mid-trial, `make harbor-cleanup JOB=var/jobs/python__circuit_eval-cp1__rules-trial/first` removes the trial environments it left behind.

**5. Assess.** Scorers run offline over the stored trials. They write to an Assessment store outside the job and use the assessment settings of the job's lock: the experiment's current lock when its `lock_sha256` is the job's (so an assessment-only relock applies), else the job's `job.lock.json`; `LOCK=` names another. Scorers given on the command line must cover the job's runtime, and a `--bundle` must be the verifier bundle of a problem the lock binds (`AS1`). `REPLAY=1` reruns existing Assessments and requires byte-identical output. With `gate-effort@10`, assessment first grades every not-yet-graded `guardrail-gate@3`/`@4` pre-fix snapshot into the store with the step's own verifier bundle, on the Docker target; `make assess-regrade ... STORE=` regrades them beside the final snapshots. With `code-quality@3`, assessment first makes the coverage run of every step whose trial deferred it into the store, on the Docker target; `make assess-regrade ... COVERAGE_CHECK=1` makes the runs again and compares them with the trial's own coverage pass or the stored run. `CONCURRENCY=N` makes the pre-fix grades and coverage runs of N trials at once (each trial's steps in order; default 1). `process-compliance@6` (the declared default) never asks a model. Everything under `var/` (jobs, stores, analysis) is git-ignored.

```bash
make experiment-assess JOB=var/jobs/python__circuit_eval-cp1__rules-trial/first STORE=var/stores/rules-trial \
  SCORERS="correctness@1 code-quality@3 resource-usage@1"
```

**6. Analyze.** The verdict thresholds and the assessment settings come from the lock. The report labels any command-line override. A trial whose inputs cannot be loaded is refused, never read as unassessed; if the problem store moved, pass `BUNDLE_SEARCH=<dir>` to find verifier bundles by digest. A benchmark's report adds the ranking view; a smoke report says it is not citable.

```bash
make experiment-analyze JOBS="var/jobs/python__circuit_eval-cp1__rules-trial/first" STORE=var/stores/rules-trial ANALYSIS_OUT=var/analysis/rules-trial
```

**7. Review.** Project the Quality element from the jobs and the store, then serve the surface with the same jobs; serve builds the UI first if `review-surface/dist` is missing (`make review-surface-build` rebuilds it). Each job's lock is found beside its experiment file or in the job directory; pass `REVIEW_LOCK=<lock>` (also used by the Quality projection, or `QUALITY_LOCK=`) for a lock kept elsewhere, such as a smoke lock under `var/tmp/smoke/`. A job whose lock cannot be found is skipped with a warning. `make review-surface-test` rewrites `review-surface/data` with its fixtures plus the jobs you pass, so run the projection again before serving.

```bash
make review-surface-quality-data QUALITY_JOBS=var/jobs/python__circuit_eval-cp1__rules-trial QUALITY_STORE=var/stores/rules-trial
make review-surface-serve REVIEW_JOBS_ROOT=var/jobs/python__circuit_eval-cp1__rules-trial REVIEW_STORE=var/stores/rules-trial
```

## Smoke checks

The smoke targets run real agent trials through the same experiment path: `lab/experiments/smoke/python__circuit_eval-cp1__agent-health/` (kind `smoke`) and the agent benchmark `lab/experiments/benchmark/python__circuit_eval-cp1__agents/` (one arm per pack agent). Each target locks its experiment under `var/tmp/smoke/` with its own overrides and writes jobs under `var/jobs/`.

```bash
make orchestrator-smoke                                   # codex + gpt-6-luna (low), one trial
make agent-smoke AGENT=claude-code PROVIDER=anthropic MODEL=claude-sonnet-4-6
make smoke-agents                                         # one trial per pack agent (arms codex, claude)
make smoke-agents SMOKE_ARM=codex                         # one arm only
```

Before a non-smoke job runs, `make experiment-execute` looks for a clean smoke job on the same code, pack, agents and target, and refuses without one (`ALLOW_UNSMOKED=1` overrides; recorded in `job.json`). Clean includes that the agent changed `/app` in every executed step of every trial, so a smoke where the agent wrote nothing does not count. `lab/experiments/smoke/python__forge__mechanics/` is the multi-checkpoint forge smoke for that purpose.

## Run health, pooling and the fault ledger

Every trial writes `health.json`: verdict, record, limit, liveness, contention and tamper checks, each `pass`, `violation` or `unmeasured`. A verdict, record/limit or tamper violation invalidates the trial; `make health JOB=<job dir>` recomputes it, and violations land as provisional lines in `<job>/fault-candidates.jsonl`. A host-side liveness monitor (`RAIDAR_LIVENESS=enforce` by default; `observe` and `off` opt out) watches each agent phase for output, CPU or network: an agent quiet for 600 s is killed and the step is verified and graded on what exists, like an agent timeout, so the trial stays valid and the kill is a provisional fault candidate (calibration: ADR-002 A.9).

`job.json` records the runner commit and toolchain hash, pack and grader recipe digests, effective limits and the execution target. `make experiment-analyze` pools several jobs only when these match (`POOL_OVERRIDE=<field>[,<field>]` lifts a named refusal, recorded in the analysis; a records-manifest mismatch never lifts).

`lab/ledger/` tracks fault categories (`faults/FCR-NNN.md`) and remedies (`remedies/M-NNN.md`); `make ledger-index` regenerates `lab/ledger/README.md` and `make ledger-check` (part of `make quality`) validates the records.

Make variables follow the vocabulary: `JOB`, `JOBS`, `JOB_ID`, `JOBS_DIR`, `AGENT`, `AGENT_VERSION`, `ATTEMPT`, `SMOKE_ATTEMPTS`, `VERIFIER_BUNDLE`, `REVIEW_JOBS_ROOT`, `QUALITY_JOBS`, `ALLOW_DEV_JOBS`. A variable under its old name (`RUN=`, `HARNESS=`, `BLOCK=`, ...) fails with its new name.

## Repository layout

- `orchestrator/`: the `raidar` CLI and library, grouped by the stage of an evaluation each part serves (`orchestrator/src/raidar/`): `cli` (commands), `contracts` (shared records: job layout, step snapshot, review contract, repository paths), `design` (`experiments/`: schema, lock, compatibility gate; `problems/`: registry, import, store), `subjects` (what is evaluated: `agents/`, `treatments/` = components with `definition/`, `runtime/`, `catalog/` and `payloads/`, and `environments/` = runtime packs), `engine` (running a locked experiment: `targets/`, `jobs/`, `trials/`, `health/`, `perf/`), `grading` (the hidden verifier), `scoring` (`framework/`, one package per scorer, and `judge/`) and `evaluation` (`analysis/`, `review/` with `projection/` and `quality/`, and `ledger/`).
- `lab/`: everything tracked that defines experiments. `lab/experiments/<kind>/<id>/` holds experiment definitions with their locks and component files (the smoke experiments and the agent benchmark; files under `lab/experiments/**/components/` are treatment content delivered to agents), `lab/problems/` the pinned problem registry (`sources.yaml`, `registry/`, `fixtures/`), and `lab/ledger/` the fault and remedy ledger.
- `var/`: generated data, ignored by git; `RAIDAR_VAR=<dir>` relocates it. `var/jobs/`, `var/stores/` and `var/analysis/` hold job directories, Assessment stores and analysis output, `var/problems/store/` and `var/problems/cache/` the imported problem content and source checkouts, `var/tmp/` scratch (smoke locks, review fixtures), and `var/archive/` local data from before the restructure.
- `review-surface/`: the review UI, its frozen projection contract and its Playwright suite.
- `docs/reboot/`: the requirements, plan, ADR, overhead baseline, design memo, the Phase 1 gate packet, and the tracked Phase 0 spike evidence (`evidence/`).

No version appears in any file, module, class or folder name: a scorer or component is one module edited in place, its version is the `@N` in its manifest, and `make release-archive REF=<name>@<N>` tags the release being replaced as `archive/<name>@<N>` before the edit lands. Older experiments and jobs are not carried forward (tag `archive/pre-structure-reset` preserves them).

`make quality` is the repository gate: `make structure-check` (layout rules: no versions in names, root and package allowlists, package depth), `make smoke-dry-run-check` (the smoke command shapes), `make experiment-locks-check` (every `lab/experiments/*/*/*.lock.json` is current), `make ledger-check`, then ruff, import contracts, lizard, mypy over `orchestrator/src/raidar`, fan-out, and the full test suite with coverage. The pre-push hook runs `make quality`. Docker-backed tests run when a Docker target is ready; the N1 overhead gate (`make perf-gate`) is run separately as a release/gate check.
