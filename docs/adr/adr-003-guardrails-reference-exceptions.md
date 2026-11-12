# ADR-003: Exceptions to the pinned guardrail reference's new test rules

- **Status:** Proposed (recorded by the surface-setup agent, 2026-11-11, under omp-amends.md §1.7 step 4 PR 1; each entry waits for owner review in the R1 pull request)
- **Date:** 2026-11-11
- **Authority:** `omp-amends.md` §1.7 (raidar transition) and §1.11 group B (the new catalogue rules). The exceptions live in `.omp/test-profile.md` front matter, `house_style.exceptions`; each entry names this ADR and the section that justifies it.
- **Scope:** findings of `omp-guardrails adapter lint` (TEST.no-test-hooks, TEST.no-evasion, TEST.no-self-mocking) and `omp-guardrails naming` (NAME.test-files) on the tree that R1 lands. The R1 gate runs these rule IDs blocking.

---

## 1. Context

- R1 pins `omp-guardrails` and runs 17 new catalogue rule IDs blocking in `make gate-commit` and `make gate-ci`. Those rules had no implementation in raidar before R1.
- Before the fixes below, `omp-guardrails adapter lint` reported 658 findings on the R1 tree: 595 TEST.no-self-mocking in 103 test files, 55 TEST.no-test-hooks in 40 production files and 8 TEST.no-evasion in 8 test files. `omp-guardrails naming` reported 2 NAME.test-files findings.
- R1 fixes what it can fix without changing product behaviour:
  - `orchestrator/tests/test_package_at3_docker.py` is renamed to `orchestrator/tests/test_package_failure_attribution_docker.py` (NAME.test-files numbered token);
  - the profile's tier selectors become pytest arguments, so `omp-guardrails collection-check` (TEST.tier-selectors) selects every test file in exactly one tier, including the Playwright specs and journeys;
  - six test files patched a standard-library module through a raidar module's namespace (for example `measurement.subprocess.run`). They now patch the standard-library module directly; the patched object is the same, so the tests are unchanged in effect (TEST.no-self-mocking);
  - four production files defined symbols only tests used (TEST.no-test-hooks). `markdown.py` now prints the interval level through `stats.confidence_label` instead of a copy of its format; the test-only wrappers `checks_pass`, `superseded_request_ids` and `build_quality_projection` are deleted, and their tests read the production paths they wrapped.
- The remaining findings fall into the groups below. A waiver is an exact `(rule, path)` pair: a new file with the same violation still fails.

## 2. Decision

### 2.1 Test-runner vocabulary in raidar's product code (TEST.no-test-hooks)

- Raidar is a coding-agent benchmark. Its product code builds, runs and scores the subject's test suites, so strings such as `"pytest"`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD`, `WORK_TESTS*` and `TEST_COVERAGE` name product inputs and metric names, not test-only branches.
- `orchestrator/src/raidar/cli/quality.py` is raidar's own test-runner command (`raidar quality test-fast`).
- Waived: the 11 files where every TEST.no-test-hooks finding is a test-marker string (`grading/bundle.py`, `grading/payload.py`, `grading/sandbox.py`, `subject/primitive/gate.py`, `subject/primitive/gate_tool.py`, `subject/workflow/judgeloop.py`, `subject/workflow/package_checks.py`, `scoring/process_checks/work_session.py`, `scoring/process_checks/work_tests.py`, `scoring/test_coverage/__init__.py`, `cli/quality.py`).

### 2.2 Verify-stage fixture states (TEST.no-test-hooks)

- `lab/stages/raidar/verify/fixtures/*/state/test_calc.py` are fixture workspaces the verify stage grades. The adapter treats them as production because the path holds the owned package name `raidar`.
- Waived: `lab/stages/raidar/verify/fixtures/*`. The reference should not treat fixture data as production; this is reported upstream.

### 2.3 Symbols loaded by name (TEST.no-test-hooks)

- `RaidarVerifier` (`raidar.grading.verifier:RaidarVerifier`, `design/problem/rewrite.py`) and `RunScopedDockerEnvironment` (`RUN_SCOPED_DOCKER_IMPORT_PATH`, `engine/trial/environment.py`) are loaded by Harbor from `module:Symbol` strings.
- `resolver_factory` and `scorer_resolver` are the `raidar.contract.resolvers` entry points in `orchestrator/pyproject.toml`.
- `stage_lock` is the `raidar stage lock` command, registered by its `@stage.command("lock")` decorator.
- The adapter's symbol-usage scan does not resolve import-path strings, entry points or decorator registration, so it reports these symbols as used only by tests.
- Waived: `orchestrator/src/raidar/grading/verifier.py`, `orchestrator/src/raidar/engine/trial/environment.py`, `orchestrator/src/raidar/design/problem/resolver.py`, `orchestrator/src/raidar/scoring/catalog/builtin.py` and `orchestrator/src/raidar/cli/stage.py`. This is reported upstream.

### 2.4 Environment-capability skips (TEST.no-evasion)

- Eight tests skip when the host lacks a capability the test needs: a non-root user, procfs, bash 4, `uv` on `PATH`, a ready Docker target, the archived pre-S6 gate source, an opt-in perf run, or a missing `orchestrator/.env`. The Docker skips turn into failures under `RAIDAR_REQUIRE_DOCKER=1`. None skips to hide a failure.
- Waived: the 8 files the lint names (`process_guard.py`, `test_make_targets.py`, `test_overhead_fixture_run.py`, `test_primitive_calls.py`, `test_primitive_payload_supervisor_slow.py`, `test_step_reset_script.py`, `test_verifier_bundle_and_isolation_slow.py`, `test_workflow_verify_docker.py`).
- Owner question: whether these should become doctor or readiness failures (test-guidance v2) instead of skips.

### 2.5 Legacy debt recorded at R1 (TEST.no-self-mocking, TEST.no-test-hooks)

- 97 existing test files patch or mock raidar's own modules (587 findings), and 12 production files define symbols only tests use.
- Fixing them rewrites most of the test suite and changes production modules, which is outside the shadow PR's behaviour-preserving scope. Examples:
  - most patches replace a collaborator that production code reaches by module global (for example `dependencies_module.load_health`), so a fix injects that collaborator through the public entry point;
  - `subject/agent/harness.py` is in the `primitive_code` import inventory: removing its two test-only functions changes all 18 `primitive_code` digests, so every experiment must be relocked;
  - `scoring/judge/gating.py` (`verdict_observations`, `uncalibrated_observations`, `jev_policy_modes`) is the plan-H2 gate that keeps shadow Jev observations out of verdicts, and no production path calls it yet. Wiring it in changes analysis output, so it needs its own product PR.
- Waived: each named file, by exact path. New test files and new production files are checked in full.
- The waivers are a burn-down list, not a standing policy. Each removal of an entry is a normal PR that passes the gate.

### 2.6 Old checker module with a test-like name (NAME.test-files): withdrawn

- The waived path was the evidence module of raidar's old budget checker under `scripts/checks/`, not a test; its `test_` prefix tripped NAME.test-files.
- By owner decision (2026-11-11), R1 brings the budget cutover forward from R2 and deletes the module, so no waiver remains.

## 3. Consequences

- The 17 new rule IDs block every new violation from R1 onwards.
- Every waiver is listed in the R1 pull request body for owner review. Removing a waiver needs no ADR change.
- Groups 2.2 and 2.3 are reference defects. When a pinned reference fixes them, the matching waivers are deleted in the bump PR.
