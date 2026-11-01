---
name: "test-patterns"
description: "Use when creating, moving or modifying Raidar tests, fixtures, test support or quality-gate configuration."
---

# Guidance

## Pick the tier by what the test drives

What each tier is, its selector, budget, coverage floors and make target:
[Test tiers](references/test-tiers.md).

- Fast (no marker): logic, schemas, decisions, CLI through `CliRunner`, and
  process, git or Docker shells driven through an injected runner. A few small
  files in `tmp_path` are fine. The tier covers the full production scope
  (`orchestrator/src`, `contract/src`, every `lab/stages/*/*/tool/src`) with line
  and branch coverage each at or above its floor, and finishes in under 30 s
  including collection and coverage.
- Slow (a file with `pytestmark = pytest.mark.slow`): real `git`, `bash`, `sh`,
  `python`, `uv` or `make` processes, `supervise` forks, real run directories,
  loopback servers and real-clock waits. Never Docker.
- Quality (`quality` marker): a canary proving a quality rule accepts and
  refuses correctly, in isolated tool processes and workspaces.
- Docker (`docker` marker): needs a ready target and the pack image; no real
  models. `security` and `perf` sit on top of `docker` for the phase-exit suites.
- `orchestrator/tests/conftest.py` refuses any process start (`subprocess.Popen`,
  which covers asyncio subprocesses, `os.system`, `os.posix_spawn`, `os.fork`,
  `os.exec*`, `os.spawn*`, `pty.spawn`) in a test that is not `slow`, `quality`
  or `docker`, its fixtures and teardown included. A fake that replaces
  `subprocess.run` or `Popen` is fine.
- Place by what the test drives, not by how long it takes. When one behaviour
  needs both, split it: unit test the decision in the fast tier, prove the
  wiring in the slow tier.

## Write the fast unit test first

- Every behaviour a user can see (output, record, error, exit code,
  termination reason) gets a fast test.
- When code mixes a decision with git, process, disk, Docker or clock I/O,
  extract the decision into a pure function, test that function, and keep the
  I/O shell thin.
- Build inputs in memory (records, parsed data); don't write directories to
  disk.
- Test process, git and Docker shells through an injected or monkeypatched
  runner. Assert the result for success, non-zero exit, timeout and malformed
  output.
- Inject clocks and timeouts. Never sleep in a fast test.

## Use slow tests for seams and scenarios only

- Each slow test proves real end-to-end wiring of a seam or scenario that
  unit tests can't (real git, processes, run directories); don't repeat logic
  the fast tier already covers.
- Never mix tiers in one file; markers go on the whole file. Add new slow
  tests to a slow-marked file.
- Set every timeout on the object that actually waits, not only on the
  definition. A slow test that dominates the tier's time is usually waiting
  out a default it meant to override.

## Hard rules

- Don't test mere wiring, and don't assert that a mock received what the
  test just gave it.
- Never weaken an assertion, add a coverage pragma, or widen coverage `omit`
  to reach a floor; delete unreachable code instead. Never lower a coverage
  floor.
- Stage tool sources stay in coverage. Released package bytes are frozen
  (raidar-release); prove package behaviour beyond in-process units through
  `make stage-check`. Fixture programs are inputs, not coverage targets; their
  real execution belongs in slow, quality or Docker scenarios.
- Nothing under `orchestrator/src` or `lab/stages` may be shaped to one
  problem or experiment.

## Regressions and verification

Read `~/.agents/skills/coding-guidance/references/regression-coverage.md` and
`~/.agents/skills/coding-guidance/references/verification.md`.

- Before fixing, name the broken behavioural rule, beyond the one input that
  exposed it. Check whether an existing behavioural scenario covers it.
- When coverage is missing, extend that scenario to protect the intended
  outcome. If no scenario covers the area, report the coverage gap. Never pin
  a single bug-fix value or wording.
- Scale verification to blast radius: focused tests for a narrow change, the
  broader tiers for shared behaviour, cross-module contracts and workflows.

## Before committing

- `make test-fast`: the fast tier with the `line_floor` and `branch_floor` from
  `[tool.raidar.coverage]` in `orchestrator/pyproject.toml` (never lower them). Check `--durations=10`: a
  fast test over 0.1 s probably still does I/O.
- `make test-slow` when the change touches engine, subject, grading, cli,
  scripts or test support code; CI repeats it on push.
- `make test-quality` when the change touches the quality runner or check
  scripts, policy, test support, or upgrades Python, ruff, mypy, pytest or
  coverage; CI repeats it on push.
- `make test-docker TARGET=<name>` for `engine/trial|target`,
  `subject/environment|primitive`, `grading/runtime`, primitive gate or
  payload code, stage tooling or pack changes; `make security-gate` and
  `make perf-gate` at phase exit.
- Prefix `make`, `uv` and `pytest` calls with `env -u AGENT`.
