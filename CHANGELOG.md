# Changelog

All notable changes to this project are documented in this file.

## Unreleased - reboot Phase 1 cutover (WS-L)

### Breaking Changes

- The scenario epoch's authoring and execution paths are removed. Raidar now has one authoring convention: `experiment.yaml` (problems, agents, conditions built from components, arms, scorers, analysis), locked by `raidar lock` and run by `raidar run`.
- Removed CLI commands: `raidar scenario ...`, `raidar info`, `raidar matrix ...`, `raidar experiment run`, `raidar experiments list|prune`, and the `raidar run --scenario` compatibility branch.
- Removed make targets, with no refusing stubs left behind. Old to new:

  | Removed target | Replacement |
  |---|---|
  | `scenario-list` | `make problem-list` |
  | `scenario-info` | `make problem-info PROBLEM=<ref>`, and `make experiment-plan EXPERIMENT=<path>` for an experiment's resolved contract |
  | `scenario-validate` | `make experiment-validate EXPERIMENT=<path>` and `make problem-validate PROBLEM=<ref>` |
  | `scenario-init` | `make experiment-init EXPERIMENT=<dir>/experiment.yaml` |
  | `scenario-clone-revision` | no target: add a condition and an arm inside the existing `experiment.yaml` |
  | `experiment-run`, `matrix-run` | `make experiment-execute EXPERIMENT=<path>` |
  | `runtime-stack-scenario-smoke` | `make pack-audit` and `make component-probe-trials` |
  | `experiments-list`, `experiments-prune` | none: `runs/` is a plain directory; `make harbor-cleanup` removes leftover trial environments |
  | `smoke-matrix SMOKE_MATRIX_SELECTOR=` | `make smoke-agents SMOKE_ARM=codex\|claude` (the Gemini selector is gone; the Python pack bakes in only codex-cli and claude-code) |
  | `benchmark-fixture-synthetic` | none: the scenario-epoch review read path is removed (see below); `make review-fixture-synthetic` writes the experiment fixture |

- Review surface variables and flags: `REVIEW_EXPERIMENTS_ROOT`, `python -m raidar.review_projection --experiments-root` and the fixture's scenario-epoch experiments are removed with the scenario-epoch read path (next entry). `make review-surface-test` always runs on the synthetic review fixture, and it takes one `REVIEW_STORE` for every `REVIEW_RUNS_ROOT` or one per root, failing on any other count instead of leaving given runs out of the Quality data.
- The scenario-epoch review read path is removed: `raidar.review_projection.scenario_epoch` (with its frozen fixture), `REVIEW_LEGACY_ROOT`, `python -m raidar.review_projection --legacy-root`, `REVIEW_FIXTURE` (`legacy` mode) and the scenario taxonomy in `raidar.schemas.review` are gone. `make review-surface-data` now requires `REVIEW_RUNS_ROOT`.
- The review projection is `schema_version=2`: `scenario` -> `problem`, `revision` -> `arm` (the arm's condition label), `scenario_meta` -> `problem_meta` (display name, description, difficulty, languages, checkpoints, timeout), `revision_diffs` -> `arm_diffs` (`problem`, `from_arm`, `to_arm`, `files.condition`), `parent_revision` -> `parent_arm`, and the `initial-revision` change category -> `baseline`. The surface's TypeScript types, data build, pages, `/wireframe` pages and wireframe artifacts use the new names; "Revision movement" reads "Arm movement".
- The review fixture's headline-policy mismatch now compares two experiment locks of `synthetic/policy-mix@1` (`scb-canonical-oneshot@1` and `scb-canonical@1`), and its `review-only` run root (projected for review, left out of the suite's Quality data) shows a run and a family without Quality data.
- The review surface's primary pages use experiment-model wording: "Problem families" anchors, the "problem / agent spec / id" runs filter, problem-scoped finding hints and failure patterns, and a "contract" tab for the scenario-epoch contract diff. The `schema_version=1` wire keys are unchanged; the Playwright specs assert the same signals under the new labels.
- Retired directories: `scenarios/`, `matrices/`, `environments/` and the Phase 0 `spikes/`, plus `raidar-revision-cleanup.md`. Nothing referenced them, so they are removed from the tree; the last tree containing them is tagged `archive/scenario-epoch`. The two spike artifacts the ADR and the N1 budgets cite are kept as `docs/reboot/evidence/a-prime-RESULTS.md` and `docs/reboot/evidence/overhead-baseline-summary.json`. The oracle-validation reproduction steps now use `make problem-validate`. The Node/Bun environment becomes a runtime pack in Phase 2.
- Removed modules: the scenario runtime pipeline (`raidar.runtime`: task bundles, task images, workspace phases, scorecards), `raidar.application`, the scenario and scorecard schemas, the scenario-attached weighted scorers (code-task, design-to-code, plan-to-code, test-generation, requirements, resource-efficiency, and the Codex-in-Docker `llm-as-judge` default), the matrix schema, starters, the gate watcher, the evidence-runner asset, and the repository-local Harbor CLI agents and routing.
- `raidar harbor cleanup` now removes the trial environments (run-scoped Compose projects) a stopped `raidar run` leaves behind: all of a named run's (`--run`), or every one with nothing running. It no longer matches scenario-era container names.

- `make perf-check` / `raidar perf check` is report-only on real experiment runs (user decision on N1 overhead budgets, 2026-10-01): it reports every phase against its accepted budget, prints breaches (over budget, `insufficient`, `missing`) as warnings and exits 0. Malformed or empty input still exits non-zero. `PERF_STRICT=1` (`--strict`) restores the failing check. The enforced overhead check is now `make perf-gate`.

### Features

- `make perf-gate [PERF_GATE_DIR=.tmp/perf-gate]`: the enforced N1 overhead gate. It runs the no-LLM fixture (`make perf-fixture`) at concurrency 1 and then 3, each checked with `--strict` against the accepted budgets (unchanged), and fails on any breach. It needs Docker (OrbStack), so it is a release/gate check and is not part of `make quality`. Each concurrency leaves its strict JSON report at `<PERF_GATE_DIR>/concurrency-<n>/perf-check.json`.
- `raidar perf check` records each run's contention context: the configured trial concurrency (`run.json`), the peak number of trials running at once in the checked runs, the peak including other Raidar runs under the same runs root that overlapped in time (listed by path), and how many trials had no start/finish timestamps. Docker work outside Raidar runs is not recorded by runs, and the report says it is not detected. For each p95 budget that is over, the report re-judges it separately for trials that ran with at most 3 and with 4 or more trials running at once.
- `raidar problem list` / `make problem-list` lists every registered problem with its mode, checkpoints, validation verdict (or why it has no usable one) and import state. `raidar problem info` / `make problem-info PROBLEM=` shows one problem's steps, source pin, pack binding, manifest digest and validation report.
- `raidar init` / `make experiment-init EXPERIMENT= [PROBLEM=] [EXPERIMENT_ID=]` writes a two-arm starter experiment (baseline vs `rules-file@1`, agent version from the pack pin) and a stub rules file. It validates as written and refuses an existing path.
- Experiment-path smoke targets: `make orchestrator-smoke`, `make agent-smoke` and `make smoke-agents` run `examples/smoke/` through lock and Harbor. `smoke-agents` has one arm per pack harness (`codex`, `claude`).
- `make experiment-assess RUN= STORE= SCORERS=` rescores a run's stored trials into an Assessment store (`raidar assess --rescore`).
- `make harbor-cleanup RUN= DRY_RUN=`.
- Secrets come from Infisical (project `mac-os-cf`, environment `dev`, bound by `orchestrator/.infisical.json`). Targets that run agents or Jev wrap `raidar` in `infisical run --env=dev`. `SECRETS=auto` (default) uses Infisical when the CLI is installed and logged in and otherwise falls back to `orchestrator/.env`; `SECRETS=infisical` fails instead of falling back; `SECRETS=dotenv` skips Infisical. Only the source is printed, never a value.
- `raidar run` runs a presence-only credential preflight for every selected arm before any trial starts, and exits 2 naming the missing variables. It does not check that an account is funded.
- `make agent-smoke HARNESS=claude-code` defaults to `claude-haiku-4-5` with no reasoning effort, and the `smoke-agents` claude arm uses the same model. An empty override (`REASONING_EFFORT=`) unsets the key.
- Jev calibration: labels record a `human` or `model` reviewer (and the model id) through import, export, readiness and the review-surface labels API. `make jev-export-blind` / `make jev-import-blind` run blind labelling. `make jev-promote` fits and freezes a threshold on a calibration/evaluation split and records metrics, a regression corpus and reviewers; `make jev-verify-promotion` re-derives them. Rubric question set `scb-rubric@2` asks codebase-scope criteria with an indexed focus.
- Scoring alignment: correctness C is the SCB fork's mean per-checkpoint case pass rate everywhere S is computed, and analysis reports C as `correctness`. New headline policies `scb-canonical@2` and `scb-canonical-oneshot@2` score S = 0 when C = 0; the pilot experiments declare `@2`.

### Bug Fixes

- `make jev-promote` no longer writes `mode: enforce` while the scorer minor bump the promotion names is still missing. Writing it then left a repo policy that `make jev-verify-promotion` rejects. The command now exits 1 with `"blocked_on_scorer_bump": true`; `ALLOW_SCORER_BUMP=1` (`--allow-pending-scorer-bump`) writes anyway, for the "promote, then bump the scorer" sequence.

### Tests

- An end-to-end analysis test checks the `scb-canonical@2` zero-correctness rule through rescore and `raidar analyze`: C = 0 trajectories with a quality gap score S = 0 and are paired, C > 0 ones stay unassessed, and `@1` leaves all of them unassessed. No pilot trajectory has C = 0, so this path was otherwise covered only by unit tests.

### Documentation

- Gate packet §12 closes Phase 1: Infisical secrets, Claude Haiku readiness (blocked on Anthropic account credit), live shadow Jev (15,516 provider calls, about $2.33), labelling provenance and its limits, the promotion outcome (not promoted), scoring alignment and cleanup. §6 and §11.15 no longer say live Jev is blocked, and §11.17 states what the labelling artifacts can and cannot prove.

### Chores

- Generated coverage data (`.coverage`, `orchestrator/.coverage`) is no longer tracked and is ignored.
- The component proof fixtures (`examples/pilot/components/probe/`) move to `orchestrator/tests/fixtures/components/probe/`; the component tests compile them from there.
- The quality gates' generated-artifact guard covers `runs/` as well as `experiments/`, and the Python check scripts exclude `runs/`, `problems/store/` and `problems/cache/` instead of `scenarios/`.
- CI no longer installs Bun; only the removed scenario starters used it.
- `AGENTS.md` is rewritten for the experiment, problem, component and scorer model (workflow rules, known experiment configs, smoke testing). `CODEOWNERS` drops the nonexistent `/.configs/` and `/benchmark-view/` and names the current top-level surfaces.
- The README, `make help`, the review-surface README and the import contracts describe the experiment, problem, component and scorer model.

## 0.14.0 - 2026-07-03

### Chores

- chore: capture orchestration and wireframe updates

- wireframe: add runs review surface
- wireframe: simplify expanded run summary
- wireframe: tint expanded row panel
- wireframe: remove run criteria label
- wireframe: align run criteria headers
- wireframe: remove redundant criterion score pills
- wireframe: add run criterion matrix
- wireframe: clarify expanded row evidence
- wireframe: left align movement value cells
- wireframe: remove neutral movement arrows
- wireframe: simplify neutral movement values
- wireframe: derive output tokens from runs
- wireframe: left align token movement headers
- wireframe: split token spend movement
- wireframe: show runtime before movement
- wireframe: remove duplicate native tooltips
- wireframe: default trends to spend
- wireframe: add revision chart filter
- wireframe: inline trends subtitle
- wireframe: inline section subtitles
- wireframe: align rotated pattern header anchors
- wireframe: bottom align pattern headers
- wireframe: hide pattern header question marks
- wireframe: clarify patterns agent spec count
- wireframe: simplify patterns revision count label
- wireframe: explain pattern criterion sources
- wireframe: clarify trends scenario scope
- wireframe: show latest pattern revisions first
- wireframe: scope revision filter to movement
- wireframe: normalize section vertical padding
- wireframe: align trends container padding
- wireframe: reduce trends container padding
- wireframe: restore trends container border
- wireframe: wrap charts in trends section
- wireframe: align patterns subtitle style
- wireframe: explain runtime and spend columns
- wireframe: remove redundant pattern evidence row
- wireframe: show visible pattern runs
- wireframe: simplify checks chip
- wireframe: count visible pattern revisions
- wireframe: refine patterns subtitle
- wireframe: clarify revision sort tooltip
- wireframe: explain revision row sorting
- wireframe: add revision row sorting
- wireframe: rank revision rows by scenario
- wireframe: remove run column from revisions
- wireframe: add movement arrows to revision rows
- wireframe: move revision selector to scenario header
- wireframe: merge revision movement and rows
- wireframe: center evidence map labels
- wireframe: remove evidence map legend
- wireframe: mark scenario titles with mint dot
- wireframe: distinguish scenario title color
- wireframe: add recovery color for patterns
- wireframe: align section title styling
- wireframe: align cleared pattern color
- wireframe: title case evidence map label
- wireframe: label criterion checks
- wireframe: clarify patterns map labels
- wireframe: space patterns summary controls
- wireframe: widen patterns subtitle
- wireframe: inline patterns summary counts
- wireframe: simplify patterns map panel
- wireframe: tune patterns map headers
- wireframe: align patterns map labels
- wireframe: add patterns evidence map
- wireframe: add patterns panel design artifacts
- wireframe: clarify patterns and reorder family controls
- wireframe: add recurring patterns panel
- wireframe: split chart and movement evenly
- wireframe: align movement beside charts
- wireframe: link diff pills to tabs
- wireframe: tint diff pills cyan
- wireframe: simplify revision diff pills
- wireframe: show all selected movement pairs
- wireframe: rank revision movement rows
- wireframe: reuse agent spec labels
- wireframe: refresh revision movement table
- wireframe: match family body vertical padding
- wireframe: align scenario outer padding
- wireframe: tighten spend chart padding
- wireframe: balance spend chart layout
- wireframe: align chart legend dots
- wireframe: match chart subtitle typography
- wireframe: show chart tab button states
- wireframe: match runtime chart edge padding
- wireframe: balance runtime plot edges
- wireframe: tighten runtime chart right edge
- wireframe: balance runtime chart padding
- wireframe: correct chart left alignment
- wireframe: align chart header rows
- wireframe: label spend chart outcome axis
- wireframe: align spend chart subtitle
- wireframe: rename token chart tab
- wireframe: use outcome rings on token chart
- wireframe: populate token spend chart data
- wireframe: extract token bar chart component
- wireframe: revise token spend chart axis
- wireframe: add token chart tab

## 0.14.0 - 2026-06-14

### Features

- feat(synthetic): add an unscored run to the bugfix-low spec
- feat(synthetic): populate scorer metric_contributions
- feat(synthetic): multi-spec, multi-revision fixture at volume
- feat: plain-language nav copy and Runs landing guidance
- feat: refine issues triage, revision-movement readability
- feat: semantic verdict layer and progressive-disclosure review UX
- feat: link runs back to their experiment review
- feat: efficiency contrast in diagnosis and benchmark-row messaging
- feat: replace experiments metric table with scenario board and experiment review
- feat: add synthetic visual-ui benchmark fixture with screenshot evidence
- feat: family quick-nav, token delta sign fix, and docs for ported comparisons
- feat: port benchmark-view's comparison value into review-surface
- feat: keyboard navigation, run dates, and persona doc updates
- feat: scale span-tree navigation for debuggers
- feat: give benchmark reviewers scenario context and ranking deltas
- feat: add scorecard breakdown and gate chips to run review
- feat: add persona-aware review-surface app over Raidar benchmark evidence
- feat: scaffold review-surface data projection and API server
- feat: surface review findings in benchmark-view with synthetic fixtures
- feat: project run and experiment evidence into deterministic review findings
- feat: add bugfix-ledger-balance scenario exercising bugfix scorer
- feat: ingest scenario-declared retained evidence into scorer context
- feat: activate concrete scorer definitions

### Bug Fixes

- fix: persist execution trace events on run records
- fix: improve task image caching and coverage gates

### Documentation

- docs: update runLabel example to current synthetic id format
- docs: record review-surface delivery against charter backlog items
- docs: record charter-review execution results and follow-on backlog
- docs: record charter-review implementation goal and phase-1 plan

### Refactoring

- refactor: ground repeatability tier in Raidar's variance threshold; doc redesign
- refactor: drop unused IdChip from Verdict
- refactor: deprecate benchmark-view in favor of review-surface
- refactor: harden review-surface UX from browser persona testing

### Tests

- test(review-surface): committed Playwright e2e functional suite (S1)

### Chores

- chore(docs): remove superseded review-surface and migration todo specs
- chore: add charter review goal assets

- revert: roll back scenario-board redesign and visual fixture

## [0.13.0] - 2026-06-09

- feat: migrate scorer definitions to coded scorers
- feat: move matrix execution to stored configs
- refactor: realize scorer metric contracts
- refactor: make scorers canonical
- refactor: organize scorer implementations
- docs: organize todo references
- chore: add scorer platform goal
- chore: strengthen python quality gates
- chore: ignore generated runtime outputs

## [0.12.1] - 2026-05-20

- refactor: introduce scorer-based metrics

## [0.12.1] - 2026-05-19

- fix: stabilize agent smoke warm prep
- refactor: enforce runtime fan-out boundaries
- refactor: normalize duplicated orchestrator paths
- refactor: clean up orchestrator dispatch and release bumping
- refactor: implement code quality cleanup plan
- refactor: clean up orchestrator execution contracts
- chore: commit outstanding workspace updates

## [0.12.0] - 2026-05-14

- feat: improve benchmark web view ux
- feat: further improvements to benchmark web view
- feat: add benchmark scenario iteration tooling
- refactor: consolidate harbor cli smoke adapters
- refactor: improve benchmark dashboard filters
- chore: ignore infisical state file
- chore: remove stale benchmark review artifacts

## [0.11.1] - 2026-04-21

- docs: add harness optimization notes
- chore: restore coverage database

## [0.11.1] - 2026-04-21

- fix(runner): retry transient workspace prune failures
- refactor(models): separate provider and reasoning config

## [0.11.0] - 2026-04-20

- feat(runner): harden harbor fast-mode execution

## [0.10.0] - 2026-03-28

- feat(codex): add oauth onboarding and smoke auth defaults

## [0.9.3] - 2026-03-28

- fix(scenarios): keep homepage iterations in revision roots
- docs(agents): remove temporary sync notes
- chore(main): reconcile non-autoresearch local work

## [0.9.2] - 2026-03-28

- fix(ci): remove deleted auto_researcher sync step
- fix: route public smoke targets through fast agents
- fix(orchestrator): rewrite canonical verifier artifacts
- fix: align homepage loop and low-model orchestration
- refactor: remove autoresearch surface from main
- docs: add branch syncing guidance
- docs(readme): trim benchmark and mode guidance
- chore: remove stale plan and scenario artifacts
- chore: ignore autoresearch objective artifacts

## [0.9.1] - 2026-03-27

- fix(auto-researcher): use starter-local temp env
- fix(orchestrator): use workspace-local temp env for starter prep
- fix(make): use repo-local temp env for public commands
- fix(orchestrator): restore api-key-only codex harbor auth
- fix(orchestrator): support codex auth file for harbor smoke
- fix(orchestrator): tolerate ps permission errors in harbor cleanup
- docs(readme): align raidar mode experiment flows

## [0.9.0] - 2026-03-26

- feat: add codex gpt-5.4-mini support
- perf: reuse orchestrator prep caches and add smoke matrix
- refactor: default smoke tests to codex gpt-5.4-mini
- refactor: rename smoke target to orchestrator-smoke
- refactor: add dedicated make agent smoke target
- refactor: route agent smoke checks through make targets
- docs: add orchestrator reuse execplan package
- ci: install Bun in quality gates workflow
- chore: ignore local cache artifacts
- chore: tighten quality gates for implementation-agnostic debt smells
- test: add ci smoke dry-run drift checks
- test: cover smoke repeat and parallel make flows

## [0.8.0] - 2026-03-16

- feat: tighten homepage scenario baseline contract
- refactor: strengthened the homepage scenario revision 001 to be a better baseline and ensure metric usage is suitable/working

## [0.7.2] - 2026-03-14

- fix: preserve provider env for standard Harbor runs

## [0.7.1] - 2026-03-11

- fix: gate fast smoke env wiring and document env surface
- docs: trim env example to common local setup

## [0.7.0] - 2026-03-11

- refactor: align terminology around harnesses and agent specs
- chore: checkpoint current repo state

## [0.7.0] - 2026-03-11

- docs: reorganize review surface artifacts

## [0.7.0] - 2026-03-11

- refactor: simplify repo docs and scripts

## [0.7.0] - 2026-03-10

- feat: add codex gpt-5.4 thinking support
- fix: isolate experiment run directories by model
- fix: recognize heredoc verification commands in codex traces
- fix: pass rerun metadata to experiment summaries
- refactor: finish migration naming cleanup
- refactor: finish unscored rerun migration
- refactor: finish scenario migration cleanup
- refactor: remove legacy comparison package
- refactor: migrate scenario experiment trace vocabulary
- docs: record homepage analysis artifact
- docs: record codex migration validation
- docs: finalize migration wording cleanup
- docs: mark naming migration complete
- docs: close remaining migration cleanup
- docs: refresh codex script command surface
- docs: refresh codex migration plan and scripts
- docs: rename new scenario reference
- docs: finish scenario migration reference docs
- chore: restore coverage database

## [0.6.0] - 2026-03-08

- chore: ignore visual explainer artifacts

## [0.6.0] - 2026-03-08

- refactor: remove opik workflow
- test: enforce argv-only task commands
- refactor runner phase-2 complexity slices

## [0.6.0] - 2026-03-04

- chore: sync uv lockfile version metadata

## [0.6.0] - 2026-03-03

- docs: align metrics v2 guidance and analyzer prompt

## [0.6.0] - 2026-03-03

- feat: add modular metrics schema and artifact presence module
- docs: align reference guides with module-driven metrics
- docs: add metrics module setup guidance for tasks
- docs: backfill readme for required metrics modules

## [0.5.0] - 2026-03-02

- feat: added more rigour to the odiff comparisons

## [0.4.0] - 2026-03-01

- refactor: remove provider probe preflight and refresh eval guidance

## [0.4.0] - 2026-02-24

- feat: add adapter-driven provider probe preflight
- chore: add fast pre-commit quality preflight

## [0.3.0] - 2026-02-24

- Update README.md

## [0.3.0] - 2026-02-24

- Update README.md by removing sections and adding details

## [0.3.0] - 2026-02-24

- feat: add claude sonnet 4.6 and gemini 3.1 pro support

## [0.2.4] - 2026-02-24

- fix: enforce single-source version wiring
- chore: enforce locked uv sync in git hooks

## [0.2.3] - 2026-02-23

- refactor: rename agentic_eval to raidar and standardize evals layout

## [0.2.3] - 2026-02-23

- fix: use StrEnum for harness agent enum
- docs: update AGENTS project map and workflows
- build: track uv lockfile and enforce frozen sync

## [0.2.2] - 2026-02-22

- fix: remove frozen sync requirement from quality gates

## [0.2.1] - 2026-02-22

- fix: align local and ci gate sync flow
- refactor: migrate execution maintenance into raidar cli

## [0.2.0] - 2026-02-22

- docs: update readme strapline

## [0.2.0] - 2026-02-22

- chore: rename project branding to Raidar

## [0.2.0] - 2026-02-22

- feat: finalize multi-provider harness support and smoke validation
- feat(orchestrator): enforce harness-model pairing
- feat(harness): add adapters for cursor copilot pi
- feat(orchestrator): enforce harness adapters and scaffold catalog
- feat(parser): implement Gemini parser and fix Claude Code multi-block handling
- feat(compliance): add structured judge response parsing with retry logic
- feat(config): add centralized configuration with pydantic-settings
- fix: cap void retries to one attempt
- fix: addressed various architecture smells
- fix(harness): support runtime validation plus sample run
- fix(pre-commit): exclude scaffold and generated folders from checks
- refactor: remove scaffold manifests and track workspace diffs
- refactor: finalize v1 execution model and evidence pipeline
- refactor: split run validity and performance gates across scoring pipeline
- refactor: migrate orchestrator ops to cli and untrack generated artifacts
- refactor: align harbor execution with scaffold task bundles
- refactor: require argv commands in task yaml
- refactor: split runner phases and add lizard gate
- chore: add changelog automation and root project readme
- chore: added enaible to ignore
- chore: ignore next-session
- chore: centralize quality gates
- chore(auth): load .env and document codex oauth plan
- chore(devex): add uv setup and harbor prep
- chore: delete actioned implementation plan
- chore: add pre-commit configuration with ruff and pytest
- test: add comprehensive unit test suite with 64 tests
- commit clean ahead of big refactor
- Add context note to implementation plan
- Fix validation and lint script issues
- Add list-agents and info CLI commands from impl-4
- Enhance schemas with @computed_field pattern
- Add comparison/aggregation module from impl-4
- Add vitest test infrastructure from impl-2
- Rename namespace to agentic_eval
- Add configuration matrix and run aggregation
- Add scoring system with multi-dimensional evaluation
- Add scaffold audit, schemas, task definition, and CLI
- Add orchestrator Python project and Next.js scaffold template
- Add research document and implementation plan

## [0.1.0] - 2026-02-22

- Initial orchestrator and task evaluation framework baseline.
