# Breaking Naming Migration: Scenario / Experiment / Trace Vocabulary

## Summary

Perform one repo-wide, breaking vocabulary migration so the implementation, persisted artifacts, CLI, docs, tests, and historical evaluation outputs all use a single coherent model:

- `scenario` = authored evaluation spec
- `scenario_revision` = immutable version of that spec
- `experiment` = repeated evaluation campaign for one `scenario_revision + agent + model + evaluation_profile`
- `run` = one scored execution inside an experiment
- `trace` = execution telemetry/log/event stream for a run

This plan now reflects the implemented migration:
- historical generated artifacts remain immutable evidence and are not rewritten in place
- no backward-compatibility aliases are kept
- `docs/analyze-results.md` stays at the same path with fully migrated contents
- `docs/references/` is included because explicit user approval was granted and the reference docs were updated

## Current Status (2026-03-10)

Completed in the current repository state:

- Public scenario / agent / experiment vocabulary is live across the supported Make targets, CLI groups, canonical schemas, verifier contract, docs, and first-party reference docs.
- Canonical artifact roots and filenames are migrated to `experiments/`, `experiment.json`, `experiment-summary.json`, and `report.md`.
- Verifier metric ids and persisted scorecard fields use `acceptance`, `verification-stability`, `execution-validity`, `resource-efficiency`, `metric_results`, and `unscored_reasons`.
- `docs/references/` has now also been updated because explicit user approval was granted after this plan was written.
- Codex CLI now supports `gpt-5.4` thinking tiers (`low`, `medium`, `high`, `extra high`) and the Codex smoke suite is green across the supported Codex models.
- `suite` at the top level of matrix configs remains an intentional repo contract and is not treated as unfinished migration work.
- `retry_void` remains the current matrix-config field name for the same reason; public Make variables are migrated to `RERUN_UNSCORED`.
- The latest generated human review artifact is `experiments/eval-analysis-homepage-20260310-140956.html`, derived from the fresh homepage Codex experiment set.

Migration completion notes:

- A fresh homepage Codex experiment set completed on March 10, 2026 across `codex/gpt-5.2-{low,medium,high}` and `codex/gpt-5.4-{low,medium,high,extra-high}`.
- The corresponding human review artifact is now generated at `experiments/eval-analysis-homepage-20260310-140956.html` using the workflow in `docs/analyze-results.md`.
- No remaining repository code, docs, test, or command-surface tasks are required for this migration.

Decision update:

- Historical generated experiment artifacts under `experiments/` are treated as immutable evidence and are not rewritten in place. Rewriting archived traces, stack traces, and raw Harbor outputs would mutate historical evidence rather than complete the source migration. The migration completion bar is therefore: all live code/docs/contracts use the new vocabulary, and all newly generated experiments use the migrated artifact schema and paths.
- The public CLI flag is `--rerun-unscored`. Only matrix YAML keeps `retry_void`, as an explicitly retained config-schema exception.
- The homepage implementation Codex experiment matrix has been rerun on the migrated surface, and those artifacts are the canonical post-migration validation set for analysis.

## Chosen Vocabulary

| Current | Target | Notes |
|---|---|---|
| task | scenario | Public + internal |
| task version / version | scenario_revision | Public + internal |
| tasks/ | scenarios/ | Repo tree rename |
| task.yaml | scenario.yaml | Authored spec rename |
| suite | experiment | Public + internal |
| suite.json | experiment.json | Artifact rename |
| suite-summary.json | experiment-summary.json | Artifact rename |
| evals/ | experiments/ | Canonical artifact root |
| harness | agent | Public + persisted config |
| scaffold | starter | Authored baseline asset |
| metric_profile | evaluation_profile | Derived identity |
| metric_modules | metrics | Public + persisted metadata |
| metrics.modules[] | metrics[] | Task schema simplification |
| module_id | metric_id | Internal + persisted |
| module_outcomes | metric_outcomes | Persisted summary rename |
| void / voided | unscored | Public + persisted |
| retry_void | rerun_unscored | CLI / config rename |
| compliance | acceptance | Schema + scoring rename |
| run-validity | execution-validity | Metric id + artifact rename |
| visual-odiff | visual-regression | Metric id rename |
| coverage-threshold | test-coverage | Metric id rename |
| requirements | requirements-coverage | Metric id rename |
| artifact_presence | artifact-checks | Metric id rename |
| optimization | resource-efficiency | Score dimension rename |
| efficiency | verification-stability | Score dimension rename |
| session_log | trace_log | Telemetry rename |
| SessionEvent | TraceEvent | Telemetry rename |

## Public Interface and File-System Changes

### Root repo tree

- Rename `/Users/adamjackson/Projects/raidar/tasks` to `/Users/adamjackson/Projects/raidar/scenarios`
- Rename `/Users/adamjackson/Projects/raidar/evals` to `/Users/adamjackson/Projects/raidar/experiments`
- Keep `/Users/adamjackson/Projects/raidar/docs/analyze-results.md` at the same path, but rewrite all vocabulary and all artifact paths inside it

### Authored scenario tree

Use this canonical authored structure:

```text
scenarios/<scenario-name>/<scenario_revision>/
  scenario.yaml
  prompt/task.md
  rules/
  starter/
  reference/   # unchanged if already present
```

Decisions:
- Keep `prompt/` and `rules/` directory names unchanged
- Rename only `task.yaml` and `scaffold/`
- Keep `v001` style revision labels unchanged; only the semantic name changes to `scenario_revision`

### CLI and Makefile surface

Rename the supported public surface to:

- `make agent-list`
- `make agent-validate AGENT=... MODEL=...`
- `make scenario-init SCENARIO_DIR=... [SCENARIO_REVISION=v001]`
- `make scenario-info SCENARIO_DIR=...`
- `make scenario-validate SCENARIO=...`
- `make experiment-run SCENARIO=... AGENT=... MODEL=...`
- `make matrix-run SCENARIO=... [CONFIG=matrix.yaml]`
- `make experiments-list [EVALUATION_PROFILE=...] [LIMIT=...]`
- `make experiments-prune [KEEP_PER_MODEL=1]`
- `make quality`

CLI command groups/verbs must align:

- `provider list` -> `agent list`
- `provider validate` -> `agent validate`
- `task init` -> `scenario init`
- `task validate` -> `scenario validate`
- `task clone-version` -> `scenario clone-revision`
- `suite run` -> `experiment run`
- `evals list` -> `experiments list`
- `evals prune` -> `experiments prune`

Defaults:
- Keep `matrix` as the command/group name
- Keep `AGENT` and `MODEL` env/make variables
- Rename `TASK`, `TASK_DIR`, `TASK_VERSION`, `SCAFFOLD_ROOT`, `METRIC_PROFILE`, `REPEATS`, `REPEAT_PARALLEL`, `RETRY_VOID` to `SCENARIO`, `SCENARIO_DIR`, `SCENARIO_REVISION`, `STARTER_ROOT`, `EVALUATION_PROFILE`, `RUN_COUNT`, `RUN_PARALLELISM`, `RERUN_UNSCORED`

## Internal Code Changes

### Package/module moves

These package/module moves are already complete in the current repository state:

- `orchestrator/src/raidar/agents/`
- `orchestrator/src/raidar/starter/`
- `orchestrator/src/raidar/experiment.py`
- `orchestrator/src/raidar/scenario_clone.py`
- `orchestrator/src/raidar/parser/trace_log.py`
- `orchestrator/src/raidar/scoring/acceptance.py`

Remaining work here is limited to auditing imports, docs, tests, and prompts for stale references to the pre-migration module paths.

### Type and class renames

Rename the following core types:

- `TaskDefinition` -> `ScenarioDefinition`
- `ScaffoldConfig` -> `StarterConfig`
- `PromptConfig` stays `PromptConfig`
- `ComplianceConfig` -> `AcceptanceConfig`
- `VerificationGate` stays `VerificationGate`
- `MetricsConfig` stays `MetricsConfig`, but field becomes `metrics: list[MetricDefinition]`
- `CoreMetricModule` -> `CoreMetricDefinition`
- `ArtifactPresenceMetricModule` -> `ArtifactCheckMetricDefinition`
- `HarnessConfig` -> `AgentRunConfig`
- `ScaffoldSource` -> `StarterSource`
- `ScaffoldContext` -> `WorkspaceContext`
- `SuiteExecutionResult` -> `ExperimentExecutionResult`
- `MatrixRunResult` stays, but fields must use `agent` not `harness`
- `ComplianceCheck` -> `AcceptanceCheck`
- `ComplianceScore` -> `AcceptanceScore`
- `EfficiencyScore` -> `VerificationStabilityScore`
- `OptimizationScore` -> `ResourceEfficiencyScore`
- `RunValidityScore` -> `ExecutionValidityScore`
- `SessionEvent` -> `TraceEvent`

### Function renames

Rename key helpers for direct traceability:

- `load_task` -> `load_scenario`
- `task_metric_profile` -> `scenario_evaluation_profile`
- `task_metric_modules` -> `scenario_metrics`
- `prepare_run_context` stays
- `_ensure_suite_baseline_workspace` -> `_ensure_experiment_baseline_workspace`
- `create_repeat_suite_summary` -> `create_experiment_summary`
- `persist_repeat_suite` -> `persist_experiment`
- `clone_task_version` -> `clone_scenario_revision`

### Remove legacy implementation seams

Delete or fully rewrite the legacy comparison path so old vocabulary does not survive in dead-end code:

- Rewrite or remove `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/comparison/matrix_runner.py`
- Rewrite or remove `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/comparison/aggregator.py`

Decision:
- Rebuild both on top of canonical experiment artifacts, not per-run legacy scorecard loading

## Artifact Schema Migration

### Canonical experiment root

Canonical root becomes:

- `/Users/adamjackson/Projects/raidar/experiments/`

Per-experiment structure becomes:

```text
experiments/<existing-dir-name>/
  experiment.json
  experiment-summary.json
  report.md
  runs/
    run-01/
      run.json
      report.md
      verifier/
        scorecard.json
        execution-validity.json
        performance-gates.json
      agent/
        trace*.json
        *.txt
      workspace/
      workspace-diff.json
```

Decisions:
- Preserve existing timestamped experiment directory basenames; only rename root and internal files
- Keep `runs/run-XX/` naming unchanged
- Keep `agent/` directory name because it stores mixed agent artifacts, not only traces
- Keep `.preflight-cache/` unchanged
- Rename root `analysis.md` to `report.md`
- Rename run-level `summary.md` to `report.md`

### JSON field renames

Apply these schema renames in all persisted artifacts:

| Current | Target |
|---|---|
| task_name | scenario_name |
| task_version | scenario_revision |
| scaffold_root | starter_root |
| harness | agent |
| metric_profile | evaluation_profile |
| metric_modules | metrics |
| module_outcomes | metric_outcomes |
| suite_id | experiment_id |
| voided | unscored |
| void_reasons | unscored_reasons |
| retry | rerun |
| unresolved_void_count | unresolved_unscored_count |
| repeat_required_count | rerun_required_count |
| compliance | acceptance |
| run_validity | execution_validity |
| optimization | resource_efficiency |
| efficiency | verification_stability |
| modules | metric_results |
| module_id | metric_id |

Additional rules:
- Preserve all timestamps, numeric scores, and evidence paths except where filenames/directories are renamed
- Bump schema version markers where present
- Update `scores.metadata.run.run_analysis_path` to `run_report_path`
- Update any `canonical_run_dir` and `run_json_path` values to the new root/file names
- Update `scores.metadata.task` to `scores.metadata.scenario`
- Update `scores.metadata.scaffold` to `scores.metadata.starter`

### Historical artifact rewrite

Rewrite all six existing artifact roots in place.

Migration steps for each existing experiment directory:
1. Move it from `evals/` to `experiments/`
2. Rename `suite.json` to `experiment.json`
3. Rename `suite-summary.json` to `experiment-summary.json`
4. Rename root `analysis.md` to `report.md`
5. Rename each run `summary.md` to `report.md`
6. Rename `verifier/run-validity.json` to `verifier/execution-validity.json`
7. Rewrite all JSON payload keys and embedded file paths
8. Rewrite markdown references inside `report.md`
9. Rewrite any `evals/` path literals to `experiments/`
10. Rewrite any `suite`/`task`/`harness`/`metric_profile` terminology in human-readable headings and labels

Decision:
- This is an in-place destructive migration of historical artifacts inside the repo
- There will be no `legacy/` archive tree and no dual-read support

## Docs and Prompt Rewrite Scope

### Must be rewritten

- `/Users/adamjackson/Projects/raidar/README.md`
- `/Users/adamjackson/Projects/raidar/Makefile`
- `/Users/adamjackson/Projects/raidar/docs/command-surface.md`
- `/Users/adamjackson/Projects/raidar/docs/analyze-results.md`
- `/Users/adamjackson/Projects/raidar/AGENTS.md` only where repo-specific command examples or file paths mention old terms
- task/scenario prompts and rule files only where they explicitly refer to old artifact/model names
- inline help strings and Click command docstrings
- verifier asset prompt text in `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/assets/verifier-score-task.mjs`

### `docs/analyze-results.md` rewrite requirements

Rewrite the prompt so it uses only new vocabulary and new paths:

- `evals/*/suite.json` -> `experiments/*/experiment.json`
- `evals/*/suite-summary.json` -> `experiments/*/experiment-summary.json`
- `evals/*/analysis.md` -> `experiments/*/report.md`
- `Run-validity artifacts` -> `Execution-validity artifacts`
- `task_name`, `task_version`, `harness`, `metric_profile`, `metric_modules`, `module_outcomes`, `void_count`, `void_rate`, `visual-odiff` all rewritten to new field names
- text that says “latest suite” rewritten to “latest experiment”
- ranking identity changed from `(task_name, task_version, harness, model, metric_profile)` to `(scenario_name, scenario_revision, agent, model, evaluation_profile)`
- “Agent traces” section updated to use `trace` terminology

Decision:
- Keep the analytical logic and ranking math unchanged except for renamed field references and renamed metric IDs
- The doc remains single-task/single-scenario comparison guidance; do not introduce dataset semantics yet

### Explicit out-of-scope docs

Do not touch `/Users/adamjackson/Projects/raidar/docs/references/` in this migration plan without separate approval.

## Implementation Phases

### Phase 1: Rename charter and grep inventory

- Create a single rename matrix from the tables above and use it as the source of truth
- Run repo-wide searches to inventory every instance of:
  `task`, `suite`, `evals`, `harness`, `scaffold`, `metric_profile`, `void`, `compliance`, `run-validity`, `visual-odiff`, `provider` command surface, and telemetry `session`
- Mark allowed exceptions:
  third-party package names, dependency lockfiles, vendor source, and untouched `docs/references/`

### Phase 2: Public surface and repo tree

- Rename root directories and authored scenario file names
- Rename Make targets and CLI command groups/options/help
- Rename command-surface docs and README examples
- Rename `provider` command group to `agent`
- Update all path examples from `tasks/.../task.yaml` to `scenarios/.../scenario.yaml`
- Update README system diagram terminology to match the new model

### Phase 3: Schema and runtime refactor

- Rename schema classes, config fields, metric ids, score dimension names, and metadata keys
- Rename package/module paths from `harness` and `scaffold`
- Rewrite internal imports and public exports
- Update verifier asset code to emit the new JSON shape and filenames
- Rewrite all runner and experiment aggregation code to the new names

### Phase 4: Historical experiment migration

- Implement a one-off migration tool inside the repo to rewrite the six existing experiment directories in place
- Migration tool responsibilities:
  directory moves
  file renames
  JSON key rewrites
  embedded path rewrites
  markdown heading/body rewrites
- The tool must be idempotent when re-run on already migrated data
- After migration, remove the tool if it is truly one-off, or keep it only if explicitly needed for future internal re-migrations

### Phase 5: Tests, fixtures, and docs

- Update all tests to use new command names, paths, and schema keys
- Update fixture payloads in unit tests and any golden artifact snapshots
- Update `docs/analyze-results.md` fully
- Update sample scenario YAMLs and generated expectations in tests
- Add a terminology regression test or grep-based quality gate that fails on legacy core terms in first-party code/docs

### Phase 6: Cleanup and deletion

- Delete or rewrite legacy comparison modules using old vocabulary
- Remove stale comments, docstrings, and internal variable names that preserve old terms
- Ensure there is no dual terminology left in code paths reachable by contributors

## Tests and Acceptance Criteria

### Automated tests to update or add

- CLI tests for:
  `agent list`
  `agent validate`
  `scenario init`
  `scenario info`
  `scenario validate`
  `scenario clone-revision`
  `experiment run`
  `experiments list`
  `experiments prune`
- Schema tests for:
  `ScenarioDefinition`
  new metric ids
  new persisted config keys
- Migration tests for:
  one historical experiment fixture rewritten in place
  path rewriting inside JSON and markdown
  idempotent second run of migration tool
- Reporting tests for:
  experiment summary generation
  `docs/analyze-results.md` field/path consistency
- Search-based regression test:
  fail if first-party code/docs still contain banned legacy terms outside allowed exceptions

### Manual validation checklist

- All six historical experiment roots exist only under `experiments/`
- No canonical artifact remains under `evals/`
- `docs/analyze-results.md` references only `experiments/*/...` paths and new field names
- README and Makefile examples are runnable with only new names
- Code navigation from CLI -> schema -> runner -> persisted artifacts uses the same vocabulary without translation

### Final quality gates

- `make quality` passes
- Repo-wide grep for legacy terms passes with the approved allowlist
- Historical experiments load correctly under the migrated schema
- `experiments list` and reporting flows operate against migrated historical artifacts

## Important Changes to Public APIs / Interfaces / Types

### Public commands

- All `task-*`, `suite-*`, `evals-*`, and `provider-*` names change as listed above
- Environment/Make variables change to `SCENARIO`, `SCENARIO_DIR`, `SCENARIO_REVISION`, `STARTER_ROOT`, `EVALUATION_PROFILE`, `RUN_COUNT`, `RUN_PARALLELISM`, `RERUN_UNSCORED`

### Authored spec schema

- `task.yaml` becomes `scenario.yaml`
- `scaffold.root` becomes `starter.root`
- `compliance` becomes `acceptance`
- `metrics.modules[]` becomes `metrics[]`
- metric ids change to the new names above

### Persisted artifact contract

- `suite*.json` files renamed to `experiment*.json`
- config identity fields use `scenario_name`, `scenario_revision`, `agent`, `model`, `evaluation_profile`
- `run_validity` becomes `execution_validity`
- `void*` becomes `unscored*`
- `module*` becomes `metric*`

## Assumptions and Defaults

- Use `scenario`, not `dataset`, for the authored spec concept
- Use `experiment`, not `suite`, for the repeated evaluation grouping
- Keep `run` as the scored execution unit
- Use `trace` only for telemetry/logging, not for scored runs
- Rewrite historical artifacts in place
- Keep revision labels as `v001`, `v002`, etc
- Keep `docs/analyze-results.md` file path unchanged
- Keep `prompt/` and `rules/` directory names unchanged
- Do not edit `docs/references/` in this proposal
- Do not keep compatibility aliases, fallback readers, or dual command surfaces
