## 1. Adaptation

**Tiers.** Markers are registered in `orchestrator/pyproject.toml` and enforced with `--strict-markers`. Every marker applies to the whole file through `pytestmark`, and no file mixes tiers.
- **fast** (no marker): logic, schemas, decisions, CLI through `CliRunner`, and process, git or Docker shells driven through an injected runner. A few small `tmp_path` files are allowed. Build records in memory, inject clocks, never sleep. No network beyond loopback fakes.
- **slow**: real `git`, `bash`/`sh`/`python`/`uv`/`make` processes, `supervise` forks, real run directories and real-clock waits. Never Docker.
- **docker**: needs a ready target and the pack image. It skips without one, and `RAIDAR_REQUIRE_DOCKER=1` turns the skip into a failure. Two extra markers sit on top of it for the phase-exit suites: `security` on today's four `SECURITY_GATE_TESTS` files, and `perf` on `test_overhead_fixture_run.py`.
- **Process guard.** In `tests/conftest.py`, one `sys.addaudithook` plus a per-test flag set in `pytest_runtest_setup` and cleared in teardown, so fixtures are covered too. For an item marked neither `slow` nor `docker`, it raises on these audit events, naming the test: `subprocess.Popen` (this also covers `asyncio.create_subprocess_*`), `os.system`, `os.posix_spawn`, `os.fork`, `os.exec`, `os.spawn`, `pty.spawn`. A fake that replaces `subprocess.run` or `Popen` stays allowed.
- **Docker probe.** Move it out of collection. Today the module-level `docker_target_ready()` calls cost 20.3 s of a 21.4 s collection; without those files collection takes 1.29 s (measured). Probe once in `pytest_collection_modifyitems`, and only when a selected item is marked `docker`.
- **Budgets.** Fast tier ≤ 60 s wall on the owner's Mac (measured on the owner's machine, not CI). Any fast test over 0.1 s in `--durations=10` gets reviewed. Slow tier ≤ 5 min [INFERENCE: not measured].
- **Coverage: not 100% now.** The last real run measured 95.14% line coverage over the whole suite: 37,735 statements, 1,833 missed, 266 files below 100%. Branch coverage has never been measured (`has_arcs: False`). Moving process-bound tests out of the fast tier will cut fast-tier coverage further.
  - **Scope:** fast tier only, line plus branch, over `orchestrator/src`, `contract/src` and every `lab/stages/*/*/tool/src` (today's scope; dropping any of it would count as widening `omit`).
  - **Ratchet:** `fail_under` = the measured fast-tier baseline, floored to 2 decimals. Each slice raises it in the same commit and never lowers it.
  - **Entry bar for contract 1.1.0:** ≥ 95.00. 100 stays the stated direction.
- **Make targets.** New targets are `make test-fast` (fast tier with coverage; the pre-commit hook switches to this), `make test-slow` (`-m "slow and not docker"`) and `make test-docker [TARGET=]` (`-m docker`, `RAIDAR_REQUIRE_DOCKER=1`). `make security-gate` becomes `-m security`; `make perf-gate` is unchanged.
- **What `make quality` runs:** today's checks, then the fast tier with the coverage gate, then the slow tier without coverage. Docker stays out because it needs a target.
- **Tier reference location:** `.agents/skills/test-patterns/references/test-tiers.md` (tiers, markers, budgets, commands), plus one pointer line in README. Nothing goes under `docs/`.
- **Skill text, versus FXL:**
  - Description: "creating, moving or modifying tests under `orchestrator/tests/` for `orchestrator/src`, `contract/src` or stage `tool/src`".
  - Tier link: points to `references/test-tiers.md`.
  - Fast bullet: the scope above, and "keep `fail_under`; never lower it".
  - Slow bullet: the Raidar process list above.
  - FXL's Quality tier is replaced by the docker, security and perf tiers.
  - Guard list: adds `os.fork`, `exec`, `spawn` and `pty`.
  - "Before committing": `make test-fast`; `make test-slow` for engine, subject, grading, cli, scripts or test-support changes; `make test-docker` for `engine/trial|target`, `subject/environment|primitive`, `grading/runtime` or pack changes; security and perf gates at phase exit.
  - FXL's "quality job" section becomes "Run the Docker tiers when…".
- **Hard rules:**
  - Kept verbatim: no wiring or mock-echo tests; no weakened assertions, no pragma, no `omit` widening (delete unreachable code instead); never lower `fail_under`.
  - FXL's gate-pack bullet becomes: "stage tool sources stay in coverage; released package bytes are frozen (raidar-release); prove package behaviour beyond in-process units through `make stage-check`".
  - "Nothing under `orchestrator/src` or `lab/stages` may be shaped to one problem or experiment."
- **Runbook references.** The skill gains a section, "Regressions and verification", that cites coding-guidance `references/regression-coverage.md` (name the broken rule, extend the existing behavioural scenario, no single-value pins) and `references/verification.md` (scale to blast radius). AGENTS.md and CLAUDE.md each get one line: "Tests follow `.agents/skills/test-patterns`; regression and verification per coding-guidance `regression-coverage.md` and `verification.md`."

## 2. C1

- **Cause: rounding to whole percents, at precision 0 by default.** pytest-cov 7.0.0 reads `precision` from the coverage config and falls back to 0 (`plugin.py:269-270`). It then calls coverage 7.13.4's `should_fail_under`, which returns `round(total, precision) < fail_under`. `round(94.99, 0)` is 95.0, which is not below 95, so the gate passes.
- **Observed:** `should_fail_under(94.99, 95, p)` gives False at p=0, False at p=1 and True at p=2.
- **No config exists:** `orchestrator/pyproject.toml` has no `[tool.coverage]` section, and `quality.py:27` passes `--cov-fail-under=95` on the command line.
- **Second symptom, same bug:** pytest-cov's summary line (`plugin.py:410`) compares unrounded, so it can print "FAIL … not reached" while the process exits 0 [INFERENCE from the code; that run's output not seen].
- **Fix:**
  - In `orchestrator/pyproject.toml`, add `[tool.coverage.run] branch = true` and `[tool.coverage.report] precision = 2` with `fail_under = <ratchet>`.
  - Delete `COVERAGE_FAIL_UNDER` and the `--cov-fail-under` argument from `quality.py`, so the threshold has a single source.
  - Leftover: at precision 2, a total of 94.995 or above still rounds to 95.00. That 0.005-point resolution matches what the report displays.
- **Regression-coverage note:** no existing test covered the threshold comparison, and there is no behavioural scenario to extend. That gap is flagged here; no single-value 94.99 test is to be added.

## 3. C2

- **Decision: keep the verify gate's whole-check cap at 300 s.**
  - `MAX_TIMEOUT_S = 300` and `Timeout … le=MAX_TIMEOUT_S` are in `contract/src/raidar_contract/package/manifest.py:35,86`. Raising the cap changes a manifest refusal, which means `stage/2`, a MAJOR bump, two votes and an `rc` tag (design §5.6, C7).
  - The `raidar/verify` manifest's checks are each `timeout_s: 300`, with `params: {test_timeout: 60}` per test. Per R3 this package is the control, so it shouldn't move without a registered question.
- **The new test approach has no bearing on it.** The approach governs Raidar's own suite. The agent-facing equivalent would be verify's `pytest-templates` and `test-per-aspect` skills, and editing them changes the package digest and forces a re-release.
- **When to revisit:** if smoke or measured jobs show `tests`-check timeouts with a passing canary (counted as agent outcomes under design §5.2), raise a `stage/2` question with that evidence.
- **Stale text:** `split-design.md:409` still says "past 600 s" and should be corrected to 300.

## 4. Plan

**Inventory** (3,379 tests collected; 168 `test_*.py` files):
- **Markers:** no tier markers and no `markers=` registration. Only skipif guards:
  - 5 module-level guards: `test_package_at3_docker`, `test_trial_health_audit_docker`, `test_workflow_verify_docker`, `test_agent_liveness_monitor_docker` (env opt-in), `test_overhead_fixture_run` (env opt-in).
  - 1 fixture skip: `test_declared_wheels_docker`.
- **Files mixing tiers:** `test_step_reset.py` (bash units plus `docker_trial` at :174) and `test_verifier_copy_sandbox.py` (a docker test at :142).
- **Docker inside `make quality`:** Docker tests run there whenever the default target is ready [INFERENCE: target not probed].
- **Process starts:**
  - 32 files call `subprocess`, `create_subprocess_*` or `os.system` directly, for example `test_cli_commands.py:322` (`make`) and `test_primitive_payload_boundaries.py:1005`.
  - 23 files use spawning test doubles: `primitive_support` (`bash -c`), `workflow_support` (a global plugin), `criteria_support`, `verifier_support`.
  - `supervise.py:143` calls `os.fork` (29 warnings in `test_primitive_gate`).
  - The full offender list comes out of the first guard run [INFERENCE: roughly 60–80 files].
- **Writing to disk:** 142+ files use `tmp_path` (writing trees is the norm, e.g. `two_step_trial` and `build_fixture_store`). 7 create real git repos.
- **Sleeps:** 19 files. About 12 sleep in the test process itself, e.g. `test_trial_execution.py:105`, `test_assessment_store_concurrency.py:38`, `test_agent_liveness_monitor.py:291`, `test_workflow_runner.py:239` and the polling in `test_detach_job.py`. The others wait on child timeouts, e.g. `test_primitive_payload_boundaries.py:810`, `test_dependency_resolver.py:217`, `test_submission_dependencies.py:622`.
- **Wiring and mock-echo:** `test_quality_gate_commands.py:7-20`, `test_cli_commands.py:166` (COVERAGE_FILE) and `:192` (the env-setup call list). Of the 19 files that assert on call lists, most check `calls == []` ("nothing ran"); that is behaviour, so keep them.
- **Narrow regression pins:** `test_analysis_command.py:621`, `test_cli_commands.py:310`, `test_experiment_spec_behaviors.py:297` (removed flags), and `test_cli_commands.py:23` (version equals pyproject).
- **Slow:** 6 files with 274 tests took 69.3 s; `test_primitive_gate` tests take 1.5–4.1 s each. Full-suite time not measured.
- **Coverage pragmas or `omit`:** none in `src`, `contract` or stage tools. Weakest files: `gate_tool.py` 67.87%, `verify_tool/checks.py` 0%, `cli/target.py` 65.66%, `define_tool/sections.py` 58.44%.

**Slices.** One owner does S0 first. After that, slices A–F each own their test files, their support module and the matching src modules. A cross-slice src change goes through the S0 owner.

| Slice | Covers |
|---|---|
| **S0** (shared) | Skill and reference; markers; `[tool.coverage]` (the C1 fix); guard and Docker probe in conftest; `quality.py`; Makefile targets and `make help`; AGENTS.md, CLAUDE.md, README; provisional file-level `slow` on every guard offender; measured baseline becomes the first `fail_under` |
| **A** | Primitives and gate: `test_primitive_*`, `test_gate_*`, `test_stage_check_*`, `test_stage_container_lifecycle`; `primitive_support` |
| **B** | Workflow, verify and judging: `test_workflow_*`, `test_acceptance_*`, `test_jev_judge_parity`; `workflow_support`, `workflow_attack_support`, `criteria_support` |
| **C** | Engine: `test_trial_*`, `test_detach_job`, `test_target_*`, `test_step_reset`, `test_agent_liveness*`, the docker files, `test_run_scoped_docker_environment`, health tests |
| **D** | Grading: `test_verifier_*`, `test_submission_dependencies`, `test_dependency_resolver`, `test_offline_grading*`; `verifier_support` |
| **E** | Scoring: `test_coverage_scorers`, `test_code_quality_*`, `test_assessment_*`, `test_analysis_*`, `test_jev_client_and_cache` |
| **F** | Design, CLI, release and scripts: `test_cli_commands`, `test_experiment_*`, `test_problem_*`, `test_release_*`, `test_structure_check`, `test_python_fanout_check`, `test_quality_gate_commands`, define-tool tests |

**Acceptance per slice:**
- The guard runs clean, and no file mixes tiers.
- No sleeps or real clocks in fast tests; every fast test is ≤ 0.1 s, or a justification is listed.
- Slow tests only prove seams and don't repeat fast-tier logic.
- Deleted wiring, echo and pin tests are listed, each with the broken rule it would have guarded.
- Diff review shows no weakened assertion, pragma or `omit` change.
- `fail_under` is raised to the slice's new floor.
- `make quality` is green.

**"All green" before contract 1.1.0 starts:**
- `make quality` exits 0, with fast-tier coverage ≥ 95.00 line plus branch at precision 2, within 60 s, and the slow tier passing.
- `make test-docker`, `make security-gate` and `make perf-gate` pass on the local OrbStack target, failing rather than skipping when it's missing.
- No `xfail` or skip added beyond the environment skips, and the skill and rule lines are in place.

I edited no repository file. One side effect: the first `uv run` rebuilt and reinstalled `raidar` and `raidar-contract` in `orchestrator/.venv`.
