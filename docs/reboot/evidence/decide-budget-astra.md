# Vote: test budgets — GPT-6 Astra

## Q1

**Decision:** Enforce timing limits in **CI only**, through `gate-ci`. Keep hook timing advisory; do not apply CI limits to developer machines. Record at least five successful, complete CI baseline runs of the same revision and measurement configuration.

Identify the environment using a versioned profile: GitHub runner class, OS image family, architecture, Python version, dependency-lock digest, coverage configuration, test selector and worker count. Record the actual image version and CPU details in every measurement.

Run the measured fast tier once, sequentially, with provisioned dependencies and no concurrent test processes in that job. Include collection and coverage; exclude dependency installation and unrelated quality checks. Never discard a slow measurement merely because it exceeded the budget, retry until green, or dynamically relax limits based on load. An incompatible environment or invalid measurement fails with an infrastructure diagnostic.

**Reason:** The existing tier policy explicitly names CI as the host timing reference. Local timings supplied in the brief vary substantially under contention. Per-machine enforcement would introduce unstable limits and ongoing calibration work. `gate-ci` currently calls `gate-commit`, so enforcement must distinguish the CI invocation without running the fast tier twice. Hosted-runner contention remains a residual risk, not something five runs can eliminate.

## Q2

**Decision:** Use explicit, reproducible provisional formulas:

- **Fast-tier wall limit:** round up `1.25 × maximum wall time` across the qualifying baseline runs to whole seconds.
- **Common per-test limit:** calculate each test’s maximum setup-plus-call-plus-teardown duration across those runs; take the p95 across these test maxima, multiply by `1.5`, and round up to 10 ms.
- **Existing outliers:** create named, finding-level baseline exceptions for tests above the common limit. Each exception has its own ceiling of `1.5 × that test’s observed maximum`, rounded up to 10 ms, plus an owner, concrete speed-up remedy and review deadline. Expired exceptions fail. No wildcard exceptions.
- **New tests:** use the common limit. A new exception, raised exception ceiling or deadline extension requires owner approval.

Keep 100 ms as an initial per-case optimization goal, not a measured CI claim. If the computed common limit is unreasonably high, report that as unresolved debt rather than silently declaring it acceptable.

**Reason:** Five observations cannot estimate a meaningful p95 over runtime variability; a maximum with declared headroom is more transparent. The per-test percentile above summarizes the *population of tests*, not confidence about runtime stability. Named exceptions prevent one expensive test from raising every test’s allowance. Exact observed-duration ceilings invite flakiness, while unrestricted grandfathering conceals debt.

## Q3

**Decision:** Store reviewed policy and evidence under a tracked `.omp/test-budgets/` directory:

- `limits.json`: environment profile, limits, formulas, provisional status, goals and named exceptions.
- `baselines/<measurement-id>.json`: at least five complete run records containing revision, CI run/job identity, environment, inventory, wall time and every case duration.
- `approvals/<approval-id>.json`: exact old/new policy digests, authorized changes, owner identity and externally verifiable approval evidence.

Keep ordinary generated reports in unique per-invocation directories under `var/`; promote only reviewed baseline evidence into tracked configuration.

Mechanically enforce:

1. Limits are recomputed from the referenced baseline records; malformed, incomplete or mismatched evidence fails.
2. Reports belong to the current invocation and tested revision, cover the independently collected fast inventory, and contain finite durations and complete execution phases. Skipped or unexecuted cases cannot count as zero-duration evidence.
3. Status remains **provisional** until an identified repository measure run produces a valid completion record bound to the policy and environment. A manually edited status flag is insufficient.
4. Compare policy against the trusted PR base or previous protected-branch revision. Any increase—including exception additions, deadline extensions or weaker measurement scope—requires owner approval bound to the exact change.
5. Verify approval using protected review metadata or an owner signature anchored outside the proposed change. Protect the checker and CI enforcement path too; a committed `ownerApproved: true` is not authorization.

**Reason:** The draft checker validates an approval Boolean but does not establish who approved an increase. Its fixed output paths also permit concurrent runs to interfere. Historical records need provenance; current reports need completeness and invocation identity.

## Q4

**Decision:** Keep **under 30 seconds** as a separately tracked goal, not today’s blocking limit. Display the measured wall time, enforced limit, goal and remaining gap together. Tighten the enforced limit deliberately as improvements land; never automatically raise it.

**Reason:** The tier policy calls 30 seconds prospective. The supplied local observations establish substantial debt but cannot establish a numeric CI limit. Setting 30 seconds as the immediate limit would intentionally block current work without providing a calibrated regression guard.

## Q5

**Decision:** Accept CI-only enforcement with explicit residual risks:

- Five runs provide weak variability evidence; margins are provisional engineering choices.
- Hosted-runner image changes and contention can still produce false failures.
- Shared fixtures distort per-case attribution; retain setup/call/teardown breakdowns alongside totals.
- Baseline exceptions can become permanent debt; enforce ownership, review deadlines and exact test identities.
- Test renames, selector changes, skipped cases and truncated `-x` runs can undermine comparisons; validate inventory and completion.
- Aggregate wall growth can come from additional legitimate tests, but any resulting limit increase still needs approval.
- Diagnostics must name affected tests and concrete speed-up remedies—inject clocks/runners or remove needless steps—never recommend changing tiers to escape timing limits.

**Reason:** These controls make failures interpretable without pretending wall-clock measurement is deterministic. CI timings remain unknown from the evidence reviewed; no numeric enforced limit is justified yet.