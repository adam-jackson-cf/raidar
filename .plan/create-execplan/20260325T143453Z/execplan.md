# ExecPlan: Orchestrator Reuse And Code Quality Refactors

- Status: Approved
- Start: 2026-03-25 • Last Updated: 2026-03-25T15:50:49Z
- Artifact root: `/Users/adamjackson/Projects/raidar/.plan/create-execplan/20260325T143453Z/`
- Workspace root: `/Users/adamjackson/Projects/raidar/.plan/create-execplan/20260325T143453Z/workspace/`
- Context Pack: `/Users/adamjackson/Projects/raidar/.plan/create-execplan/20260325T143453Z/context-pack.md`
- Runtime Input artifact: `/Users/adamjackson/Projects/raidar/.plan/create-execplan/20260325T143453Z/workspace/execplan-runtime-input.json` (generated after finalization; do not edit)
- Requirements Freeze artifact: `/Users/adamjackson/Projects/raidar/.plan/create-execplan/20260325T143453Z/workspace/requirements-freeze.md`
- Draft Review artifact: `/Users/adamjackson/Projects/raidar/.plan/create-execplan/20260325T143453Z/workspace/draft-review.md`
- Links:
  - `/Users/adamjackson/Projects/raidar/.enaible/analyze-code-quality/20260325T100903Z-code-quality-review/final-analysis.md`
  - `/Users/adamjackson/Projects/raidar/.plan/create-execplan/20260325T143453Z/context-pack.md`

## Requirements Freeze

- R1: Produce a brownfield implementation plan for orchestrator spin-up reuse and observability that resolves the remaining live Docker image warm-path issue and extends cache visibility into persisted and aggregate reporting.
- R2: Produce a brownfield refactor plan for the highest-value code-quality hotspots: typed request/option objects for CLI-heavy entrypoints, decomposition of `_promotion_guard`, consolidation of shared provider-adapter behavior, and targeted tests for low-coverage operational modules.
- R3: Exclude scenario files from implementation scope entirely, including starter folders; the plan must not schedule code or structural changes under `scenarios/**`.
- R4: Add and enforce repo guidance that `scenarios/**/starter/**` is excluded from analysis and code-quality checks because those files are representative delivery-scenario artifacts rather than canonical shared product code.
- Confirmed by user at: 2026-03-25T15:02:23Z

## Purpose / Big Picture

This plan packages the next safe implementation wave for two intertwined concerns: the orchestrator still has a real warm-path Docker image reuse gap in live smoke runs, and the repo still carries concentrated maintainability hotspots in a small set of CLI, orchestration, adapter, and low-coverage operational seams. The goal is to fix those high-leverage areas without broadening into scenario refactors or adding ongoing overhead for scenario authors.

The smallest safe path is to keep scope inside `orchestrator`, `auto_researcher`, repo analysis/code-quality policy, and related tests. Scenario files remain untouched. Any analysis/code-quality alignment for starter folders is handled via repo policy/config only.

Checkpoint split:

1. Checkpoint A: orchestrator warm-path reuse, cache/reporting visibility, and starter-folder analysis exclusion enforcement.
2. Checkpoint B: code-quality hotspot refactors and low-coverage operational test uplift after Checkpoint A is green.

Checkpoint A implementation choice:

- The fast-image reuse fix is locked to a generic persisted reusable image-artifact path rather than continued dependence on Docker daemon-local image retention.
- This keeps the solution scenario-agnostic while prioritizing determinism over the smallest possible code change.

## Success Criteria (how to prove "done")

- [ ] Smoke: `make orchestrator-smoke` twice in sequence -> second identical invocation reuses the persisted fast-image artifact path and does not pay a fresh fast-image build
- [ ] Reporting: aggregate and persisted metadata make baseline/image cache state and invalidation reason visible enough to debug warm-path regressions without raw Docker logs
- [ ] Refactor: CLI/request seams, `_promotion_guard`, and shared adapter behavior are reduced into smaller typed helpers without changing public command behavior
- [ ] Verification: targeted low-coverage operational seams gain deterministic coverage and `make quality` passes
- Non-Goals:
  - any code or structure changes under `scenarios/**`
  - scenario starter deduplication or template migration
  - backward-compatibility layers for legacy internal designs

## Constraints & Guardrails

- Use repo-root `make ...` as the public execution surface.
- Keep `make quality` as the completion gate.
- Do not change files under `scenarios/**`.
- Treat `scenarios/**/starter/**` as excluded from analysis/code-quality scope and align tooling accordingly.
- Do not add backward-compatibility branches or legacy fallback modes.
- Keep verification brownfield and change-scoped on touched modules.

## Dependency Preconditions

| Dependency | Purpose | Check command | Install command | Source | Hard-fail behavior |
| ---------- | ------- | ------------- | --------------- | ------ | ------------------ |
| Repo environment | Run repo gates and targeted tests | `uv --version` | `make env-setup` | repo `Makefile` | stop and escalate if repo env cannot be bootstrapped |
| Docker daemon | Run Harbor-backed smoke verification | `docker info >/dev/null 2>&1` | `n/a (user-managed daemon startup)` | local Docker install | stop and escalate if daemon is unavailable |

## Task Table (single source of truth)

Status keys:

- `@` = in progress
- `X` = complete
- (blank) = outstanding

Task Types:

- Code, Read, Action, Test, Gate, Human

Use `n/a` when `File Anchors` or `Command` does not apply. Every row must:

- map to one or more requirement IDs
- include at least one concrete file anchor or command
- state the expected output or completion signal
- avoid implicit discovery

| Status | Phase # | Task # | Type | Req IDs | File Anchors | Command | Expected Output | Action |
| ------ | ------- | ------ | ---- | ------- | ------------ | ------- | --------------- | ------ |
|        | 1       | 1      | Read | R1,R3,R4 | `/Users/adamjackson/Projects/raidar/.plan/create-execplan/20260325T143453Z/context-pack.md:1` | `n/a` | current context confirmed | Read the Context Pack and freeze scope before implementation starts. |
|        | 1       | 2      | Code | R3,R4 | `/Users/adamjackson/Projects/raidar/AGENTS.md:11` | `n/a` | policy and tooling path aligned | Enforce the starter-folder analysis exclusion in the relevant analyzer/quality configuration without touching scenario files. |
|        | 1       | 3      | Gate | R3 | `n/a` | `git diff --name-only` | no `scenarios/` paths in the implementation diff | Fail the implementation if any planned change spills into `scenarios/**`. |
|        | 1       | 4      | Gate | R1,R3,R4 | `/Users/adamjackson/Projects/raidar/.plan/create-execplan/20260325T143453Z/execplan.md:1` | `n/a` | Checkpoint A scope locked | Do not start hotspot refactors until the orchestrator warm-path and exclusion-enforcement track is complete and verified. |
|        | 2       | 5      | Code | R1 | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/runner.py:1718`, `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/runner.py:1961`, `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/runner.py:4134` | `n/a` | persisted fast-image artifact path implemented with cache-state traceability | Replace daemon-local fast-image dependence with a generic persisted reusable artifact flow and preserve cache-state traceability. |
|        | 2       | 6      | Code | R1 | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/experiment.py:155`, `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/storage.py:177` | `n/a` | aggregate reporting exposes cache-state and invalidation data | Surface orchestrator cache metadata beyond raw run artifacts. |
|        | 2       | 7      | Test | R1,R4 | `n/a` | `make orchestrator-smoke` | repeated warm-path smoke succeeds with durable image/cache behavior | Run the mandatory repeated orchestrator smoke verification for Checkpoint A. |
|        | 2       | 8      | Test | R1,R4 | `n/a` | `make quality` | Checkpoint A changes remain green under the full repo gate | Run the full quality gate at the end of Checkpoint A before allowing hotspot refactors. |
|        | 3       | 9      | Code | R2 | `/Users/adamjackson/Projects/raidar/auto_researcher/src/auto_researcher/cli.py:124`, `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/cli.py:984`, `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/experiment.py:155` | `n/a` | request/result assembly broken into typed helpers | Refactor the broad CLI and summary seams into smaller typed request/result objects. |
|        | 3       | 10     | Code | R2 | `/Users/adamjackson/Projects/raidar/auto_researcher/src/auto_researcher/engine.py:669` | `n/a` | guard logic split into independently testable evaluators | Decompose `_promotion_guard` without changing promotion semantics. |
|        | 3       | 11     | Code | R2 | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/agents/adapters/claude_code_cli.py:15`, `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/agents/adapters/gemini_cli.py:15`, `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/agents/adapters/codex_cli.py:15` | `n/a` | shared adapter behavior centralized with provider-specific rules preserved | Consolidate provider-adapter duplication through reusable base helpers/contracts. |
|        | 4       | 12     | Test | R2 | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/agents/harbor_agents/fast_cli_agents.py:1`, `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/scoring/acceptance.py:239`, `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/scoring/verification_stability.py:22`, `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/watcher/gate_watcher.py:56` | `uv run --project orchestrator python -m pytest orchestrator/tests -q` | targeted low-coverage seams have deterministic tests | Add or extend tests around the operational modules named in the quality report. |
|        | 4       | 13     | Test | R2 | `n/a` | `make quality` | repo gate passes with both checkpoints complete | Run the full quality gate at the end of Checkpoint B. |

## Progress Log (running)

- (2026-03-25T15:02Z) Step 1 requirements freeze confirmed by the user with `proceed`.
- (2026-03-25T15:02Z) Built the initial brownfield Context Pack from current repo anchors and report artifacts.
- (2026-03-25T15:02Z) Drafted the initial ExecPlan and paused for user review before finalization.
- (2026-03-25T15:02Z) Split the draft into Checkpoint A (orchestrator warm-path/reporting/exclusion enforcement) and Checkpoint B (quality hotspot refactors plus coverage uplift).
- (2026-03-25T15:50Z) User approved the Step 3 draft after locking Checkpoint A to the persisted fast-image artifact direction.

## Decision Log

- Decision: Exclude `scenarios/**` from implementation scope.
  - Rationale: User made this a hard requirement because scenarios are transient and should not become maintenance anchors.
  - Date: 2026-03-25
- Decision: Treat starter-folder analysis/code-quality exclusion as policy/config alignment work rather than scenario refactor work.
  - Rationale: This satisfies the user’s requirement without violating scenario immutability.
  - Date: 2026-03-25
- Decision: Keep the first implementation wave centered on orchestrator image reuse, reporting, hotspot seams, and test coverage.
  - Rationale: These are the highest-leverage issues named by the current evidence and can be changed without scenario edits.
  - Date: 2026-03-25
- Decision: Sequence the work as two checkpoints instead of one continuous batch.
  - Rationale: This lowers risk by forcing the orchestrator warm-path and policy/enforcement work to go green before broader maintainability refactors begin.
  - Date: 2026-03-25
- Decision: Lock Checkpoint A to a persisted reusable fast-image artifact path.
  - Rationale: Live smoke evidence showed daemon-local image retention was not reliable enough across separate invocations, and the user prioritized determinism over the smallest change.
  - Date: 2026-03-25

## Execution Findings

- Finding: Earlier code-quality recommendation to deduplicate scenario starters is no longer admissible.
- Evidence: `/Users/adamjackson/Projects/raidar/.plan/create-execplan/20260325T143453Z/workspace/requirements-freeze.md`
- Decision link: Exclude `scenarios/**` from implementation scope.
- User approval (required if this introduces new discovery scope): User explicitly tightened scope before Step 2.
- Finding: The remaining orchestrator performance gap is the live fast-image warm path, not baseline/preflight reuse.
- Evidence: `/Users/adamjackson/Projects/raidar/.plan/create-execplan/20260325T143453Z/workspace/context-discovery.md`
- Decision link: Keep the first implementation wave centered on orchestrator image reuse, reporting, hotspot seams, and test coverage.
- User approval (required if this introduces new discovery scope): Already within the confirmed Step 1 scope.
- Finding: The draft no longer leaves fast-image reuse on a daemon-local branch.
- Evidence: `/Users/adamjackson/Projects/raidar/.plan/create-execplan/20260325T143453Z/workspace/draft-review.md`
- Decision link: Lock Checkpoint A to a persisted reusable fast-image artifact path.
- User approval (required if this introduces new discovery scope): User selected the deterministic path during draft review.

## Test Plan

Use scenario-focused BDD coverage for changed behavior and high-risk regressions.

- Keep `Given`, `When`, and `Then` to one concise line each.
- Keep evidence commands executable.
- Use `Task Ref` format `P<phase>-T<task>` and ensure each row maps to one or more executable task rows.

| Scenario ID | Priority | Given | When | Then | Evidence Command | Task Ref |
| ----------- | -------- | ----- | ---- | ---- | ---------------- | -------- |
| S1 | P0 | Given an unchanged orchestrator smoke setup and a warm persisted fast-image artifact state | When `make orchestrator-smoke` is run twice with identical inputs | Then the second invocation reloads or reuses the persisted fast-image artifact path without a fresh image build | `make orchestrator-smoke` | P2-T7 |
| S2 | P0 | Given Checkpoint A changes are complete | When `make quality` is run before hotspot refactors begin | Then orchestrator warm-path/reporting and exclusion enforcement are green in isolation | `make quality` | P2-T8 |
| S3 | P1 | Given the refactored CLI/request seams and adapter helpers | When `make quality` is run at the end of the full implementation | Then public command behavior and repo gates remain green | `make quality` | P4-T13 |
| S4 | P1 | Given the scenario-exclusion hard requirement | When the implementation diff is inspected | Then no path under `scenarios/**` is modified | `git diff --name-only` | P1-T3 |
| S5 | P1 | Given low-coverage operational modules named in the quality report | When targeted pytest coverage is executed | Then new deterministic tests protect the touched operational seams | `uv run --project orchestrator python -m pytest orchestrator/tests -q` | P4-T12 |

## Idempotence & Recovery

- Context Pack and ExecPlan edits are safe to revise in place before finalization.
- Targeted pytest commands, `make quality`, and smoke commands are safe to re-run.
- If the persisted fast-image artifact path proves unsafe, revert to the last green checkpoint and keep the added instrumentation/tests while redesigning artifact format or cache lifecycle rather than falling back to daemon-local dependence.
- If a refactor widens beyond intended seams, stop and reduce scope back to the line-anchored code map instead of carrying partial broad cleanup forward.
