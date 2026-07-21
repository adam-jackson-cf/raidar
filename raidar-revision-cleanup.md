# Raidar Revision Cleanup Plan

## Decision

Repair measurement validity before changing public taxonomy. Raidar currently has several executable SDLC tasks whose attached scorers cannot establish the outcome they claim to measure. A cosmetic scenario or scorer rename would create new identities while leaving invalid evidence paths, impossible pass conditions, and untrustworthy rankings in place.

The target model is strict:

- A **scenario** is an agent-executed SDLC task with a defined initial workspace, instruction set, oracle, and completion contract.
- A **scorer** is a reusable quality lens with distinct metric ownership and evidence that can support its claim.
- **Execution validity** remains a system-level gate. It is not an attachable scorer.
- An operational smoke task can use scenario infrastructure but cannot enter benchmark ranking or benchmark storage.
- A profile with incomplete evidence is calibrating or quarantined, never silently ranked.

This plan preserves historical result identity. Existing scenario IDs and scorer versions remain the identity of their recorded measurements. Semantic changes create a new scenario revision and scorer version; they do not add runtime aliases or mix score scales.

## Scope and completion criteria

The cleanup covers the scenario schema, catalogue selection, experiment-kind enforcement, scorer evidence contracts, scenario revisions, scorer versions, matrices, validation, and calibration. It does not redesign agent harnesses or alter arbitrary existing benchmark results.

The cleanup is complete when:

1. Every catalogue entry has orthogonal task, delivery, workspace, language, evaluation-role, and lifecycle metadata.
2. Operational smoke tasks cannot run or persist as benchmark experiments.
3. Scenario validation rejects overlapping scorer metric ownership before a run starts.
4. Python and TypeScript requirements checks inspect the declared source and test surfaces correctly.
5. Defect repair, refactoring, test authoring, and plan-driven delivery have immutable baseline or temporal evidence suited to their claims.
6. Every activated profile has an evidence-backed pass path and a negative fixture that proves a material failure is caught.
7. New and historical measurement epochs remain separate in reporting and matrices.

## Target taxonomy

Replace the overloaded free-form category with validated, orthogonal fields. `category` may remain only as a temporary display grouping until all catalogue consumers use the fields below.

```yaml
task_kind: feature_implementation | design_implementation | defect_repair |
  refactoring | test_authoring

delivery_mode: direct_implementation | plan_driven | plan_and_implement
workspace_state: new | existing
starting_conditions: [] # known_defect, legacy_structure
languages: [python]
evaluation_role: benchmark | operational
catalog_status: active | calibration | quarantined | retired
family_id: optional-comparison-family
variant_axes: {}
```

`task_kind` describes the agent’s work. `delivery_mode` describes the process contract. `workspace_state` and `starting_conditions` describe what the agent receives; a workspace may be existing and simultaneously have a known defect and legacy structure. `evaluation_role` determines execution and storage policy. `catalog_status` determines whether a scenario can rank. `family_id` and `variant_axes` are optional and must be present only for intentionally matched comparison families.

Add a human-facing `display_name` rather than renaming stable machine IDs during this cleanup. The canonical display labels are:

| Machine ID | Display name | Intended status |
|---|---|---|
| `homepage-implementation` | Homepage reference implementation | Active after scorer calibration |
| `python-code-task-baseline` | Python utility implementation | Calibration until Python requirements are repaired |
| `skill-benchmark-coding-test` | TypeScript utility implementation | Active as a standalone task |
| `targeted-bugfix` | Python shipping defect repair | Quarantined |
| `bugfix-ledger-balance` | TypeScript ledger defect repair | Active only as a standalone task until matched-family work exists |
| `refactor-library` | Python reporting-library refactor | Quarantined |
| `plan-to-code-baseline` | Planned alert-escalation implementation | Quarantined |
| `test-generation-baseline` | Pricing test authoring | Quarantined |
| `hello-world-smoke` | Harbor orchestration smoke | Operational |

Do not describe the Python and TypeScript utility tasks, or the two defect tasks, as language variants yet. Their prompts, difficulty, starter state, reproduction setup, and evidence requirements are materially different. A future comparison family requires matched task contracts first.

## Phase 1 — establish catalogue policy and identity boundaries

### 1.1 Extend the schema and lifecycle surface

Update `ScenarioDefinition` and its serializers, initialisation, cloning, list, inspection, catalogue, matrix, and report consumers to carry the target taxonomy. The schema must reject unknown values and invalid combinations. For example, `evaluation_role: operational` cannot use `catalog_status: active` for benchmark selection, and a `plan_driven` delivery mode requires a scenario-owned plan artifact.

Extend the experiment-kind model with an operational-smoke path, or enforce an equivalent role-specific execution policy. The current run surface defaults to benchmark execution, so validation must reject a benchmark request for an operational scenario before workspace setup or persistence.

### 1.2 Quarantine before repairing

After the schema exists, mark these profiles `quarantined`:

- `targeted-bugfix`: Python requirements checks and semantic evidence are language-incompatible; it lacks immutable defect replay.
- `refactor-library`: structural improvement and public contract stability have no starter baseline.
- `plan-to-code-baseline`: the plan is mutable agent output while the scorer claims approved-plan adherence.
- `test-generation-baseline`: coverage lift has neither a baseline nor a final coverage-producing command, and its production-change policy conflicts with the prompt.

Mark `hello-world-smoke` as `evaluation_role: operational`. Exclude it from benchmark matrices, benchmark experiment persistence, and leaderboard aggregation while retaining direct smoke invocation.

### 1.3 Tests

Add schema and command tests that prove:

- invalid taxonomy values and invalid field combinations fail validation;
- operational scenarios reject benchmark experiment requests;
- quarantined scenarios are omitted from benchmark catalogue and matrix selection but remain directly invocable for calibration;
- persisted historical runs retain their original scenario and scorer identity.

## Phase 2 — repair shared requirements evidence and scorer composition

### 2.1 Replace TypeScript-only deterministic checks

`import_present` is currently a substring check restricted to `src/**/*.ts` and `src/**/*.tsx`; `no_pattern` has the same restriction. Replace these names and semantics with explicit, language-aware checks. A requirement must declare its allowed workspace roots and language/file patterns, including top-level test directories where the contract concerns tests.

Use check types that state what they prove. A text-presence check is not an import check. Preserve safe-regex validation and fail closed for unknown paths, file types, and unsupported check modes.

Cover at least these cases:

- a Python production symbol in `src/` is found;
- a Python test assertion in `tests/` is found;
- a TypeScript production symbol is found;
- a forbidden marker in a declared source or test surface is found;
- a matching string in an excluded artifact directory does not satisfy a requirement;
- an absent expected symbol fails.

### 2.2 Repair judge evidence selection

Expand judge source selection to include Python source and test files when the scenario language requires them. Prefer scenario-derived declared surfaces and valid changed-file evidence over global file heuristics. A judge reviewing Python defect repair must receive the changed repair source, relevant tests, scenario requirements, and retained defect evidence.

### 2.3 Enforce unique metric ownership

Validate the raw metric IDs across all resolved scorers before deduplication. Reject a scenario when two scorers own the same output metric, including `functional`, `test-coverage`, or `verification-stability`. This prevents a late runtime duplicate-score error and forces composed scorers to expose clear, disjoint lenses.

### 2.4 Tests

Add false-positive and false-negative fixtures for each language/path requirement mode. Add an integration test proving that a Python defect repair can attain requirements coverage only when the repaired behavior and test evidence are present. Add validation tests proving that duplicate metric ownership fails before execution.

## Phase 3 — add immutable baseline and temporal evidence

Build one shared baseline-capture mechanism rather than separate scorer-local approximations. It must execute on the pristine scenario starter and on the final workspace with identical pinned commands, source selection, environment, and tool configuration. Persist the following with the run:

- starter fingerprint and content hashes for protected source surfaces;
- resolved command and tool-version identity;
- scenario-owned oracle inputs and results;
- declared public API snapshot where required;
- coverage report and selected source set where required;
- valid changed-file evidence.

A baseline is invalid if the source selection, coverage configuration, command, or protected source fingerprint differs between starter and final evaluation.

### 3.1 Defect repair

Each defect scenario requires a scenario-owned reproduction oracle. The oracle must fail against the pristine starter and pass against the submitted workspace. Regression protection must inspect the behavior exercised by the oracle and changed tests; filename words such as `regression` or `bug` cannot establish protection.

For `targeted-bugfix`, repair Python requirements evidence first, then add a shipping-quote reproduction that proves the zone-three expedited surcharge defect. For `bugfix-ledger-balance`, retain the existing debit reproduction but make its starter failure and final pass part of scorer-owned evidence.

### 3.2 Refactoring

Capture three starter/final comparisons:

1. **Behavior:** the same public behavior oracle passes on both workspaces.
2. **Public contract:** exported API snapshot and signature-level contract remain compatible.
3. **Structure:** declared structural measures improve or satisfy the task contract without relying on final-file inventory alone.

The refactoring scorer must reject missing baseline evidence rather than emit a passing or partially trusted proxy. It must score the declared structural objective, such as reducing a specified function’s responsibility, complexity, duplication, or unreviewable mixed concerns. File movement alone is not improvement.

### 3.3 Test authoring

For `test-generation-baseline`, add identical baseline/final coverage commands and reports. The baseline task states the production implementation is already correct, so select one coherent policy: production files are hash-protected and changes fail the test-authoring profile. Remove the prompt’s genuine-defect exception. A distinct defect-discovery task can allow production changes with separate evidence requirements.

Require generated tests to map to declared behavior, use meaningful assertions, and increase coverage over the locked production source set. Final coverage without a baseline is diagnostic evidence only; it cannot pass a coverage-lift metric.

### 3.4 Tests

Introduce intentional negative fixtures:

- a defect remains unfixed;
- a test has a behavior-related filename but does not protect the defect;
- a refactor changes a public contract;
- a refactor moves code without improving the declared structural target;
- test authoring modifies protected production code;
- coverage report generation is missing or differs from baseline configuration.

## Phase 4 — resolve the plan-driven contract

Use the immutable-plan route for the current alert-escalation task. Store a versioned, scenario-owned plan artifact outside the mutable agent workspace or copy it into the workspace with a verified hash. The agent may read it but cannot replace the authoritative plan.

Replace the current pre-implementation plan-quality judge with an implementation-adherence scorer. It must compare plan rows, declared acceptance criteria, changed surfaces, verification evidence, and justified deviations after implementation. It must not grade plan prose as a substitute for delivery adherence.

If future scenarios require agent-authored planning, treat them as `delivery_mode: plan_and_implement`. Capture a plan snapshot before the first workspace modification; score plan quality, plan execution, and final delivery separately. Do not call the plan approved unless it was supplied and frozen before the run.

Add tests proving that replacing the plan, completing unplanned work while omitting planned work, or marking acceptance rows passed without command evidence fails the profile.

## Phase 5 — create calibrated scorer and scenario revisions

Create new scorer versions for every changed evidence contract. New versions must have disjoint metric IDs when composed together. The intended active lens set is:

- **Requirements Adherence:** language-aware deterministic checks plus semantic review over declared surfaces.
- **Resource Efficiency:** a within-scenario diagnostic, not a normalized cross-task leaderboard metric.
- **Python Engineering Quality** and **TypeScript Engineering Quality:** language-specific implementation quality with their own non-overlapping metrics.
- **Visual Fidelity:** reference-image and region comparison only.
- **Defect-Repair Quality:** starter failure/final success, behavior-specific regression protection, containment, and retained evidence.
- **Refactoring Quality:** behavior preservation, public-contract stability, structural objective, and containment.
- **Test Authoring Quality:** requirement mapping, assertion strength, baseline/final coverage change, and protected production integrity.
- **Plan Adherence:** immutable-plan delivery comparison only.

For homepage work, create a new scenario revision with a visual-fidelity scorer and disjoint requirements/UI-quality scorers. Do not reuse the old score scale: the current design-to-code profile weights visual regression at only 0.34 of its internal score.

Update matrices atomically to select only active benchmark profiles. Preserve prior matrices and results as historical measurement records; do not aggregate them with the revised profiles.

## Phase 6 — shadow scoring and activation

Run revised profiles against frozen historical submissions and curated good/bad fixtures before activating rankings. Assess:

- whether each intended failure is caught by the lens responsible for it;
- whether a correct submission can satisfy every metric through real evidence;
- scorer weight and threshold sensitivity;
- LLM judge repeatability;
- baseline integrity and resistance to source/configuration manipulation;
- whether resource efficiency remains comparable only within the same scenario contract.

Record acceptance thresholds before activation. Activate only after repeated shadow runs show stable expected ordering and no missing-evidence proxy can receive a passing result.

## Delivery sequence and gates

| Gate | Prerequisite | Exit condition |
|---|---|---|
| Catalogue gate | Phase 1 schema and policy | Operational and quarantined profiles cannot enter benchmark ranking. |
| Evidence gate | Phase 2 shared requirement and composition repairs | Python and TypeScript evidence fixtures pass; duplicate metrics fail early. |
| Baseline gate | Phase 3 | Every task-conditioned scorer has immutable starter/final evidence and negative fixtures. |
| Contract gate | Phase 4 | Plan ownership, timing, and scorer semantics agree. |
| Measurement gate | Phase 5 | New revisions, scorer versions, and matrices are internally consistent. |
| Activation gate | Phase 6 | Shadow scoring supports thresholds and expected discriminants; only complete profiles become active. |

The governing risk is false confidence: a score must never imply defect resolution, refactoring improvement, test coverage lift, or plan adherence when the evidence cannot establish it. Quarantine and calibration are deliberate states that protect benchmark credibility until the contract is measurable.
