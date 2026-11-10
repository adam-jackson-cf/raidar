# Test tiers

Markers (`slow`, `quality`, `docker`, `security`, `perf`) are registered in
`orchestrator/pyproject.toml` and enforced with `--strict-markers`. They apply
to a whole file through `pytestmark`; no file mixes tiers. `security` and `perf`
overlay `docker`.

| Tier     | Markers              | Selector                                                                | Permitted work                                                                                                                                               | Budget (prospective)                 | Target                           |
| -------- | -------------------- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------ | -------------------------------- |
| Fast     | none                 | `not slow and not quality and not docker and not security and not perf` | In-memory decisions, injected runners and clocks, a few small temp files; no processes, network (loopback fakes aside), sleeps or run-directory construction | < 30 s incl. collection and coverage | `make test-fast`                 |
| Slow     | `slow`               | `slow and not quality and not docker`                                   | Real local processes, git repositories, run directories, loopback servers, real-clock seams; no Docker                                                       | < 300 s                              | `make test-slow`                 |
| Quality  | `quality`            | `quality and not docker`                                                | Canaries proving quality rules accept and refuse correctly; isolated tool processes and workspaces                                                           | < 600 s                              | `make test-quality`              |
| Docker   | `docker`             | `docker and not security and not perf`                                  | Real container/Harbor seams on a ready target; no real models                                                                                                | < 1800 s                             | `make test-docker TARGET=<name>` |
| Security | `docker`, `security` | `security`                                                              | Planted-code and container security scenarios                                                                                                                | < 1800 s                             | `make security-gate`             |
| Perf     | `docker`, `perf`     | `perf`                                                                  | No-model fixture at concurrency 1 and 3; accepted N1 budgets unchanged                                                                                       | < 1800 s                             | `make perf-gate`                 |

Budgets are measured wall time with dependencies and images provisioned
(provisioning recorded separately). They are proposals, not measured
feasibility claims; never raise a performance threshold to pass.
The host-tier timing reference is the pinned CI runner (`ubuntu-24.04`,
Python 3.13.7). Record local baselines with their platform, Python version and
wall time; they are not CI feasibility evidence. Docker phase-exit proof uses
the trusted remote `mbp` target.

## Fast-tier measured limits

The single fast run in `make quality` writes an invocation-bound JSON report
under `var/tmp/tests/budgets/`. `make gate-commit` consumes that report with
`make test-budget-check`: invalid, skipped or truncated measurements fail on
every host; timing breaches are advisory locally and blocking on matching CI.
CI profile mismatches fail as infrastructure errors. No second fast run occurs.

Tracked `.omp/test-budgets/` holds policy, imported baselines and external
approval references. Before five complete same-revision CI measurements exist,
the status is `no limits (0/5)` (or the actual count); report validity still
blocks. Limits are recomputed from evidence, not hand-entered: wall is
`ceil(1.25 * max)`, and common per-case is p95 of each case's maximum total
setup/call/teardown time times 1.5, rounded up to 10 ms. Named outliers need
owners, concrete remedies and review deadlines. The 30-second wall and
100-ms per-case goals remain separate from calibrated limits.

Dispatch `test-budget-measure.yml` at a fixed revision (default five sequential
jobs), then run `make test-budget-record RUNS="<GitHub run ids>"` to import
successful complete artifacts. Ordinary reports are never baseline records.
New baseline records must match GitHub measurement artifacts as canonical JSON.
Retained trusted records cannot be rewritten and need
no repeated artifact download after retention expires.
Policy increases require an owner GitHub review bound to the exact policy diff
digest and commit, verified through the API; a committed approval boolean is
not authorization. Status remains provisional until verified measure evidence.
Generate the policy with `make test-budget-limits EXCEPTION_METADATA=<path>`.
The tracked metadata maps each computed outlier's exact nodeid to its owner,
concrete remedy and review deadline; ceilings always come from the baselines.
The generated provisional policy records a pending, policy/profile-bound
measure run, not an externally verified acceptance.
The first calibrated limits and their recomputed outlier ceilings need no
approval when replacing null limits under an unchanged profile and formulas.
Later exception additions, ceiling increases and deadline extensions do.

For an increase, request the owner's review with one body line
`Policy-Diff-SHA256: <policy_diff_digest(base, proposed)>`. Record its
`repository`, `pullNumber`, `reviewId`, `commit` and `policyDiffDigest` in
`approvals/<id>.json` in a later commit without changing `limits.json`.
The checker fetches the review and verifies owner identity, approved state,
digest, commit, and identical policy bytes at that reviewed commit.
An accepted policy additionally references externally verified measurement
completion through `measurementApproval`; hand-editing status cannot accept it.

## Process guard

`orchestrator/tests/conftest.py` loads the audit-hook plugin, whose per-test flag
spans the entire test protocol, so fixture setup and teardown are covered. For items that
are not `slow`, `quality` or `docker` it raises, naming the test, on real
process starts (`subprocess.Popen`, `os.system`, `os.posix_spawn`, `os.fork`,
`os.exec*`, `os.spawn*`, `pty.spawn`). Injected fakes stay allowed.

## Docker

The readiness probe runs once in fixture setup, on the first selected `docker`
item, never during collection. Under `RAIDAR_REQUIRE_DOCKER=1` a missing
selected target fails instead of skipping. Runs use the `mbp` target
(`TARGET=mbp`; setup in `docs/reboot/handoff.md`).

## Coverage

- Fast tier only earns the fast floors; other tiers' coverage is neither
  combined nor counted.
- Scope: `orchestrator/src`, `contract/src` and every `lab/stages/*/*/tool/src`.
  No `omit` widening, no pragmas.
- Line and branch are enforced separately, each by integer covered/total count
  against its floor; precision is for display only (`[tool.coverage.report]`
  has no `fail_under`). Floors are `line_floor` and `branch_floor` in
  `[tool.raidar.coverage]` of `orchestrator/pyproject.toml`, the single source.
- Ratchet: after each accepted slice, floor = `max(previous, floor(measured \*

100. / 100)`; never lowered. A measured baseline below 95 is recorded as the
     temporary floor with its gap. "All green" requires both >= 95.00. Target 100.

- Newly introduced or extracted decision logic requires complete fast-tier line
  and branch coverage.

## All green

Fast, slow and quality tiers pass, and Docker, security and perf pass on the
`mbp` target.

## Targets

- `make quality`: structure, smoke-dry-run, releases, experiment-locks, ledger,
  ruff, import contracts, lizard, mypy, fan-out, plus the fast tier with coverage.
- `make test-fast` (`raidar quality test-fast`), `make test-slow`,
  `make test-quality`, `make test-docker TARGET=<name>`
  (`RAIDAR_REQUIRE_DOCKER=1`), `make security-gate`, `make perf-gate`.
- `make gate-commit`: whole-tree path policy, identity equality, `make quality`,
  then affected slow/quality seams selected by `scripts/checks/repository-owners.json`.
  Unknown ownership runs all local T2 seams. The check-only pre-commit hook
  dispatches this target through `.omp/gate.json`.
- `make gate-ci`: commit gate, then the full slow and quality tiers, on every
  push and pull request. Docker, security and perf remain separate target checks.

Install the hook in each clone (it never fixes or stages files):

```bash
env -u AGENT uv run --project orchestrator --extra dev pre-commit install --hook-type pre-commit
```
