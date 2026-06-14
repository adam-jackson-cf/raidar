# Changelog

All notable changes to this project are documented in this file.

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
