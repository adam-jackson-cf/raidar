# Orchestrator Performance Improvements Plan

## Objective

Reduce orchestrator and harness startup latency for local runs while preserving eval validity.

## Hard Constraints

1. Do not invalidate evals.
2. Keep canonical scoring behavior unchanged.
3. Preserve module-driven metric semantics (`metrics.modules[]`, `metric_profile`, `scores.modules[]`) across optimization paths.
4. Do not increase local parallelism or local fan-out.
5. Keep per-run workspace and artifact isolation semantics.

## Git-History Review (Latest)

Reviewed latest commits on `main` as of March 1, 2026:

1. `26779ac` (2026-02-24): pre-commit quality preflight.
2. `8602650` (2026-02-24): adapter-driven provider probe preflight.
3. `35c2762` (2026-02-24): additional Claude/Gemini model support.

### Impact on Performance Plan

1. `8602650` materially changes startup behavior and should be reflected as completed baseline work.
2. `26779ac` is developer workflow only; no runtime orchestrator startup impact.
3. `35c2762` changes supported models but not startup architecture materially.

## What Is Already Implemented (From History)

1. Adapter-level provider probe hook is implemented (`provider_probe()` in harness adapters).
2. Provider preflight execution and failure classification are implemented.
3. Provider preflight caching exists in `.preflight-cache` and is lock-guarded for the cache key.
4. Provider preflight is integrated into `_prepare_workspace_phase`.

Status note: this reduces wasted expensive runs when provider auth/billing/network is broken, but it also introduces startup work that should be measured and cache-optimized across invocations.

## Updated Plan Status

### Completed

1. Provider probe preflight integration and per-execution cache (commit `8602650`).

### Still Open

1. Detailed phase timing for non-Harbor orchestration overhead.
2. Cleanup cadence optimization (avoid running expensive stale cleanup on every run prep by default).
3. Shared content-addressed preflight cache across execution directories.
4. Scaffold preflight lock symmetry (provider preflight has lock; scaffold preflight should match).
5. Immutable cache strategy for Harbor bundle static segments.
6. `runner.py` modularization to reduce fan-out and blast radius.
7. Retry policy improvement (replace fixed sleep with bounded exponential backoff + jitter and telemetry).

## Updated Implementation Plan

## P0: Instrumentation and Low-Risk Friction Removal

1. Add non-Harbor phase timings in `orchestrator/src/raidar/runner.py`:
   - `initialize_run`
   - `prepare_run_context`
   - `provider_preflight`
   - `scaffold_preflight`
   - Harbor bundle build
   - evidence capture/hydration/diff
2. Keep provider preflight enabled, but explicitly record probe duration and cache hit/miss in run metadata.
3. Reduce unconditional per-run stale cleanup; keep explicit `raidar harbor cleanup` plus retry/failure cleanup paths.
4. Add lock wait-time metrics for both baseline and preflight lock paths.

Acceptance:

1. Canonical parity unchanged.
2. Lower median and p95 `harness_overhead_sec`.
3. No increase in void rate due to orchestration changes.

## P1: Safe Reuse Without Eval Contamination

1. Promote preflight caches (provider + scaffold) from execution-local to shared content-addressed cache keys.
2. Include immutable invalidation inputs:
   - task yaml hash
   - scaffold fingerprint
   - lockfile hash
   - required command list
   - harness/model
3. Add scaffold preflight lock symmetry for duplicate-call prevention.
4. Cache Harbor bundle static segments (`environment`, `tests`) by fingerprint while preserving per-run mutable workspace materialization.

Acceptance:

1. Canonical parity unchanged.
2. Repeated CLI invocations avoid repeated cold preflight/setup work.

## P2: Architecture Refactor for Sustained Performance Work

1. Split `runner.py` into bounded components while keeping `run_task(...)` facade contract.
2. Isolate modules for:
   - workspace lifecycle
   - Harbor execution/retry policy
   - artifact persistence
   - score synthesis
3. Replace fixed retry sleep with bounded exponential backoff + jitter and telemetry.

Acceptance:

1. Canonical behavior unchanged.
2. Reduced coupling hotspots in follow-up architecture scan.
3. Lower risk and smaller blast radius for future optimizations.

## P3: Governance and Protection

1. Add `CODEOWNERS` for orchestrator subdomains.
2. Add parity tests that compare optimized path outputs against canonical baselines.
3. Add regression tests for cache key invalidation behavior.
4. Add parity assertions for module-era fields:
   - run/suite `metric_profile`
   - suite `metric_modules`
   - run `scores.modules[]` payload integrity

Acceptance:

1. Ownership and review boundaries are explicit.
2. Performance optimizations are protected by deterministic parity checks.

## Explicitly Deferred

1. Increasing local parallelism (resource overhead constraint).
2. Python-to-Go rewrite unless post-P2 metrics show Python runtime overhead remains dominant.

## Eval Validity Guardrails

1. Canonical mode remains default for official evals.
2. Any non-canonical fast mode must be explicit and never used for official score reporting.
3. Reuse only immutable, fingerprinted assets.
4. Never reuse mutable run workspace state across runs.

## Suggested Next Implementation Slice

1. Implement P0 timing instrumentation first.
2. Add provider/scaffold cache hit/miss telemetry.
3. Re-baseline with existing eval suite and compare:
   - startup-to-agent-execution latency
   - `harness_overhead_sec`
   - parity outputs
