# Objective

Optimize the smoke workflow so `make agent-smoke HARNESS=codex-cli PROVIDER=openai MODEL=gpt-5.5` completes the warm orchestration overhead path in under 10 seconds excluding actual harness/test execution, with cold-start setup pushed as low as safely possible.

# Outcome

`make agent-smoke HARNESS=codex-cli PROVIDER=openai MODEL=gpt-5.5` uses `reasoning_effort=low` by default where applicable, preserves feature parity, produces a valid comparable smoke experiment, and completes the warm orchestration overhead path in less than 10 seconds when actual harness/test execution is reported separately without invalidating any experiment runs.

# Scope boundaries

Allowed work:

- Optimize any orchestration-layer code, Makefile wiring, CLI validation, smoke setup, Harbor preparation, cleanup, auth checks, image/container preparation, verifier execution, artifact persistence, or runtime boundaries that affect end-to-end smoke time.
- Add or complete support for `gpt-5.5` with `reasoning_effort=low` anywhere current validation, matrices, tests, or public defaults require it.
- Rewrite or substantially refactor orchestration internals, including changing runtime boundaries, if that is the cleanest way to meet the target while preserving behavior.
- Use conservative caching, precomputation, or moved-earlier deterministic setup when cache keys fully capture inputs and stale state cannot affect experiment correctness.
- Explore or implement non-Docker sandboxing and alternative experiment-isolation mechanisms if they preserve scenario isolation, artifact parity, scoring semantics, feature parity, and result comparability.
- Explore or implement ready-to-run environment state, including prebuilt verification/setup environments that only need scenario/workspace material copied in, when cache keys fully capture the prepared state and no prior experiment data can leak into agent-visible context or outputs.

Non-negotiable boundaries:

- Do not compromise experiment integrity.
- Do not remove feature parity from public smoke, experiment, or matrix workflows.
- Do not invalidate stored experiment runs, experiment comparability, scoring semantics, schemas, artifact layout, or result interpretation.
- Do not let one experiment inherit polluted scenario artifacts, context, workspace state, runtime output, or agent-visible data from a previous experiment.
- Do not bypass, suppress, weaken, or defer failures from Docker, auth, harness validation, scenario validation, quality gates, or other semantic checks merely to satisfy the time target.

Excluded work:

- Changing scenario task content or scoring criteria to make the smoke path faster.
- Treating artifact persistence or orchestration runtime outside the actual harness/test execution window as outside the 10 second target.
- Hiding or omitting actual harness/test execution time; it may be excluded from the under-10 second threshold, but it must still be measured and reported separately.
- Introducing temporary shortcuts, placeholders, mocks, or fallback modes outside test contexts.

# Approach context

The benchmark command is the public smoke workflow:

```sh
make agent-smoke HARNESS=codex-cli PROVIDER=openai MODEL=gpt-5.5
```

The timing window starts at command invocation and ends when `make agent-smoke` exits. Dependency installation, one-time environment bootstrap, user-driven authentication setup, and actual harness/test execution may be reported separately, but the remaining warm orchestration overhead must complete in under 10 seconds. Actual harness/test execution includes the task the harness is instructed to execute through the task's own completion, including quality gates, builds, linting, typechecking, or other commands the task runs before considering itself complete.

The current repo routes public workflows through the root `Makefile`; direct `uv run --project orchestrator raidar ...` is an implementation detail. The current smoke path includes Docker availability checks, Harbor cleanup, harness validation, and an `experiment-run` call. The executor should measure the path before changing it, identify the dominant end-to-end costs, then optimize the real bottlenecks without changing the observable experiment contract.

`gpt-5.5` with low reasoning is the canonical default for smoke tests and test expectations. If any current support surface does not accept `gpt-5.5` or `reasoning_effort=low`, update that support rather than working around validation.

# Decision boundaries

The executor may independently:

- Refactor orchestration internals and tests to meet the target.
- Add precise instrumentation for end-to-end smoke timing and phase timing.
- Replace slow deterministic setup with safe cache-aware or moved-earlier equivalents.
- Replace Docker/Harbor runtime boundaries with another isolation mechanism, or add a fast isolated execution path, if the observable experiment contract remains equivalent.
- Update public defaults, examples, validations, and tests to use `gpt-5.5` with low reasoning.
- Remove legacy-only code paths that conflict with the target design.

The executor must seek user agreement before:

- Changing stored experiment schemas or artifact layout.
- Changing scoring semantics, scenario contracts, or benchmark interpretation.
- Removing public workflow capabilities rather than preserving feature parity.
- Introducing cross-run reuse that could plausibly expose prior experiment artifacts, context, workspace state, or agent-visible data to a later experiment.

# Definition of Done

- `make agent-smoke HARNESS=codex-cli PROVIDER=openai MODEL=gpt-5.5` completes the warm orchestration overhead path in under 10 seconds when actual harness/test execution time is excluded and reported separately.
- Cold-start setup costs are measured and reduced where safe, especially dependency installation and base image preparation.
- The measured end-to-end boundary and phase breakdown are explicit, repeatable, and backed by evidence from instrumentation or command output.
- `gpt-5.5` with `reasoning_effort=low` is supported wherever the smoke path, validation, tests, or public defaults require it.
- Feature parity is retained for public smoke, experiment, and matrix workflows.
- Existing experiment runs remain valid and comparable; no stored-result schema, scoring, or interpretation change is required.
- Experiment isolation is preserved: scenario artifacts, agent-visible context, workspaces, and runtime outputs are unpolluted by previous experiments.
- Relevant tests cover the optimized orchestration path, model default, reasoning effort propagation, isolation guarantees, and any cache invalidation behavior.
- `make quality` passes.

# Produced goal statement

Optimize the repo-root `make agent-smoke` workflow so the warm orchestration overhead path completes in under 10 seconds excluding separately reported actual harness/test execution, while making `gpt-5.5` with low reasoning the canonical smoke/test default, retaining feature parity, preserving experiment isolation, and keeping existing experiment runs valid and comparable.

# Executor guidance

This `goal.md` is the source of truth for implementation. Maintain `goal-tracker.md` during execution so later readers can see the decisions and evidence behind the optimization.

Update `goal-tracker.md` only for high-value events: decisions, direction changes, commits, verification evidence, open questions discovered during implementation, convergence of risk, complexity, or user journeys, deferred work, and rejected paths. Keep routine progress out of the tracker.
