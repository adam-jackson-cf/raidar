# Raidar Charter Review

Date: 2026-06-10
Status: repo-grounded charter review and backlog proposal

## Scope and Evidence Rules

This review is scoped to Raidar's charter, coverage, and future backlog. It does not implement backlog items, change scorer code, change scenario files, retire scorer IDs, create new scorer families, or run live benchmark/scenario experiments.

Evidence used:

- Public entrypoint and repo model: `README.md`, `Makefile`, `make help`.
- Reference model docs permitted by the goal: `docs/references/metrics.md`, `docs/references/new-scenario.md`, `docs/references/orchestration-flow.md`, `docs/references/new-harness.md`, `docs/references/raidar-framework-comparison.md`.
- Code contracts: `orchestrator/src/raidar/schemas/scenario.py`, `orchestrator/src/raidar/schemas/scorecard.py`, `orchestrator/src/raidar/scorers/*`, `orchestrator/src/raidar/runtime/scorecard.py`, `orchestrator/src/raidar/runtime/process_metrics.py`, `orchestrator/src/raidar/matrix.py`, `orchestrator/src/raidar/agents/*`, `orchestrator/src/raidar/commands/*`.
- Scenario contracts, excluding `starter/`: `scenarios/*/*/scenario.yaml`, `scenarios/*/*/prompt/task.md`, `scenarios/*/*/rules/*`.
- Stored matrix configs: `matrices/*.yaml`.
- Existing generated evidence surfaces only, without running new experiments: `experiments/**/run.json`, `experiments/**/experiment-summary.json`, `benchmark-view/src/data.json`, `benchmark-view/scripts/build-data.mjs`.
- Test evidence: `orchestrator/tests/*`.

Explicit exclusions for this pass:

- No other `docs/todos/` directories were read or searched.
- Scenario `starter/` directories were not inspected because repo instructions exclude them from default analysis and the review can classify coverage from scenario contracts and retained experiment evidence.
- Existing benchmark artifacts were summarized, not treated as proof that new live runs are healthy today.

## Charter Statement

Raidar is a scenario evaluation suite for agentic software-delivery work. Its core comparison unit is:

> scenario revision x AgentSpec x repeated run evidence

where `AgentSpec` means harness plus model. Raidar should help answer which harness/model combinations deliver better engineering outcomes under a scenario contract, and why: artifact correctness, requirement satisfaction, verification discipline, visual fidelity, workflow/process behavior, reliability, and resource efficiency.

Raidar is not primarily a generic prompt-output evaluator. It is most valuable when scenarios model real delivery tasks with a workspace, rules, verification commands, required artifacts, scorer definitions, retained process traces, and comparable experiment artifacts.

## Evaluation Domain

Raidar should cover both delivered artifacts and delivery-process quality.

Delivered artifact outcomes include:

- runnable code and project files;
- required paths, exports, behavior, tests, and coverage;
- visual implementation against a reference;
- semantic satisfaction of scenario requirements;
- behavior-preserving refactors, bug fixes, and test additions.

Delivery-process behaviors include:

- selected harness rules and instruction files;
- command execution, failed command categories, and verification retries;
- completion claims versus gate state;
- atomic commit discipline where scenarios require it;
- retained planning artifacts and adherence to the plan;
- orchestration or delegation artifacts when the task uses multiple workers;
- token, duration, command-count, and repeatability/resource efficiency signals.

## Evaluation Principles

1. **Scenario contracts are source of truth.** A scenario revision defines prompt, rules, starter root, verification, requirements, visual reference, and attached scorers. Scoring behavior should not live in harness adapters.
2. **AgentSpec is an experimental variable.** Harness and model are compared together because harness rules, CLI behavior, auth/runtime behavior, and model output jointly affect delivery.
3. **Prefer deterministic scoring where evidence is sufficient.** File presence, command results, coverage, visual diffs, requirement checks, command records, token counts, and retained artifacts should be scored deterministically when they can prove the claim.
4. **Use LLM-as-judge only for residual semantic judgment.** Judge-backed metrics are appropriate for plan adherence or semantic requirement adherence when deterministic checks cannot fully prove intent. Judge files should stay scorer-owned, have explicit rubric/output contracts, and consume retained evidence rather than impressions.
5. **Use hybrid scoring for semantic work.** Semantic judge metrics should be constrained by deterministic prerequisites: missing prompt, missing implementation evidence, failed functional execution, or missing retained artifacts should cap or fail the judge-backed score.
6. **Retain process evidence.** Claims about workflow quality need concrete artifacts: gate history, command records, process metrics, git commit records, plan packets, handoff logs, or workspace diffs.
7. **Keep revisions comparable.** Use revisions inside an existing scenario root for prompt/gate/scorer/rule refinements to the same task. Use new scenario roots for materially different delivery activities.
8. **Separate quality and efficiency.** `quality_score`, `resource-efficiency`, and composite score serve different decision needs. Efficiency should not hide weak delivered quality.
9. **Prefer simplest-sufficient planning/orchestration evidence.** Start with static retained artifacts such as plan packets, handoff logs, and delegation summaries before requiring an interactive orchestration runtime.

## Current Public Workflow Surface

The repo-root `Makefile` is the public interface. The relevant review evidence is that it exposes discovery and validation through `make help`, scenario inspection through `make scenario-list` and `make scenario-info`, scenario validation through `make scenario-validate`, scenario creation/revision workflows, harness validation, experiment/matrix execution, experiment listing/pruning, benchmark-view generation/serving, and `make quality`.

The implementation command behind the Makefile is intentionally not the human workflow surface. Charter and backlog items should be framed around public `make` targets when they require future execution.

## Current Scenario and Revision Coverage

`make scenario-list` reports three authorable scenario roots and six revisions.

| Scenario revision | Delivery activity | Contract evidence | Attached scorers | Coverage assessment |
| --- | --- | --- | --- | --- |
| `hello-world-smoke@v001` | Harness/runtime smoke and minimal workspace mutation | Category `agent-integration`, 5-minute timeout, two requirements, typecheck/lint required commands, rules files for supported harnesses | `typescript-code-task@1` weight `0.01`, `resource-efficiency@1` weight `0.99` | Good for transport and Harbor orchestration smoke. Not a meaningful delivery-quality benchmark because efficiency dominates and task scope is intentionally tiny. |
| `homepage-implementation@v001` | Greenfield UI/design-to-code delivery | Category `greenfield-ui`, visual reference, typecheck/lint/coverage/build gates, ten requirements, atomic commits required | `design-to-code@1` weight `0.9`, `resource-efficiency@1` weight `0.1` | Strongest current artifact-quality scenario: visual, functional, tests, artifacts, stability, efficiency. Limited to one UI shape. |
| `homepage-implementation@v002` | Refined UI/design-to-code prompt and verification discipline | Same scorer blend and gates as `v001`, compact verification-first prompt, ten requirements, atomic commits required | `design-to-code@1`, `resource-efficiency@1` | Useful revision for prompt/rules refinement measurement. Still not an explicit rules-intervention experiment because only one revision pair and no isolated treatment/control variable. |
| `skill-benchmark-coding-test@v001` | Greenfield TypeScript utility implementation | Category `greenfield-code`, typecheck/lint/test/coverage gates, eight requirements, atomic commits not required | `typescript-code-task@1` weight `0.98`, `resource-efficiency@1` weight `0.02` | Baseline code-delivery scenario. Scope is narrow: one TypeScript utility. |
| `skill-benchmark-coding-test@v002` | TypeScript utility with stronger verification/lint guidance | Same scorer blend as `v001`, explicit verification instructions, eight requirements | `typescript-code-task@1`, `resource-efficiency@1` | Good prompt/rules revision candidate for verification discipline. Still not sufficient to attribute causality to a specific skill/rule/lint intervention. |
| `skill-benchmark-coding-test@v003` | Concise TypeScript utility with requirements scorer | Typecheck/lint/test/coverage gates, eight requirements, concise verification checklist | `typescript-code-task@1` weight `0.78`, `requirements@1` weight `0.20`, `resource-efficiency@1` weight `0.02` | First active requirements-scorer scenario. Useful semantic-adherence coverage, but only one simple TypeScript task exercises `requirements@1`. |

Current scenario rules provide harness-specific instruction surfaces (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `copilot-instructions.md`, `user-rules-setting.md`). Rules evolved across homepage and skill benchmark revisions. That is useful evidence for rule interventions, but current contracts do not isolate a rule or skill treatment from other prompt/scenario changes.

## Current Matrix Coverage

Current stored matrix configs cover:

- `hello-world-smoke-codex-gpt55.yaml`: Codex CLI with `gpt-5.5` low/medium on smoke `v001`.
- `hello-world-smoke-trio.yaml`: Codex, Gemini, and Claude on smoke `v001`.
- `homepage-implementation-codex-gpt55.yaml`: Codex CLI `gpt-5.5` low/medium across homepage `v001` and `v002`.
- `skill-benchmark-coding-test-v003-codex-gpt55.yaml`: Codex CLI `gpt-5.5` low/medium across skill benchmark `v001`, `v002`, and `v003`.

All listed configs define `matrix.id`, `matrix.scenario`, `matrix.experiment`, and entries with revision plus nested agent fields. Current stored matrices are useful for smoke, revision comparison, and Codex reasoning-effort comparisons. They do not yet provide broad multi-harness comparisons for the main delivery-quality scenarios, and most use `repeats: 1`, so they should be treated as smoke or early comparison matrices rather than high-confidence benchmark samples.

## Current Scorer Coverage

The code-backed active scorer registry currently exposes nine active scorer definitions.

| Active scorer | Category | Metrics | Scenario coverage | Assessment |
| --- | --- | --- | --- | --- |
| `design-to-code@1` | quality | `visual-regression`, `functional`, `test-coverage`, `verification-stability`, `artifact-checks` | Homepage `v001`, `v002` | Well represented for one UI scenario. Needs broader visual/responsive/a11y variation. |
| `typescript-code-task@1` | quality | `functional`, `code-quality`, `test-coverage`, `artifact-checks`, `verification-stability` | Smoke `v001`; skill benchmark `v001`-`v003` | Main code-task scorer. Current scenario scope is TypeScript-only and simple. |
| `requirements@1` | quality | `requirements-coverage`, `requirements-adherence` | Skill benchmark `v003` | Under-exercised. Hybrid design is appropriate, but scenario coverage is thin. |
| `resource-efficiency@1` | efficiency | `resource-efficiency` | All six current scenario revisions | Well represented as cross-cutting ranking signal. Needs guardrails so efficiency never masks weak quality. |
| `bugfix@1` | quality | `defect-resolution`, `regression-protection`, `change-containment`, `verification-stability`, `defect-evidence-completeness` | No scenario coverage | Active and unit-tested, but not benchmarked by an authorable scenario. Material backlog gap. |
| `plan-to-code@1` | quality | `plan-adherence`, `planned-scope-coverage`, `acceptance-evidence-completeness`, `functional`, `verification-stability` | No scenario coverage | Active and unit-tested. Needs retained plan artifact scenario/platform representation. Material planning gap. |
| `python-code-task@1` | quality | `functional`, `code-quality`, `test-coverage`, `artifact-checks`, `verification-stability` | No scenario coverage | Active but not represented by current scenarios. Material language/domain gap. |
| `refactor@1` | quality | `behavior-preservation`, `structural-improvement`, `public-contract-stability`, `change-containment`, `verification-stability` | No scenario coverage | Active and unit-tested, but no scenario root exercises behavior-preserving structural work. |
| `test-generation@1` | quality | `requirement-mapping`, `assertion-strength`, `coverage-lift`, `production-code-guardrail`, `verification-stability` | No scenario coverage | Active and unit-tested, but no scenario root measures test-writing delivery. |

Scorer unit and behavior tests cover registration, artifact metrics, LLM judge path safety/redaction, requirements coverage, TypeScript artifact checks, and concept-specific evidence for proposed scorer families. The material coverage gap is not only unit-test coverage; it is absence of authorable benchmark scenarios and stored matrices for five active scorer families.

## Platform Evidence Surfaces

Current platform evidence is stronger than the current scenario suite.

Scenario model evidence:

- `ScenarioDefinition` has fields for scenario identity, revision, description, difficulty, category, timeout, starter, verification, requirements, visual config, scorers, and prompt.
- `VerificationConfig` includes max gate failures, coverage threshold, min quality score, required commands, setup actions, gates, and workflow config.
- `RequirementsConfig` owns requirement items with deterministic checks and required test evidence.
- `ScenarioScorerRef` attaches code-backed scorer IDs/versions with scenario-level weights and metric-specific config.

Run/scorecard evidence:

- `EvalRun` retains config, duration, termination state, scorecard, traces, and gate history.
- `Scorecard` retains functional, visual, verification stability, test coverage, requirements coverage, execution validity, performance gates, resource efficiency, `metric_scores`, `scorer_results`, `quality_score`, `composite_score`, and diagnostics.
- Existing `run.json` metadata includes run, starter, scenario, Harbor, harness, verifier, process, evidence, and workspace keys.
- Process metadata includes token counts, command counts, failed command categories, verification rounds, required/executed verification commands, missing commands, first-pass verification outcomes, repeated verification failures, and git commit bypass commands.
- Completion-claim integrity and atomic-commit integrity are represented in execution-validity/performance-gate style checks when scenario workflow requires them.

Experiment and dashboard evidence:

- Existing generated evidence includes 959 `run.json` files and 152 `experiment-summary.json` files under `experiments/`.
- `benchmark-view/src/data.json` exposes rows, scenario metadata, scenario diffs, and revision deltas. Current data contains 154 rows and 41 scenario/revision metadata entries.
- Dashboard-only synthetic scenarios include regression, variance, sample-size, linear-progress, performance/cost, and model-crossover cases. These are valuable comparison fixtures but are not currently authorable scenario roots under `scenarios/`.

Harness evidence:

- `make harness-list` reports `claude-code`, `codex-cli`, `gemini`, `cursor`, `copilot`, and `pi`, each mapped to rule files and supported model namespaces.
- Smoke matrices include a basic cross-harness smoke comparison, but main delivery-quality scenario matrices are Codex-only at present.

## Delivery-Activity Taxonomy and Coverage Map

| Delivery activity | Current coverage | Evidence surfaces | Gap severity |
| --- | --- | --- | --- |
| Harness/runtime integration | Covered by `hello-world-smoke@v001`, smoke matrices, harness registry, Harbor artifact metadata | Scenario contract, rules injection, run metadata, harness artifacts | Low for smoke; medium for deeper harness behavior |
| Greenfield TypeScript code delivery | Covered by `skill-benchmark-coding-test@v001`-`v003` | Typecheck/lint/test/coverage gates, artifact checks, requirements items, TS scorer | Medium: only one simple utility domain |
| Greenfield UI/design-to-code delivery | Covered by `homepage-implementation@v001`-`v002` | Visual reference, visual score, semantic tests, artifact checks, atomic commits | Medium: one page type; limited responsive/a11y/product complexity |
| Requirement adherence | Partially covered by `requirements@1` in skill `v003` | Deterministic requirements coverage plus LLM-as-judge requirements adherence | High: only one simple scenario uses it |
| Resource efficiency | Covered across all current scenario revisions | Process metrics, token counts, command counts, verification rounds | Low for instrumentation; medium for policy interpretation |
| Verification discipline | Covered as gates, required commands, verification stability, process metrics | Gate history, command records, scorecard stability, completion integrity | Medium: process metrics exist but few scenarios isolate behaviors |
| Linting/process-rule interventions | Indirectly visible through prompt/rule revisions | Scenario rules, prompts, lint gates, failed command categories | High: no controlled intervention benchmark |
| Skill/system-rule effects | Indirectly visible in rules files | Harness-specific rule files and scenario revisions | High: no explicit skill/rule treatment/control design |
| Bug fixing | Active scorer exists; no scenario | `bugfix@1` unit evidence | High |
| Refactoring | Active scorer exists; no scenario | `refactor@1` unit evidence | High |
| Test generation | Active scorer exists; no scenario | `test-generation@1` unit evidence | High |
| Python delivery | Active scorer exists; no scenario | `python-code-task@1` registry definition | Medium-high |
| Planning-to-code | Active hybrid scorer exists; no scenario | `plan-to-code@1`, plan judge file, unit tests | High |
| Orchestration/delegation | Not directly represented | Potentially retained artifacts and harness traces | High, but start with static artifact representation |
| Benchmark trend/revision analysis | Visible in benchmark dashboard and synthetic artifact data | `benchmark-view/src/data.json`, benchmark rows/diffs/deltas | Medium: synthetic dashboard-only scenarios are not scenario roots |

## Material Gaps

1. **Five active scorer families have no scenario coverage.** `bugfix@1`, `plan-to-code@1`, `python-code-task@1`, `refactor@1`, and `test-generation@1` are active definitions but are not attached by any current scenario revision.
2. **Requirements scoring is under-exercised.** `requirements@1` appears only in `skill-benchmark-coding-test@v003`, a small TypeScript utility task.
3. **Planning and orchestration are not benchmarked as delivery-process activities.** `plan-to-code@1` and its judge role indicate intended coverage, but there is no scenario contract or retained artifact schema that makes planning evidence first-class across runs.
4. **Skill/rule/linting interventions are not isolated.** Scenario revisions include changed prompts and rules, but no treatment/control design isolates the effect of a skill, a ruleset, or linting-process intervention.
5. **Main delivery-quality matrices are narrow.** Current meaningful delivery matrices are Codex-only and mostly single-repeat. Smoke coverage crosses harnesses, but quality scenarios do not yet exercise multi-harness comparisons.
6. **Synthetic benchmark evidence is not backed by authorable scenario roots.** The dashboard has useful synthetic comparison scenarios, but they live as generated benchmark data rather than scenario contracts under `scenarios/`.
7. **Visual/UI coverage is narrow.** Homepage scenarios test one page with one reference; they do not cover responsive behavior, accessibility, multi-page flows, or design-system refactors.
8. **Completion/workflow process scoring is partly platform-level, not scenario-visible.** Execution validity and process metadata capture completion and command behavior, but scenarios do not yet use those process behaviors as primary benchmark objectives.
9. **Reference docs lag parts of the active code model.** Some reference docs list only older active scorers or use stale requirement-field language relative to the current schema. This review should not restructure broad docs, but the mismatch is relevant residual risk for future authors.

## Backlog Proposals

The following are high-level backlog proposals only. They should become separate implementation goals before any code or scenario changes are made.

### 1. New scenario root: targeted bug fix

- **Purpose:** Measure an agent's ability to identify and fix a scoped defect without broad drift.
- **Delivery activity measured:** Bug fixing, regression protection, change containment, evidence completeness.
- **Candidate scorers:** `bugfix@1` plus `resource-efficiency@1`; optionally `requirements@1` if the bug has semantic acceptance requirements.
- **Required evidence:** Failing baseline test or defect-linked requirement, changed files, regression tests, passing verification gates, retained defect-evidence fields, workspace diff.
- **Why current coverage is insufficient:** `bugfix@1` is active and tested, but no scenario root exercises it.
- **Work type:** Scenario work, with no new scorer family required.

### 2. New scenario root: behavior-preserving refactor

- **Purpose:** Measure structural improvement while preserving public behavior.
- **Delivery activity measured:** Refactoring, public contract stability, change containment, verification stability.
- **Candidate scorers:** `refactor@1` plus `resource-efficiency@1`; optionally `requirements@1` for semantic API obligations.
- **Required evidence:** Existing tests, public API fixture, before/after structural target, changed file list, passing gates, no broad unrelated changes.
- **Why current coverage is insufficient:** `refactor@1` is active but unused by scenarios.
- **Work type:** Scenario work; possible scorer refinement later if structural evidence proves too weak.

### 3. New scenario root: test generation and coverage lift

- **Purpose:** Measure whether an agent can add meaningful tests without production-code drift.
- **Delivery activity measured:** Requirement mapping, assertion strength, coverage lift, production-code guardrail, verification stability.
- **Candidate scorers:** `test-generation@1`, `requirements@1`, and `resource-efficiency@1`.
- **Required evidence:** Baseline coverage, target requirements, changed test files, unchanged or tightly bounded production files, coverage report, passing test gates.
- **Why current coverage is insufficient:** `test-generation@1` has unit evidence but no benchmark scenario.
- **Work type:** Scenario work, likely with existing scorer.

### 4. New scenario root: Python code task

- **Purpose:** Extend code-delivery measurement beyond TypeScript.
- **Delivery activity measured:** Python implementation correctness, code quality, tests, artifact paths, verification stability.
- **Candidate scorers:** `python-code-task@1` plus `resource-efficiency@1`.
- **Required evidence:** Python package starter, pytest/unit tests, lint/typecheck if appropriate, artifact paths, coverage where available.
- **Why current coverage is insufficient:** `python-code-task@1` is active but unused; current code-task scenarios are TypeScript-only.
- **Work type:** Scenario work.

### 5. New scenario root or platform-backed revision: plan-to-code artifact benchmark

- **Purpose:** Measure whether implementation adheres to an approved plan and retains acceptance evidence.
- **Delivery activity measured:** Planning quality, plan adherence, scope coverage, implementation against plan, acceptance evidence completeness.
- **Candidate scorers:** `plan-to-code@1`, `requirements@1`, and `resource-efficiency@1`.
- **Required evidence:** Retained plan packet, explicit approved scope, changed files, plan-to-change mapping, acceptance evidence, functional gates, judge output for plan adherence.
- **Why current coverage is insufficient:** `plan-to-code@1` exists and has judge/unit evidence, but no scenario contract requires a retained plan artifact.
- **Work type:** Mixed scenario/platform work if current artifact capture cannot reliably retain plan packets. Start static; do not require interactive planning mode first.

### 6. Scenario revision: expand requirements scoring into UI/product work

- **Purpose:** Exercise `requirements@1` on semantic product/UI requirements beyond a utility function.
- **Delivery activity measured:** Semantic product requirement adherence, test evidence mapping, visual/functional contract satisfaction.
- **Candidate scorers:** `design-to-code@1`, `requirements@1`, `resource-efficiency@1`.
- **Required evidence:** Homepage requirement items with deterministic checks and test evidence, visual reference, semantic tests, requirements judge output.
- **Why current coverage is insufficient:** Requirements scoring is represented only in `skill-benchmark-coding-test@v003`.
- **Work type:** Scenario revision inside `homepage-implementation` if task remains same; new root if the product/UI task materially changes.

### 7. Scenario revision pair: rules/linting intervention benchmark

- **Purpose:** Measure whether stronger rules or linting-process instructions improve delivery without overfitting the task.
- **Delivery activity measured:** Rule following, lint failure recovery, verification-loop discipline, completion discipline.
- **Candidate scorers:** Existing code-task or design-to-code scorer plus `requirements@1` and `resource-efficiency@1`; process behavior can be read from execution-validity and process metadata.
- **Required evidence:** Treatment/control revisions with one intentional ruleset or linting intervention difference, identical starter/task intent, lint gate history, failed command categories, repeated verification failures, completion integrity.
- **Why current coverage is insufficient:** Current revisions change multiple prompt/rule dimensions and cannot isolate intervention effect.
- **Work type:** Scenario revision work; platform work only if process metadata is insufficient for attribution.

### 8. Platform capability proposal: static orchestration evidence packet

- **Purpose:** Enable planning/orchestration benchmarks without requiring an interactive orchestration runtime first.
- **Delivery activity measured:** Delegation design, handoff quality, worker-result integration, decision logging, plan adherence.
- **Candidate scorers:** `plan-to-code@1`, `requirements@1`, possible future scorer refinement once evidence shape is validated.
- **Required evidence:** `plan.md`, delegation map, handoff log, worker summaries, final integration notes, changed files, acceptance evidence, gate results.
- **Why current coverage is insufficient:** No first-class retained orchestration artifact contract exists. Harness traces alone are not a stable scoring contract.
- **Work type:** Platform proposal first, then scenario work. Use static artifacts before interactive mode.

### 9. Platform/scenario proposal: dashboard-only synthetic benchmark alignment

- **Purpose:** Preserve useful synthetic comparison behaviors as explicit contracts or clearly label them as dashboard fixtures.
- **Delivery activity measured:** Regression detection, variance, sample-size effect, cost/performance tradeoff, model crossover, recovery after regression.
- **Candidate scorers:** Depends on whether synthetic cases become executable scenarios; otherwise dashboard/benchmark-data validation only.
- **Required evidence:** Scenario roots or fixture schema, source provenance, expected trend semantics, dashboard rows/deltas.
- **Why current coverage is insufficient:** `benchmark-view` exposes synthetic scenarios that are not authorable roots under `scenarios/`.
- **Work type:** Mixed platform/documentation/scenario proposal; not scorer work by default.

### 10. Matrix backlog: multi-harness quality scenario comparisons

- **Purpose:** Compare harness/model behavior on meaningful delivery-quality tasks, not only smoke.
- **Delivery activity measured:** Harness runtime impact, model/harness interaction, reliability, cost-quality tradeoffs.
- **Candidate scorers:** Existing scenario scorer profiles.
- **Required evidence:** Stored matrix configs for homepage and skill benchmark across Codex/Gemini/Claude where auth/runtime support is available, repeat counts high enough for sample adequacy, experiment summaries.
- **Why current coverage is insufficient:** Smoke crosses harnesses; main quality scenarios are Codex-only in stored configs.
- **Work type:** Matrix/scenario operations work. Running the matrices is a separate explicit approval goal.

### 11. Scenario revision or new root: responsive/accessibility UI delivery

- **Purpose:** Broaden design-to-code beyond single desktop visual fidelity.
- **Delivery activity measured:** Responsive layout, accessibility semantics, visual fidelity, functional tests.
- **Candidate scorers:** `design-to-code@1`, `requirements@1`, `resource-efficiency@1`; possible scorer refinement for accessibility if current deterministic evidence is too weak.
- **Required evidence:** Multiple viewport references or authored regions, semantic accessibility tests, visual captures, gate history.
- **Why current coverage is insufficient:** Homepage scenarios use one reference page and do not first-class responsive/a11y outcomes.
- **Work type:** Scenario revision if still homepage; new root if different app/product surface.

### 12. Scorer refinement proposal: workflow/process score visibility

- **Purpose:** Make delivery-process behaviors easier to reason about without inventing a new scorer family prematurely.
- **Delivery activity measured:** Completion integrity, command discipline, verification recovery, atomic commits, missing required commands.
- **Candidate scorers:** Existing quality scorers through `verification-stability` and execution-validity/performance-gate evidence; possible refinement to metric reporting, not new scorer family by default.
- **Required evidence:** Command records, gate history, process metrics, git commit records, completion claims, scenario workflow config.
- **Why current coverage is insufficient:** Platform captures process signals, but scenario-level reports do not make all process behaviors first-class objectives.
- **Work type:** Scorer/reporting refinement or platform reporting work, depending on desired output.

## Decision Rules

### Deterministic vs LLM-as-judge vs Hybrid

Use deterministic scoring when:

- the required behavior can be verified by tests, commands, coverage, file existence, exports/imports, structured requirement checks, visual diffs, command history, or explicit retained artifacts;
- failure modes can be represented as exact missing paths, failed gates, missing evidence, or numeric thresholds;
- the metric must be stable across reruns without model interpretation.

Use LLM-as-judge when:

- the claim is semantic and cannot be proven by deterministic checks alone;
- the judge receives a bounded evidence bundle with source artifacts, requirements, diffs, tests, and traces;
- the scorer owns the judge role file and output schema;
- missing evidence caps the score instead of inviting speculation.

Use hybrid scoring when:

- deterministic checks can prove prerequisites while semantic judgment resolves residual intent, such as plan adherence or requirements adherence;
- functional failure, missing prompt, missing implementation evidence, or absent retained artifacts should cap judge-backed scores;
- a deterministic metric can identify coverage while a judge metric assesses quality or adherence.

### New Scenario Root vs Revision

Create a new scenario root when:

- the delivery activity changes materially, such as bugfix versus greenfield, refactor versus implementation, or planning/orchestration versus direct coding;
- the starter architecture, domain, required evidence, or primary scorer family changes enough that old and new runs should not be considered direct revisions;
- the benchmark should become an independent reusable capability target.

Create a new revision inside an existing scenario root when:

- the task identity is the same and the change is prompt, rule, gate, requirement, visual-reference, or scorer-weight refinement;
- the purpose is to compare revisions of the same benchmark contract;
- old revisions should remain stable comparison anchors.

Use a matrix across revisions when the question is whether a changed prompt/ruleset/scorer profile improved the same task under comparable AgentSpecs.

### Scenario Work vs Scorer Work vs Platform Work

Frame the gap as scenario work when:

- active scorers already capture the desired evidence;
- current platform artifacts retain the evidence;
- the missing piece is an authored task contract, starter, prompt, rules, requirements, or matrix.

Frame the gap as scorer work when:

- evidence exists but no active metric interprets it correctly;
- current metrics overweight/underweight a delivery behavior;
- a judge rubric or deterministic metric needs refinement while scenario evidence is already available.

Frame the gap as platform work when:

- required evidence is not reliably captured, retained, normalized, or exposed in run/summary/dashboard artifacts;
- the task requires new artifact schemas, trace extraction, browser capture, plan packet ingestion, or orchestration logs;
- scoring would currently rely on unstructured traces or unstated assumptions.

Frame the gap as mixed work when both an authored scenario and a new evidence surface are required. Planning/orchestration should be treated as mixed only after confirming static retained artifacts cannot satisfy the benchmark.

## Platform-Gap Analysis for Planning and Orchestration Benchmarks

The active `plan-to-code@1` scorer and `plan-judge.toml` establish a direction: planning can be judged from retained artifacts and implementation evidence. The missing piece is not necessarily interactive orchestration machinery. The simplest sufficient next step is an artifact contract.

Proposed minimum static representation:

- `plan.md` or `plan.json`: approved intent, constraints, sequencing, verification strategy, risks, and acceptance mapping.
- `delegation.md` or `handoffs.json`: optional worker assignments, handoff prompts, dependencies, and expected outputs.
- `worker-results/`: retained summaries or artifacts from delegated work when used.
- `integration.md`: final decision log connecting plan items, changed files, tests, and known residual risks.
- Existing run evidence: changed files, command records, gates, scorecard, requirements coverage, resource metrics.

This enables plan-adherence and orchestration-quality evaluation without requiring the runner to observe live multi-agent communication. Interactive orchestration should be proposed only if static artifacts fail to capture timing, dependency ordering, coordination failures, or worker-result integrity at sufficient fidelity.

## Residual Risks

- The review did not inspect scenario starter contents, so starter-specific implementation constraints are inferred from scenario contracts and retained artifacts rather than source files.
- No live benchmark, proof run, or scenario validation run was performed as part of the review, consistent with the goal's non-goals.
- Existing experiment artifacts prove historical evidence shape, not current runtime health.
- Dashboard-only synthetic benchmark scenarios may have useful trend semantics, but their provenance and intended lifecycle need a separate audit before converting them into authorable scenario roots.
- Reference docs contain some language that appears stale relative to current code-backed scorer registry and schema fields. Future authoring docs may need alignment, but this review does not restructure broad documentation.
- CodeGraph MCP was used for indexed discovery first, but its returned source payloads were compressed in this session; direct targeted file reads were used as fallback for evidence.
