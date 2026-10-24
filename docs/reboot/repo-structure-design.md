# Repository structure, primitives and versioning

- **Status:** accepted 2026-10-24 with these user decisions: keep `orchestrator/` (no rename); `lab/` and `var/` adopted; `spikes/` deleted; package layout §4.2 approved as proposed; and **a clean start**. Once the restructure is signed off (no functional regression, verification and code reviews green), jobs and artefacts start afresh as v1. Old experiments, jobs and stores are not carried forward: finished and closed experiment definitions leave the tree (they are preserved at `archive/pre-structure-reset`), and old jobs, stores and analyses move to `var/archive/pre-v1/`. The schema-v1/legacy-job migration code and the dormant `process-compliance@1` are deleted. §3.4 ("closed, not relocked") is therefore not built.
- **Date:** 2026-10-24
- **Inputs:** the version inventory and structure audit of 2026-10-24 (session reports `version-map.md`, `structure-map.md`), and how factory-x-loops (fxl) stores versions (`docs/references/workflows.md:114-153`, `.agents/skills/workflow-experiments/references/change-workflow-or-engine.md:79-135` in that repo).

## 1. Problem

The tree grew by accretion:
- **About 75 version-named modules.** Each release of a scorer or component was a new sibling file (`continue_on_stall_v2.py`, `guardrail_gate_v2…v4.py`, `builtins/guardrails_v2…v8.py`, `scorers/gate_effort_v2…v10/`, `process_checks_v3…v6/`, `code_quality_v2/v3/`), each subclassing the one before. There are also 7 near-identical payload trees (`payloads/python_guardrails_1…8`, about 12k lines) and about 30 test files, one per version. No tracked experiment pins two versions of one family, so none of the coexistence is used as a control.
- **Top-level `raidar` packages are not grouped by any rule.** There are 21 packages plus loose root modules (`codex_auth.py`, `job_layout.py`), several one-module packages (`harness/`, `oracle/`, `schemas/`), a flat 26-file `components/` that mixes definition code with code that runs inside trials, and a `commands/` layer that holds some domain logic.
- **The repo root mixes tracked source, tracked lab content and gigabytes of generated or abandoned data:** `jobs/` 3.3G, `stores/` 220M, `analysis/`, `spikes/` 2.5G, `problems/store`, `problems/cache`, `experiments/operational_smoke/`.

## 2. Primitives

Raidar's own vocabulary (requirements, ADR-001/002) has these primitives, in lifecycle order:

| Stage | Primitive | Meaning |
|---|---|---|
| define | **problem** | a task with hidden tests and a grader (`scb/forge@<pin>`) |
| define | **runtime pack** | the language image and tools that trials run in (`python`) |
| define | **agent** | a harness, model and effort (codex `gpt-6-luna` low) |
| define | **component** | one treatment unit given to an agent (rules file, gate, loop) |
| define | **experiment** | conditions × arms × problems × attempts, plus scorers and metric; frozen by a **lock** |
| run | **target** | the Docker daemon trials run on |
| run | **job → trial → step** | one execution of a lock; a trial is one arm × attempt, a step is one checkpoint |
| verify | **grade** | the host-side verdict of a step from the hidden tests |
| assess | **scorer → assessment (in a store)** | a deterministic measure of a trial, kept in an add-only store |
| analyse | **analysis** | paired deltas, verdicts and the pooling gate over jobs |
| review | **review surface** | the UI and its projection contract |
| learn | **ledger** | fault categories and remedies |

Folder names should be these words and nothing else.

## 3. Versioning policy

**Rule: no version in a file, module or folder name.** A version is data: `name@N` in a manifest, `schema_version`, `scb-canonical@2`.

1. **Code (scorers, component runtimes, packs): one module per primitive, edited in place.** A behaviour change bumps the manifest's `@N`. The release being replaced is preserved by an annotated git tag `archive/<name>@<N>`, created by `make release-archive REF=<name>@<N>` before the edit lands. This matches fxl, which never keeps Python implementations side by side: its engine code is pinned through git provenance, and comparability is guarded by revision checks. Raidar already records the runner commit and toolchain hash and refuses to pool across them (ADR-002 A.6).
2. **Control vs treatment never needs two code versions at once.** Arms of one experiment share every scorer. Component variants that must run side by side are expressed as `with:` options on one implementation; guardrails r1 vs r2 already worked this way (`mode: shadow|enforced`).
3. **Data that must coexist goes in fxl-style folders** (`<name>/v<N>/`). These are hand-edited, immutable once measured, and allowed only for payload or config data, never for code. Raidar's natural place for them is the experiment's own `components/` directory, which already holds per-condition payloads. A shared copy goes in `lab/components/<name>/v<N>/` only when two experiments need it. Nothing uses this today.
4. **Finished experiments are closed, not relocked.** `experiment.yaml` gains `status: open|closed`. A closed experiment keeps its definition and lock byte for byte. `experiment-locks-check` skips it, `experiment-execute` refuses it, and its jobs are rescored or re-analysed at the archive tag named in its rationale. This replaces "every old version stays registered so old jobs stay rescorable".
5. **Enforcement:** `make structure-check` joins `make quality`. It refuses (a) any tracked path matching a version suffix (`_v\d+`, `-v\d+`, `\bv\d+\b`), except `v<N>/` data folders under `components/`; (b) top-level directories and `raidar` packages not on the allowlist in §4; (c) package depth over 3 below `raidar/`.

## 4. Target layout

### 4.1 Repository root

```
raidar/                     tracked Python project (was orchestrator/): pyproject, src/raidar, tests
review-surface/             tracked UI (unchanged)
lab/                        tracked research content
  experiments/<kind>/<id>/  experiment.yaml, lock, components/ (open and closed)
  problems/                 sources.yaml, registry/, fixtures/
  ledger/                   faults/, remedies/, README.md (generated)
docs/  scripts/  .agents/  .github/
var/                        ALL generated data, git-ignored, relocatable with RAIDAR_VAR=<dir>
  jobs/  stores/  analysis/  problems/{store,cache}/  tmp/  cache/tools/   (tmp replaces .tmp/)
Makefile  README.md  AGENTS.md  CHANGELOG.md
```

Deleted: `spikes/` (no code, Makefile or CI references it; findings live in `docs/reboot/evidence/`), `experiments/operational_smoke/`, `scripts/oracle-browser-run.sh`, and the stale `runs`/`workspace*`/`results` excludes.

### 4.2 `raidar` packages, grouped by stage

Top level stays flat: one package per primitive, no stage wrapper packages, so import paths stay short.

```
raidar/
  cli/          the click entry point and command handlers ONLY (was cli.py + commands/; logic in
                commands/{assess_grading,repo_state,run_checks}.py moves to its domain package)
  core/         shared value types used across stages: job layout, step snapshot (was oracle/snapshot),
                review contract (was schemas/review)
  problems/     (unchanged)
  packs/        runtime packs (unchanged)
  agents/       adapters/, harness.py (was harness/definitions.py), codex_auth.py
  components/   definition/ (registry, manifest, compiler, compiled, integrity, contribution, evidence)
                runtime/   (code that runs in or around a trial: gate, review_loop, continue_on_stall,
                            transient_retry, session_chain, provision, autofix, mcp_shim, setup_script)
                catalog/   (the built-in component manifests, was builtins/; one file per component)
                payloads/  (one data tree per component, no version suffix)
  spec/         experiment schema, validation, lock (schema-v1 loader removed)
  targets/      (unchanged)
  execution/    plan, driver, trial, environment, cleanup, reaper, guard
                health/ (health, liveness, admission, provenance, fault candidates)
                perf/   (phases, budgets, perf report)
  verifier/     grader, sandbox, regrade, records, outcomes; runtime/ (in-image pytest plugin)
  scorers/      catalog, sdk, one package per scorer: correctness, code_quality, process_checks,
                gate_effort, resource_usage
  analysis/     (unchanged, plus pooling)
  review/       projection/ (was review_projection), quality/ (was review_quality)
  ledger/       (unchanged)
  jev/          dormant (user decision 2026-10-04), unchanged
```

Removed: the legacy schema-v1 loader and legacy job migration (`spec/schema_v1.py`, `execution/job_migration.py`, `execution/errata_migration.py`, the `LEGACY_*` helpers, `experiment-upgrade`/`experiment-migrate-job`), `scorers/code_quality/sensitivity.py` (no importer), the empty `agents/harbor_agents/`, and the dormant `process-compliance@1` scorer (`scorers/process_compliance/`), which shares its id with the surviving `process-compliance@6`.

### 4.3 Version collapse (survivor = latest)

| Family | Survivor | Unversioned home |
|---|---|---|
| continue-on-stall | @2 | `components/runtime/continue_on_stall.py`, `components/catalog/stall.py` |
| review-loop | @2 | `components/runtime/review_loop.py`, `catalog/workflow.py` |
| guardrail-gate | @4 | `components/runtime/{guardrail_gate,gate_run,autofix}.py`, `catalog/gate.py` |
| python-guardrails | @8 | `components/catalog/guardrails.py`, `runtime/provision.py`, `payloads/python_guardrails/` |
| lint-tool | @2 | `components/catalog/tools.py` |
| code-quality | @3 | `scorers/code_quality/` |
| process-compliance | @6 | `scorers/process_checks/` |
| gate-effort | @10 | `scorers/gate_effort/` |

Each inheritance chain is flattened into its one module. Tests that cover only an old version are deleted, and the survivor's tests take the unversioned name.

## 5. Consequences

- **One declared re-key** (like ADR-002 step −1): every scorer implementation digest and every component fingerprint changes, and every open lock is relocked. Closed experiments keep their locks (§3.4).
- **Open experiments re-pinned to survivors:** `code-quality@1 → @3` and `process-compliance@2 → @6` in guided-pilot ×2, guided-r3 and the forge smoke. @3 reads coverage like @1 and differs only on parity failures; @6 adds diagnostic metrics the headline does not read. **Proposed:** close the three pilot experiments instead, since they are finished Phase 1 work, which leaves no re-pin.
- **Regression proof:** a fresh-store rescore of guardrails main-04 with the collapsed `code-quality`, `process-compliance` and `gate-effort` must reproduce the stored `result` of `@3`/`@6`/`@10` exactly (48 assessments). It runs at the new code against the closed lock's scorer refs. A smoke on the collapsed components must have a healthy, valid trial.
- **Moving data to `var/`:** jobs are moved, not copied (same filesystem). Ledger evidence refs and docs that say `jobs/…` are rewritten to `var/jobs/…`. Recorded absolute paths inside old job files stay as they are.
- **Archive tag before any of this:** `archive/pre-structure-reset` at the pushed head of `reboot-impl`.

## 6. Decisions for the user

1. Rename `orchestrator/` → `raidar/` (recommended), or keep `orchestrator/`.
2. Group tracked research content under `lab/` (recommended), or keep `experiments/`, `problems/`, `ledger/` at the root.
3. Put generated data under a single `var/` (recommended), or keep `jobs/`, `stores/`, `analysis/` at the root.
4. Close the finished pilot experiments, which avoids re-pinning (recommended), or re-pin them to the survivors.
5. Delete `spikes/` (2.5G, untracked) and the schema-v1/legacy-job migration code (recommended).

## 7. Implementation record (2026-10-24)

Built on `reboot-impl` in 54 commits after `archive/pre-structure-reset`: 726 files changed, +12.8k/−53.6k lines. `orchestrator/src` now has 379 tracked files and `orchestrator/tests` 152.

- **Wave 1:** package moves (§4.2) and deletions (schema-v1 loader, legacy job migration, dormant `process-compliance@1`, dead modules).
- **Wave 2:**
  - every version chain flattened into one module (§4.3);
  - `lab/` and `var/` adopted (`RAIDAR_VAR`), with `make structure-check` in `make quality` and `make release-archive`;
  - the finished experiments removed, and `smoke/python__forge__mechanics` renamed and re-pinned to `code-quality@3` and `process-compliance@6`.
- **Review:** ten slice reviews (execution, run-health, targets/perf, verifier, two for scorers, components, spec/problems/packs/agents, analysis/review/UI/CLI, and docs/build) fixed what they found. The main fixes:
  - leak-guard bypass via escaped `sha256sum` paths;
  - malformed grader ledger crashing `verify()`;
  - enforce-mode kill verdict lost on cancel;
  - records-manifest blind to symlinked directories;
  - root-shell injection via the agent user name in the reaper and liveness scripts;
  - unchecked `compose down` failures;
  - a recycled pid read as a running supervisor;
  - neutral-direction metrics given verdicts;
  - gate-effort failing on an ungated reached step;
  - review-loop rejecting session-wrapper inner agents;
  - placeholder errata accepted;
  - a review-surface static path escape;
  - SSH option injection through the target host.
- **Regression proof:**
  - the old `@3`/`@6`/`@10` scorers and the collapsed ones give identical results on the archived guardrails `main-04`: 32 payloads, about 9.8k observations;
  - `make quality` exits 0 (1784 passed, 12 skipped, coverage 96.20%);
  - `make perf-gate TARGET=mbp` passes strictly against the accepted `mbp` set;
  - `make orchestrator-smoke` solves;
  - the forge smoke `v1-forge-01` (8 checkpoints) is valid with 0 health violations, and assess plus analyze complete on it.

## 8. Round 2: descriptive groupings inside `raidar/` (proposal, 2026-10-24)

**Problem.** `orchestrator/src/raidar/` has 15 top-level packages named on three different axes:
- architectural layer: `cli`, `core`;
- domain noun: `agents`, `components`, `packs`, `problems`, `targets`, `scorers`, `spec`, `ledger`, `jev`;
- activity: `execution`, `review`, `analysis`, `verifier`.

A reader cannot tell from the names where a stage of Raidar's process lives. factory-x-loops groups by role in its process (`capability/{define,verify,judge,accept}`, `engine/{lifecycle,supervision,workflow}`, `evaluation/{analysis,health,journal,replay}`) and has 6 top-level packages.

**Rule.** The top level names the stages of an evaluation, in order, plus the interface and the shared contracts. The second level names the thing that stage handles. Every name says what the code does for an experiment, never its architectural layer.

```
raidar/
  cli/            interface: click commands only (unchanged)
  contracts/      shared records every stage reads/writes: job layout, step snapshot, review contract,
                  repo/var paths, runtime ids                                  (was core/)
  design/         declaring an experiment
    experiments/  schema, validation, lock, compatibility gate, metrics       (was spec/)
    problems/     registry, import, store, validation, errata                 (was problems/)
  subjects/       what is evaluated
    agents/       adapters, harness ids, codex auth                           (was agents/)
    treatments/   component definition, catalog, in-trial runtime, payloads   (was components/)
    environments/ runtime packs: image, provisioner, drift                    (was packs/)
  engine/         running a locked experiment
    targets/      Docker targets, transport, trust gate, job guard            (was targets/)
    jobs/         plan, run, detach supervisor, job manifest, progression     (was execution/ job modules)
    trials/       driver, environment, materialise, collect, cleanup, reaper  (was execution/ trial modules)
    health/       run-health audit, liveness, admission, provenance           (was execution/health/)
    perf/         phases, budgets, perf report                                (was execution/perf/)
  grading/        the hidden-test verdict of each step                        (was verifier/; runtime/ plugin kept)
  scoring/        measuring trials offline
    framework/    sdk, catalog, store, rescore, keys, trial inputs            (was scorers/*.py, scorers/sdk/)
    correctness/, code_quality/, process_checks/, gate_effort/, resource_usage/   one package per scorer
    judge/        dormant model judgement                                     (was jev/)
  evaluation/     turning scores into findings
    analysis/     paired effects, verdicts, pooling gate                      (was analysis/)
    review/       projection and quality views for the review surface         (was review/{projection,quality})
    ledger/       fault categories and remedies                               (was ledger/)
```

There are 8 top-level packages instead of 15, and no package sits deeper than 3 below `raidar/`. `scoring/code_quality/snapshot_tool` is at depth 3, and `subjects/treatments/payloads/**` is data.

**Consequences.**
- **Pure moves, with one declared re-key.** Scorer implementation digests change because module paths change, and component fingerprints change because payload and fingerprint paths change. Relock the three experiments; no jobs are carried over (clean start).
- **Config and checks to update:** import-linter contracts are rewritten per stage, keeping the same edges (`contracts` imports no stage, `scoring` never imports `engine`, `grading` never imports `subjects`). `make structure-check` gets the new package allowlist with second-level allowlists. Also update the entry points, `scoring/framework/catalog` dotted strings, Makefile `python -m` paths, and AGENTS/README.
- **Proof:**
  - `make quality`;
  - a scorer-parity rescore of the round-1 forge smoke `v1-forge-01` (old store vs new code, identical results);
  - `make perf-gate TARGET=mbp`;
  - `make orchestrator-smoke`.

## 9. Implementation record: round 2 (2026-10-24)

Built on `structure-groupings` (from `origin/main` @ `37adf8b`) as pure moves: 364 files under `orchestrator/src/raidar` in 8 top-level packages, with import paths, dotted strings and docs rewritten by a codemod. No behaviour change.

- **Layout as §8.** `core` → `contracts`; `spec` and `problems` → `design/{experiments,problems}`; `agents`, `components` and `packs` → `subjects/{agents,treatments,environments}`; `targets` → `engine/targets`; `verifier` → `grading`; `scorers/sdk` is folded into `scoring/framework` (flat, no name clashes), each scorer package moves to `scoring/<name>/`, `jev` → `scoring/judge`; `analysis`, `review/{projection,quality}` and `ledger` → `evaluation/`.
- **Jobs and trials split by what a module operates on.**
  - `engine/jobs/` (job level): `plan`, `run`, `detach`, `job_context`, `job_manifest`, `run_checks`, `assessment` (the lock's assessment settings) and `credentials` (the preflight over every selected arm before any trial).
  - `engine/trials/` (one trial): `driver`, `trial_run`, `trial_finish`, `trial_config`, `trial_record`, `environment`, `materialise`, `collect`, `cleanup`, `reaper`, `timestamps`, `agent_config`, `models` (planned trials, step results and trial records) and `progression` (the continue/halt rule over one trial's steps).
  - The two packages depend on each other at package level (`jobs.run` runs `trials.trial_run`; `trials.trial_run` receives a `jobs.job_context.JobContext` and `trials.trial_config` an `ExperimentPlan`). The module graph has no cycle, so no contract separates them.
- **Depth.** Nothing needed flattening. `scoring/code_quality/snapshot_tool` and `evaluation/review/projection` sit at depth 3. The Python pack's `subjects/environments/python/image/` (a Docker build context holding one `Dockerfile`, hashed into the image digest) would be depth 4. It is data like `payloads/` and `grading/runtime`, so `make structure-check` exempts it instead of moving the recipe.
- **Import contracts**, one per stage, with the existing edges kept: `contracts` imports no stage; nothing imports `cli` (new); `scoring` never imports `engine`, `subjects.agents`, `subjects.treatments` or `cli` (the jev contract is folded in); `grading` never imports `engine`, `subjects` or `cli`; `scoring.process_checks` never imports `scoring.judge`. The edge `scoring` → `grading.sandbox` is not enforced: `scoring.framework.rescore` regrades coverage through `grading.regrade`, which uses the sandbox.
- **Structure check.** `make structure-check` takes the 8 top-level packages and a second-level allowlist per group, and keeps the depth ≤3 rule with the exemptions above.
- **Re-key.** Scorer implementation digests and the `raidar_spec` toolchain digest changed with the module paths, and the three open experiments were relocked. Entry-point groups follow the package names (`raidar.design.experiments.resolvers`, `raidar.subjects.treatments`).

## 10. Round 3: tiers, singular names and data outside the package (2026-10-24)

Built on `structure-groupings` after round 2, from the import graph measured on 2026-10-24. Three rules from the coding-guidance skill applied to the round-2 layout. Behaviour is unchanged; the one declared re-key is below.

**Tiers.** The top-level groups are tiers, highest first, and a group imports only groups below it:

```
cli > evaluation > engine > design > scoring > grading > subject > contract
```

Inside a group the second level is a set of declared sub-tiers or of independent siblings:

| Group | Sub-tiers, highest first | Independent |
|---|---|---|
| `engine` | `job` > `trial` > `health` > `perf` > `target` | none |
| `scoring` | `catalog` > scorers (`code_quality`, `correctness`, `gate_effort`, `judge`, `process_checks`, `resource_usage`) > `framework` | the scorers, from each other |
| `subject` | `treatment` > `agent` | `environment`, from `treatment` and `agent` |
| `design` | none | `experiment`, `problem` |
| `evaluation` | `review` > `analysis` | `ledger`, from `review` and `analysis` |

`orchestrator/.importlinter` enforces the tiers with `layers` contracts (`tiers`, `engine-tiers`, `scoring-tiers`, `subject-tiers`, `evaluation-tiers`) and the independent siblings with `independence` contracts. The `forbidden` contracts that are stricter than the tiers stay: `scoring` never imports `subject.agent` or `subject.treatment`, `grading` never imports `subject`, `design.problem` never imports `subject.agent`, `scoring.process_checks` never imports `scoring.judge`, and `evaluation.ledger` imports no other raidar layer. The contracts that the tiers subsume (nothing imports `cli`, `contract` imports no group, `engine` and `evaluation` stay independent of `cli`) are removed.

**Upward edges removed.** The measured violations were `design` → `evaluation` (2), `scoring` → `design` (11) and `subject` → `design` (9), plus these sibling cycles. Each was broken by moving the shared part down:

- **`design.experiment` read hashing and references (scoring, subject).** `canonical` (canonical JSON hashing), `source_digest` and `references` (registry reference types, the resolver protocol) moved to `contract`. The resolver entry-point group is now `raidar.contract.resolvers`.
- **`scoring` read headline metrics from design.** `design/experiment/metrics.py` moved to `scoring/framework/metrics.py`. The lock's `raidar_spec` digest still covers it (`SPEC_EXTERNAL_MODULES` in `design/experiment/lock.py` lists it with the three `contract` modules above).
- **`design.experiment.validation` read `evaluation.analysis`.** It read the outcome metric ids and the manipulation-check names with their scorer refs. The metric catalogue (`MetricDef`, the metric ids, `primary_metric`, the checkpoint series) moved out of `analysis/outcomes.py` to `scoring/catalog/outcomes.py`, and the check table (`CHECK_SCORERS`) out of `analysis/manipulation.py` to `scoring/catalog/checks.py`. `analysis/outcomes.py` keeps the analysis models (`Value`, `TrialOutcome`, `PoolingReport`); analysis builds `treatment_check_report` on the table.
- **`scoring.framework.catalog` imported the scorers.** The registry machinery (`ScorerRegistration`, `ScorerRegistry`, the implementation digest, `tool_lock_for`) is `scoring/framework/registry.py`; the built-in registrations and their config tables are `scoring/catalog/builtin.py`. `process_checks` read `resource_usage.atif`, so `atif.py` moved to `scoring/framework/`.
- **`subject.treatment` compiled an `ExperimentSpec`.** `compile_condition`, `compile_arm` and the arm check moved to `design/experiment/arm.py` and `arm_check.py` (compiling an experiment's arm into components is design work that reads the treatment registry). `ArmBlueprint` left the job plan for `engine/trial/blueprint.py`.
- **`engine`: `health` ↔ `trial`, `perf` → `trial`, `target` → `trial`.** Trial records (`models.py`, read by `evaluation` too) moved to `contract/trial.py` with `NOT_STARTED`; Harbor's `result.json` reading (`RESULT_FILE`, `read_result`, `step_results`, from `trial/collect.py`) and the timing arithmetic of `trial/timestamps.py` together form `contract/harbor_result.py`. The agent reaper moved from `trial` to `health` (its reports are what health audits).
- **`engine`: `health` → `job`, `trial` ↔ `job`.** `provenance.py` (what a job records about the code it ran under; read only by `job`) moved from `health` to `job`. The plan (`ExperimentPlan`) and `JobContext`, which every trial receives, moved from `job` to `trial/plan.py` and `trial/context.py`.

**Singular names.** A folder is plural only when it is a catalogue of same-kind items. Renamed: `contracts` → `contract`, `subjects` → `subject` (`treatments`, `agents`, `environments` → `treatment`, `agent`, `environment`; `agent/adapters/` stays a catalogue), `engine/{jobs,trials,targets}` → `engine/{job,trial,target}`, `design/{experiments,problems}` → `design/{experiment,problem}`. `payloads/` and `adapters/` stay plural. A child no longer repeats its parent's name: `job/context.py` and `job/manifest.py`, `trial/{config,finish,record,run}.py`, `perf/report.py`, `environment/python/task.py`, `contract/review.py`, `grading/offline.py`. Entry-point groups follow the package names (`raidar.subject.treatment`).

**Data outside the package.** Run-time data lives under `lab/`, each tree at one explicit path from `raidar/contract/paths.py` (`treatments_root()`, `environments_root()`, `judge_root()`), with no search order and no fallback:

| Was | Now |
|---|---|
| `subject/treatment/payloads/python_guardrails/` | `lab/treatments/python_guardrails/` |
| `subject/environment/python/{pack.yaml,tools.lock.yaml,image/}` | `lab/environments/python/` |
| `scoring/judge/{policies,question_sets}/` | `lab/judge/{policies,question_sets}/` |

Component fingerprints are repo-relative (`orchestrator/src/raidar/...`, `lab/treatments/...`), so a payload outside the package is hashed like code. Code shipped into containers (the grader plugin `grading/runtime/`, `mcp_shim.py`, `step_reset.sh`) is code and stays in the package. `make structure-check` drops the payload and image depth exemptions (nothing needs them), allows only `experiments, problems, ledger, treatments, environments, judge` below `lab/`, and keeps the `v<N>/` data-folder rule for `components/` and `lab/treatments/`.

**Re-key.** Scorer implementation digests, the `raidar_spec` toolchain digest and component fingerprints changed with the module and payload paths. The three experiments were relocked; no jobs are carried over.

## 11. Final pass: gate green after the guidance fixes (2026-10-24)

Twelve parallel guidance passes landed without a full gate run; this pass made `make quality` exit 0 again and changed no rule.

- **`grading` owner folders.** The offline grading modules moved into `grading/prefix/` (pre-fix snapshot grades) and `grading/coverage/` (per-step coverage runs), each with its store. Both are real owners, so `make structure-check` lists them with `runtime` as the second level of `grading`.
- **Fan-out and complexity in analysis.** Resolving a verifier bundle from the problem store (the replacement for the bundle search) pushed `analysis/lookup.py` to 12 architectural imports and `load.py` to 11. `stored_bundle` now sits in `analysis/sources.py`, the lookup takes it as a callable and builds its own `BundleCache`, and `load.py` constructs the lookup in `_lookup` (which also keeps `load_inputs` within CCN 10).
- **Test typing.** `PINNED` in `test_analysis_command.py` is typed `dict[str, Any]`, and `scorer_support` narrows the inventory instead of indexing an optional pair.
- **Relock.** The three experiment locks were stale after the scorer, framework and trial changes and were relocked with `make experiment-lock`; no execution section changed, so `lock_sha256` values of jobs still match.
- **Docs.** AGENTS and README now say that a job's own `job.lock.json` is never a substitute for the lock (explicit or current lock, else a refusal, for assessment, analysis and review), name `lab/treatments` and `lab/environments` in the structure allowlist, and describe `grading/{prefix,coverage}`. The names `lab/treatments`, `lab/environments`, `tests/review_fixtures`, explicit target selection and the removal of `--bundle-search` were already current.
- **Pack recipe bytes kept.** Removing ticket tags from the header comments of `lab/environments/python/{image/Dockerfile,pack.yaml,tools.lock.yaml}` changed the pack image tag (`2b91a52663e099f2` → `9a2fea119406ded8`). Both problem pins bind that tag (RT6 drift). A default re-validation downgraded them from `validated` to `validated_partial`, because the recorded native-parity run was not repeated, and it invalidated forge's errata, which are keyed by manifest. The recipe is identity-bearing (AGENTS.md), so its bytes are restored: the old comments stay until a pack release rebuilds and re-validates both problems with native parity.
