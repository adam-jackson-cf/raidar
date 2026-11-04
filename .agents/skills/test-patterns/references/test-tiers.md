# Test tiers

Markers (`slow`, `quality`, `docker`, `security`, `perf`) are registered in
`orchestrator/pyproject.toml` and enforced with `--strict-markers`. They apply
to a whole file through `pytestmark`; no file mixes tiers. `security` and `perf`
overlay `docker`.

| Tier | Markers | Selector | Permitted work | Budget (prospective) | Target |
|---|---|---|---|---|---|
| Fast | none | `not slow and not quality and not docker and not security and not perf` | In-memory decisions, injected runners and clocks, a few small temp files; no processes, network (loopback fakes aside), sleeps or run-directory construction | < 30 s incl. collection and coverage | `make test-fast` |
| Slow | `slow` | `slow and not quality and not docker` | Real local processes, git repositories, run directories, loopback servers, real-clock seams; no Docker | < 300 s | `make test-slow` |
| Quality | `quality` | `quality and not docker` | Canaries proving quality rules accept and refuse correctly; isolated tool processes and workspaces | < 600 s | `make test-quality` |
| Docker | `docker` | `docker and not security and not perf` | Real container/Harbor seams on a ready target; no real models | < 1800 s | `make test-docker TARGET=<name>` |
| Security | `docker`, `security` | `security` | Planted-code and container security scenarios | < 1800 s | `make security-gate` |
| Perf | `docker`, `perf` | `perf` | No-model fixture at concurrency 1 and 3; accepted N1 budgets unchanged | < 1800 s | `make perf-gate` |

Budgets are measured wall time with dependencies and images provisioned
(provisioning recorded separately). They are proposals, not measured
feasibility claims; never raise a performance threshold to pass.
The host-tier timing reference is the existing CI runner (`ubuntu-latest`,
Python 3.12). Record local baselines with their platform, Python version and
wall time; they are not CI feasibility evidence. Docker phase-exit proof uses
the trusted remote `mbp` target.

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
- Ratchet: after each accepted slice, floor = `max(previous, floor(measured *
  100) / 100)`; never lowered. A measured baseline below 95 is recorded as the
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
- CI runs slow and quality on push. The pre-commit hook runs `quality gates`;
  pre-push runs `make quality`.
