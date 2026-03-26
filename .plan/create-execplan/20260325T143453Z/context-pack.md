# Context Pack: Orchestrator Reuse And Code Quality Refactors

- Created: 2026-03-25
- Repo root: `/Users/adamjackson/Projects/raidar`
- Target path: `.`
- Project mode: `brownfield`
- Artifact root: `/Users/adamjackson/Projects/raidar/.plan/create-execplan/20260325T143453Z`
- Workspace root: `/Users/adamjackson/Projects/raidar/.plan/create-execplan/20260325T143453Z/workspace`
- Related links:
  - `/Users/adamjackson/Projects/raidar/.enaible/analyze-code-quality/20260325T100903Z-code-quality-review/final-analysis.md`
  - `/Users/adamjackson/Projects/raidar/AGENTS.md`

## Change Brief (1-3 paragraphs)

The next implementation wave should stay focused on orchestrator/platform behavior and the highest-value maintainability seams in `orchestrator` and `auto_researcher`. The plan must resolve the remaining fast-image warm-path problem across full invocations, extend cache-state visibility beyond raw `run.json`, and reduce concentrated code-quality pressure in CLI/request assembly, promotion-guard logic, adapter duplication, and low-coverage operational modules.

This is a brownfield change in an existing multi-package repo with active smoke and quality workflows. The plan must preserve the public `make ...` interface, keep `make quality` as the completion gate, and use change-scoped verification on the existing runtime/test surface instead of broad new repo-wide test obligations.

`scenarios/**` is now explicitly out of implementation scope. Starter folders are treated as representative delivery-scenario artifacts, not canonical shared product code, so the plan must not schedule scenario edits and must treat starter-folder analysis/code-quality exclusion as a policy-and-tooling alignment task rather than a scenario refactor.

## Requirement Freeze (user-confirmed)

- R1: Produce a brownfield implementation plan for orchestrator spin-up reuse and observability that resolves the remaining live Docker image warm-path issue and extends cache visibility into persisted and aggregate reporting.
- R2: Produce a brownfield refactor plan for the highest-value code-quality hotspots: typed request/option objects for CLI-heavy entrypoints, decomposition of `_promotion_guard`, consolidation of shared provider-adapter behavior, and targeted tests for low-coverage operational modules.
- R3: Exclude scenario files from implementation scope entirely, including starter folders; the plan must not schedule code or structural changes under `scenarios/**`.
- R4: Add and enforce repo guidance that `scenarios/**/starter/**` is excluded from analysis and code-quality checks because those files are representative delivery-scenario artifacts rather than canonical shared product code.
- Confirmed by user at: 2026-03-25T15:02:23Z

## Discovery Inputs

- Intake artifact: `/Users/adamjackson/Projects/raidar/.plan/create-execplan/20260325T143453Z/workspace/context-discovery.md`
- Evidence artifact: `/Users/adamjackson/Projects/raidar/.plan/create-execplan/20260325T143453Z/workspace/context-evidence.json`
- Codemap artifact: `/Users/adamjackson/Projects/raidar/.plan/create-execplan/20260325T143453Z/workspace/context-codemap.md`
- Requirements freeze artifact: `/Users/adamjackson/Projects/raidar/.plan/create-execplan/20260325T143453Z/workspace/requirements-freeze.md`
- Notes:
  - User confirmed Step 1 with response excerpt `proceed`.
  - Online research is not permitted/required for this planning pass; local repository evidence and provided report artifacts are the only sources used.
  - Prior live smoke evidence in this conversation showed baseline/preflight warm hits but continued image misses across separate invocations.

## Guardrails (must-follow)

- Repository rules:
  - Public interface stays repo-root `make ...`.
  - `make quality` remains the completion gate.
  - Do not surface or search `docs/` without explicit user consent.
  - Do not implement backward compatibility or legacy dual paths.
- Security/privacy constraints:
  - Do not log or echo secrets.
  - Use presence checks rather than value printing for required env vars.
- Prohibited actions:
  - No implementation changes under `scenarios/**`.
  - No scenario-starter refactors or dedup work.
  - No quality-gate bypasses or reduced verification standards.

## Research Scope & Recency Policy

- Online research allowed: `no`
- Approved source types: local repository files, local generated analysis artifacts, user-provided conversation findings
- Approved domains/APIs: none for this pass
- Recency expectation: current local repo state as of 2026-03-25
- Exception handling: if an external Docker/Harbor capability question blocks later implementation, pause and request permission instead of guessing

## Evidence Inventory

| Evidence ID | Type | Source | Published | Retrieved | Trust rationale |
| ----------- | ---- | ------ | --------- | --------- | --------------- |
| E1 | local-analysis-artifact | `/Users/adamjackson/Projects/raidar/.enaible/analyze-code-quality/20260325T100903Z-code-quality-review/final-analysis.md` | 2026-03-25 | 2026-03-25 | User-provided retained report for current repo-wide code-quality findings |
| E2 | local-repo-file | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/runner.py:1961` | undated:local-repo-state | 2026-03-25 | Current orchestrator prep context entrypoint |
| E3 | local-repo-file | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/runner.py:1718` | undated:local-repo-state | 2026-03-25 | Current fast-image reuse/build path |
| E4 | local-repo-file | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/runner.py:4134` | undated:local-repo-state | 2026-03-25 | Current orchestrator prep-phase sequencing and cache metadata emission |
| E5 | local-repo-file | `/Users/adamjackson/Projects/raidar/auto_researcher/src/auto_researcher/engine.py:669` | undated:local-repo-state | 2026-03-25 | Current promotion-guard complexity hotspot |
| E6 | local-repo-file | `/Users/adamjackson/Projects/raidar/auto_researcher/src/auto_researcher/cli.py:124` | undated:local-repo-state | 2026-03-25 | Current wide CLI init entrypoint |
| E7 | local-repo-file | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/cli.py:984` | undated:local-repo-state | 2026-03-25 | Current orchestrator experiment-run entrypoint |
| E8 | local-repo-file | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/experiment.py:155` | undated:local-repo-state | 2026-03-25 | Current experiment summary payload assembly seam |
| E9 | local-repo-file | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/storage.py:177` | undated:local-repo-state | 2026-03-25 | Current CSV export payload assembly seam |
| E10 | local-repo-file | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/agents/adapters/claude_code_cli.py:15` | undated:local-repo-state | 2026-03-25 | Current provider adapter duplication anchor |
| E11 | local-repo-file | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/agents/adapters/gemini_cli.py:15` | undated:local-repo-state | 2026-03-25 | Current provider adapter duplication anchor |
| E12 | local-repo-file | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/agents/adapters/codex_cli.py:15` | undated:local-repo-state | 2026-03-25 | Current provider adapter comparison anchor |
| E13 | local-repo-file | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/agents/harbor_agents/fast_cli_agents.py:1` | undated:local-repo-state | 2026-03-25 | Current zero-coverage Harbor fast-agent module |
| E14 | local-repo-file | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/scoring/acceptance.py:239` | undated:local-repo-state | 2026-03-25 | Current acceptance evaluation seam with low coverage |
| E15 | local-repo-file | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/scoring/verification_stability.py:22` | undated:local-repo-state | 2026-03-25 | Current low-coverage verification stability path |
| E16 | local-repo-file | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/watcher/gate_watcher.py:56` | undated:local-repo-state | 2026-03-25 | Current low-coverage gate watcher runtime path |
| E17 | local-repo-file | `/Users/adamjackson/Projects/raidar/AGENTS.md:11` | undated:local-repo-state | 2026-03-25 | Current repo rule excluding scenario starter folders from analysis/code-quality checks |
| E18 | local-repo-file | `/Users/adamjackson/Projects/raidar/Makefile:205` | undated:local-repo-state | 2026-03-25 | Public smoke command anchor for orchestrator-smoke |
| E19 | local-repo-file | `/Users/adamjackson/Projects/raidar/Makefile:216` | undated:local-repo-state | 2026-03-25 | Public smoke-matrix command anchor |

## Verification Baseline & Strategy

- Verification scenario: `brownfield-existing`
- Existing verification commands:
  - `make quality`
  - `make orchestrator-smoke`
  - `make smoke-matrix`
  - `uv run --project orchestrator python -m pytest orchestrator/tests/test_runner_metrics.py orchestrator/tests/test_runner_harbor_env_and_cleanup.py -q`
- User decision when verification missing: `n/a-existing`
- Planned verification scope:
  - Keep verification change-scoped to orchestrator and auto-researcher runtime seams plus policy/config exclusions.
  - Preserve repo-wide `make quality` as the final gate.
  - Add repeated smoke coverage for a persisted reusable fast-image artifact path that does not depend on Docker daemon-local image retention.
- Mandatory smoke gate command:
  - `make orchestrator-smoke`
- Smoke gate expected success signal:
  - Command exits 0 and the final implementation shows a stable orchestrator spin-up path without rebuilding the fast image on the second identical invocation.

## Established Library Comparison (required for greenfield; optional for brownfield)

Not used for this brownfield plan. The current refactor direction should prefer existing stdlib dataclasses, existing repo schemas, and existing adapter abstractions over adding new planning dependencies.

## Existing Change Surface (required for brownfield; optional for greenfield)

| Area | File anchor | Current behavior | Integration concern | Evidence IDs |
| ---- | ----------- | ---------------- | ------------------- | ------------ |
| Orchestrator prep caching | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/runner.py:1961` | Builds run context from shared baseline cache before copying into per-run workspace | Warm-path metadata and image behavior must stay aligned with persisted run metadata and smoke assertions | E2,E4 |
| Orchestrator fast-image reuse | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/runner.py:1718` | Reuses or builds the fast task image and writes build logs | Live smoke still indicates image non-reuse across invocations, so Checkpoint A should replace daemon-local dependence with a persisted reusable image artifact flow | E3,E4 |
| Orchestrator prep sequencing | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/runner.py:4134` | Serializes prep, preflight, Harbor bundle creation, and fast-image ensure | Changes here affect run metadata, smoke timing, and cache-state reporting simultaneously | E4 |
| Auto-researcher promotion logic | `/Users/adamjackson/Projects/raidar/auto_researcher/src/auto_researcher/engine.py:669` | Performs validity, performance, metric-regression, and score-improvement decisions in one method | Decomposition must preserve promotion semantics used by both research and confirmation runs | E5 |
| Auto-researcher CLI intake | `/Users/adamjackson/Projects/raidar/auto_researcher/src/auto_researcher/cli.py:124` | Marshals a wide Click signature into `ObjectiveInitRequest` | Request-object extraction must not break CLI output contract or existing approval/run subcommands | E6 |
| Orchestrator CLI experiment entry | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/cli.py:984` | Accepts a broad run surface and delegates experiment execution | Typed option extraction must preserve public `make`-backed CLI compatibility | E7 |
| Experiment summary assembly | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/experiment.py:155` | Builds experiment summary dict with config/sample/rerun aggregation | Refactoring must preserve storage/report consumers and rerun metadata shape | E8 |
| CSV export serialization | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/storage.py:177` | Flattens scorecard metadata into CSV rows | Additional cache metadata surfacing must avoid destabilizing existing report consumers | E9 |
| Provider adapter family | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/agents/adapters/claude_code_cli.py:15` | Repeats provider-specific validation/env/workspace patterns across adapters | Consolidation must preserve provider auth rules and Harbor harness differences | E10,E11,E12 |
| Low-coverage runtime seams | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/agents/harbor_agents/fast_cli_agents.py:1` | Contains Harbor fast agents with minimal current automated coverage | Test uplift must avoid scenario edits and should focus on deterministic unit coverage | E13,E14,E15,E16 |
| Analysis policy | `/Users/adamjackson/Projects/raidar/AGENTS.md:11` | Documents starter-folder exclusion from analysis/code-quality scrutiny | Tooling/config must align with the policy so reports do not keep surfacing starter noise | E17 |

## Repo Facts (execution-relevant only)

- Languages/frameworks:
  - Python orchestrator and auto-researcher services
  - TypeScript/Node scenario starters exist but are excluded from implementation scope
- Package manager(s):
  - `uv` for Python projects
  - `bun` for starter runtime verification
- Build tooling:
  - repo-root `make`
  - Docker/Harbor for smoke execution
- Test tooling:
  - `pytest`
  - `ruff`, `mypy`, `lizard`
- Key environment variables/config files:
  - `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `CLAUDE_CODE_API_KEY`, `GEMINI_API_KEY`
  - `CODEX_CLI_PATH`, `CLAUDE_CODE_CLI_PATH`, `GEMINI_CLI_PATH`
  - `AGENTS.md`, `Makefile`, `orchestrator/pyproject.toml`

## Dependency Preconditions

| Dependency | Purpose | Check command | Install command | Source | Expected success signal |
| ---------- | ------- | ------------- | --------------- | ------ | ----------------------- |
| Repo Python environment | Run orchestrator and auto-researcher tests/gates | `uv --version` | `make env-setup` | repo root `Makefile` | `uv` available and project env bootstrapped |
| Docker daemon | Run Harbor-backed smoke verification | `docker info` | `n/a (user-managed daemon startup)` | local Docker installation | daemon responds successfully |
| Orchestrator smoke entrypoint | Verify public smoke workflow still executes | `make smoke-dry-run-check` | `n/a` | repo root `Makefile` | dry-run includes orchestrator-smoke and smoke-matrix shapes |

## Execution Command Catalog

| Purpose | Command | Expected success signal |
| ------- | ------- | ----------------------- |
| Install/setup | `make env-setup` | repo environment bootstrapped successfully |
| Dependency check | `docker info` | dependency present |
| Dependency install (if missing) | `n/a (start Docker Desktop / user-managed daemon)` | dependency installed |
| Smoke test (mandatory) | `make orchestrator-smoke` | orchestrator smoke completes successfully |
| Run | `make smoke-matrix` | matrix smoke command executes the public smoke scenario across the model selector |
| Tests | `uv run --project orchestrator python -m pytest orchestrator/tests/test_runner_metrics.py orchestrator/tests/test_runner_harbor_env_and_cleanup.py orchestrator/tests/test_cli_commands.py -q` | targeted orchestrator tests pass |
| Quality gate | `make quality` | gate passes |

## Code Map (line-numbered)

List only the places the executor must touch. Prefer `path:line` anchors.

| Area | File anchor | What it contains | Why it matters | Planned change |
| ---- | ----------- | ---------------- | -------------- | -------------- |
| Scenario-exclusion policy | `/Users/adamjackson/Projects/raidar/AGENTS.md:11` | Repo-level planning/analysis constraints | Align analysis/code-quality configs with the new hard exclusion | Extend enforcement beyond documentation where needed |
| Public smoke entrypoints | `/Users/adamjackson/Projects/raidar/Makefile:205` | `orchestrator-smoke` public target | Primary runtime sanity command for orchestrator changes | Keep smoke stable and use it for repeated warm-path verification |
| Public smoke matrix | `/Users/adamjackson/Projects/raidar/Makefile:216` | `smoke-matrix` public target | Secondary public coverage path for model-matrix validation | Ensure no refactor breaks matrix invocation shape |
| Orchestrator prep context | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/runner.py:1961` | Baseline/preflight prep context creation | Main shared cache handoff point | Refine cache/image observability and reuse decision flow |
| Fast image ensure | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/runner.py:1718` | Content-addressed fast image ensure/build | Current live warm-path gap | Implement a generic persisted reusable image-artifact path |
| Prep phase orchestration | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/runner.py:4134` | Prep timing and cache metadata emission | Source of persisted Harbor cache metadata | Extend surfaced cache state and aggregate hooks |
| Orchestrator CLI entry | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/cli.py:984` | Experiment run CLI command | Typed option extraction hotspot | Introduce request object without changing public behavior |
| Experiment summary | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/experiment.py:155` | Experiment summary dict construction | Needs typed/result-object cleanup and cache-metadata surfacing | Split payload assembly into smaller typed helpers |
| Storage export | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/storage.py:177` | CSV export row assembly | Needs aggregate cache metadata surfacing without row-shape drift mistakes | Extract row builder helpers and add targeted coverage |
| Auto-researcher CLI intake | `/Users/adamjackson/Projects/raidar/auto_researcher/src/auto_researcher/cli.py:124` | Wide init command signature | Highest parameter-count hotspot | Introduce typed request-building helper |
| Promotion guard | `/Users/adamjackson/Projects/raidar/auto_researcher/src/auto_researcher/engine.py:669` | Concentrated promotion decision logic | Highest complexity hotspot | Split into rule evaluators with preserved guard semantics |
| Adapter base comparisons | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/agents/adapters/claude_code_cli.py:15` | Provider adapter implementation family | Duplication hotspot | Consolidate shared env/workspace behavior behind reusable helpers/base contracts |
| Fast Harbor agents | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/agents/harbor_agents/fast_cli_agents.py:1` | Zero-coverage Harbor fast agents | Operational regression risk | Add deterministic unit coverage for agent-specific behavior |
| Acceptance scoring | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/scoring/acceptance.py:239` | Acceptance evaluation flow | Low-coverage scoring seam | Add targeted tests around deterministic and judge parsing paths |
| Verification stability | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/scoring/verification_stability.py:22` | Gate-event stability scoring | Low-coverage metric seam | Add direct score/evaluator tests |
| Gate watcher | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/watcher/gate_watcher.py:56` | Gate execution and failure categorization | Low-coverage operational path | Add targeted tests for timeout/not-found/repeat-failure cases |

## Requirement to Evidence Traceability

| Requirement ID | Requirement | Evidence IDs | Context section(s) | Planned task refs |
| -------------- | ----------- | ------------ | ------------------ | ----------------- |
| R1 | Resolve orchestrator warm-path reuse and observability gaps | E2,E3,E4,E18,E19 | Existing Change Surface, Verification Baseline & Strategy, Code Map, Risk Register | P1-T1,P1-T2,P2-T4,P2-T5,P4-T11 |
| R2 | Address concentrated code-quality hotspots and low-coverage runtime seams | E1,E5,E6,E7,E8,E9,E10,E11,E12,E13,E14,E15,E16 | Existing Change Surface, Code Map, Risk Register | P1-T3,P2-T6,P2-T7,P3-T8,P3-T9,P4-T10 |
| R3 | Keep scenarios fully out of implementation scope | E17 | Guardrails, Code Map, Risk Register | P1-T1,P1-T2,P1-T3 |
| R4 | Enforce starter-folder exclusion in analysis/code-quality tooling and guidance | E1,E17 | Guardrails, Existing Change Surface, Code Map | P1-T2,P4-T10 |

## Contracts & Interfaces

Only include what the change touches:

- CLI commands and arguments:
  - `auto-researcher init`
  - `raidar experiment run`
  - public `make orchestrator-smoke`
  - public `make smoke-matrix`
- Runtime orchestration contracts:
  - `prepare_run_context -> _prepare_workspace_phase -> _ensure_fast_task_image`
  - adapter interface methods: `validate`, `runtime_env`, `prepare_workspace`, `harbor_harness_import_path`
- Experiment/reporting payloads:
  - experiment summary dict in `experiment.py`
  - CSV export rows in `storage.py`

## Risk Register

| Risk | Impact | Mitigation | Verification command | Evidence IDs |
| ---- | ------ | ---------- | -------------------- | ------------ |
| Persisted fast-image artifact flow adds cache lifecycle and pruning complexity | High | Define explicit artifact metadata, invalidation inputs, and pruning ownership before broad refactor | `make orchestrator-smoke` run twice | E3,E4,E18 |
| Typed request/result extraction changes CLI/report payload shapes unexpectedly | High | Preserve existing command/output contracts and add targeted tests around CLI and summary/export seams | `uv run --project orchestrator python -m pytest orchestrator/tests/test_cli_commands.py orchestrator/tests/test_experiment.py orchestrator/tests/test_storage.py -q` | E6,E7,E8,E9 |
| Adapter consolidation breaks provider-specific auth/model behavior | Medium | Extract only common behavior proven identical across adapters and keep provider validation specialized | `uv run --project orchestrator python -m pytest orchestrator/tests/test_codex_cli_adapter.py orchestrator/tests/test_claude_code_cli_adapter.py orchestrator/tests/test_gemini_cli_adapter.py -q` | E10,E11,E12 |
| Coverage uplift drifts into scenario changes | Medium | Constrain new tests to orchestrator and auto-researcher units/integration seams only | `git diff --name-only` plus targeted pytest commands | E13,E14,E15,E16,E17 |
| Starter-folder exclusion remains documentation-only and reports keep surfacing scenario noise | Medium | Include config/pipeline enforcement work item and verify future analysis scope excludes starter folders | `make quality` plus targeted analyzer config checks | E1,E17 |
