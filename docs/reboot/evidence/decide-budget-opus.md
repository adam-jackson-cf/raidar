# Vote: test budgets — Claude Opus 5.5

Evidence used: `Makefile:553-554,748-765`, `orchestrator/src/raidar/cli/quality.py:136-162`, `test-tiers.md:8-23,46-52`, `.github/workflows/ci-quality-gates-incremental.yml`, `.omp/gate.json`, `engine/perf/budgets.py:1-12`, `CODEOWNERS`, `docs/reboot/handoff.md:131`, and the draft checkers under `/tmp/raidar-guardrails/full/scripts/checks/`. I also read 13 recent successful CI `gate` logs from 2026-11-10. In those logs:
- pytest reported the fast tier at **57.8–99.8 s**, for 5,374–5,641 passed cases.
- The slowest case per run was **1.09–1.81 s**. It is almost always `test_experiment_lock_behaviors.py::test_relocking_unchanged_inputs_is_byte_identical`, followed by `test_code_quality_fork_parity…[forge]`.
- The slow tier took 329–389 s.

CI per-case times are about twice the local maximum of 0.905 s, and CI wall time spreads by about 1.7×.

## Q1 — Where baselines are recorded and limits enforced

**Decision:** Both gates take the measurement; only CI enforces the limits.
- **Enforcing environment:** one environment, keyed `github-hosted/ubuntu-latest/X64/py3.13.7`. The key is built from `GITHUB_ACTIONS=true`, `RUNNER_ENVIRONMENT=github-hosted`, `RUNNER_OS`, `RUNNER_ARCH`, `ImageOS` and the interpreter version. Each record also stores `ImageVersion` and the CPU model from `/proc/cpuinfo`.
- **Baselines:** at least 5 runs (I recommend 10) from the CI `gate` job on `main`. Each run uploads its normalised report as an artifact.
- **Measurement path:** the measurement is built into `run_fast_tier` as a pytest plugin, with outer wall-clock timing around the pytest subprocess. It must not run as a second `make test-fast`, because the draft's `run-budgeted-fast.py` would add about 90 s to the gate.
- **`gate-commit` (hook):**
  - Missing or invalid measurements fail: no report, a case missing from the collected set, or a non-finite duration.
  - Timing limits are reported against the CI limits as `advisory: uncontrolled host`, with load average and CPU count. They never fail the hook.
- **`gate-ci`:**
  - Everything the hook checks, plus the limits are enforced.
  - Any other environment key fails the timing check: it is an unknown environment.
- **Contention:**
  - Only one job runs per GitHub VM, and nothing runs in parallel with the fast tier inside `gate-ci`. That leaves only noise between VMs and between CPU types.
  - Limits are set from the baseline maximum with a margin (see Q2), never from the median.
  - There is no retry-on-breach, because it would hide real regressions.

**Reason:**
- `test-tiers.md:20-22` already names `ubuntu-latest`/3.13.7 as the timing reference for host tiers, and says local baselines "are not CI feasibility evidence".
- The developer machine ranges from 105 s to 260 s depending on how many agent gates run at once. Any local limit loose enough to avoid flakes (≥ 325 s) would check nothing. Per-machine limits also cannot be tracked.
- This copies the perf precedent: budgets per target, plus `perf-check` being report-only by default while the controlled `perf-gate` is strict.
- CI runs on every push, so a breach is still caught before merge.

## Q2 — How limits are computed

**Decision:** The formula is fixed in the checker. The checker recomputes every limit from the recorded baselines on each run. The tracked limit may equal the computed value or be lower (tighter), never higher.
- **Fast-tier wall:** `limit = ceil_5s(max(baseline walls) × 1.25)`. Wall time is measured outside pytest and covers collection, coverage and report writing (the tier definition says "incl. collection and coverage"). From today's CI data, expect roughly 120–130 s. p95 is meaningless with 5–10 samples, so use the maximum.
- **Per-test, global:** `L = ceil_100ms(1.25 × p99 of setup+call+teardown durations, pooled over all baseline runs)`, with an owner-set cap of 500 ms. One limit applies to every fast case.
- **Tests already over `L`:** a finding-level baseline, not "limit equals observed".
  - Each such test becomes a named exception keyed by node ID, with `limit = ceil_100ms(max over baselines × 1.25)`.
  - Exceptions only ratchet down. A re-measure sets `min(old, computed)`.
  - Adding an exception, raising one, or growing the exception count needs owner approval.
  - An exception whose test no longer exists fails, with the message "remove exception".
  - Exceptions with large headroom are listed so they can be tightened at the next measure run.
- **Diagnostics:** every breach names the node ID, its duration and limit, and a speed-up fix: inject the clock or runner, shrink fixtures and data, cut needless steps. A slow session-scoped fixture can bill its setup to the first test, so the diagnostic also names the phase (setup, call or teardown). It never suggests moving the test to another tier.

**Reason:**
- A limit equal to the observed value has no headroom and would flake on CI's 1.6× variation per case.
- A single limit at the observed maximum (1.81 s × 1.25 ≈ 2.3 s, which is what the draft's single `perTestMs` amounts to) would let every one of about 5,600 cases grow to 2.3 s unchecked.
- A global limit plus named, ratcheted exceptions keeps both properties: there is a real bound for new tests and a visible debt list for old ones. This matches the existing coverage ratchet (`floor = max(previous, measured)`, never relaxed).

## Q3 — Where records live and how the rules are enforced

**Decision:** One tracked file, `.omp/test-budgets.json`, next to `.omp/gate.json`, the guardrail config that `run-repository-gate.py` already reads. It has these sections:
- `environment`: the key.
- `formula`: margins, rounding, the 500 ms cap. Changing it counts as an increase.
- `baselines[]`: GitHub run ID and attempt, head SHA, env key, `ImageVersion`, CPU model, wall ms, collected and passed counts, pooled p50/p95/p99/max, and every case over 100 ms.
- `limits`: wall, global per-test, and an `exceptions` map.
- `goal`: see Q4.
- `status`: `provisional` or `accepted`, with a `measure` record.
- `approvals[]`: append-only.

Raw full reports stay as CI artifacts and in `var/tmp/tests/…`, never tracked. Records are written by `make test-budget-record RUN=<id>`, which fetches the artifact and checks that the run succeeded, ran on `main`, and matches the env key. Records are never hand-typed.

How each rule is enforced:
- **Fewer than 5 baselines:** record-only. Validity is enforced, and timings are printed as `no limit yet (n/5)`.
- **Provisional:** once there are 5 or more baselines, limits are enforced. While `status` lacks a `measure` record (owner, date, the baseline run IDs it accepts), every output line carries `PROVISIONAL`.
- **Increase needs owner approval:** the checker loads the same file at `git merge-base HEAD origin/main`. Fetch-depth 0 is already set; a missing base ref is a failure. The checker refuses when any of these happen without a matching `approvals[]` entry `{key, from, to, reason, run_ids, approved_by}` added in this diff:
  - a limit rises,
  - a new exception key appears,
  - the exception count grows,
  - formula parameters change,
  - the env key changes.

  Base entries must be byte-identical (append-only). Tightening never needs approval.
- **Tier-move evasion:** if a node ID that is an exception, or that breached its limit on the base ref, leaves the fast inventory while still being collected under another marker, it fails with the speed-up diagnostic.
- **Inventory:** compare reported cases with the collected fast IDs from the same run. Drop the draft's tracked `fastTestIds`, which would hold 5,600 IDs that change with every new test and conflict across agent branches.

**Reason:**
- The checker always recomputes limits, so neither agents nor people can edit a number in by hand.
- Comparing against the base ref makes any increase show up in the diff and require a record.
- The draft's `ownerApproved: true` boolean is self-attested, so it proves nothing.
- Residual: the checker cannot prove who approved. `main` has no branch protection (API returned 404). `CODEOWNERS` lists `@adamjackson`, while the repo is `adam-jackson-cf/raidar` ([INFERENCE] the handle may not match). True approval needs branch protection with required code-owner review on `.omp/test-budgets.json`, or a signed-commit check on the approval entry.

## Q4 — The 30 s target

**Decision:** Track it as a separate goal (`goal.fastTierWallMs = 30000`). Do not use it as the limit. Every run reports `fast tier X s; limit Y s (provisional|accepted); goal 30 s; gap Z s`, and the gap never fails the gate. The goal can only be changed by the owner and is never raised. When a measured limit falls to 30 s or below, the goal has been met, and the limit enforces it from then on.

**Reason:**
- Using 30 s as the limit fails today, at 58–100 s in CI. It would either block every commit or push people to "fix" it by moving tests to another tier, which the contract forbids.
- `test-tiers.md:17-19` already calls these budgets "proposals, not measured feasibility claims".
- At about 5,600 cases with coverage running one test at a time, 30 s means about 5 ms per case. That probably needs parallelism such as xdist as well as speed-ups ([INFERENCE]).
- Adding parallelism changes the measurement environment, so it needs a re-baseline with approval.

## Q5 — Risks

1. **CI noise:** pytest wall ranged 57.8–99.8 s, and the slowest case 1.09–1.81 s. If this split comes from bimodal CPU types ([INFERENCE]), 5 baselines that all land on fast VMs will produce a limit that flakes on slow ones. Mitigation: record the CPU model, prefer 10 baselines, and refuse to set limits if all baselines share one CPU model.
2. **Suite growth:** the fast tier grew about 5% (5,374 → 5,641 cases) in about 3 hours of commits. The 25% wall headroom could be used up within days, creating steady pressure to approve increases. That pressure is intended, but expect approval churn, and watch for any drift toward rubber-stamping.
3. **CI-only enforcement:** developers learn about breaches about 10 minutes later in CI. The hook's advisory line reduces this but does not remove it.
4. **Approval identity is not enforced** without branch protection, and the `CODEOWNERS` handle may not match.
5. **`ubuntu-latest` image changes** (`ImageVersion` or OS migration) move timings. Mitigation: record `ImageVersion` and report drift; re-measure when the OS changes.
6. **Exceptions becoming a parking lot.** Mitigation: the exception count cannot grow without approval, and exceptions only ratchet down.
7. **Measurement artefacts:** coverage tracer overhead and session fixture setup billed to the first case. Limits are valid only under the same coverage config, Python pin and serial run; changing any of these means re-baselining.
8. **Draft checker defects to fix before merge:**
   - a duplicate `make test-fast` run;
   - self-attested `ownerApproved`;
   - a tracked full ID inventory;
   - a single `perTestMs` with no exceptions;
   - no base-ref comparison;
   - the plugin hard-codes the marker set instead of reusing `FAST_SELECTOR`.
9. **Adjacent:** the slow tier already exceeds its 300 s budget in CI (329–389 s). The same design should apply there later; it is out of scope for this vote.