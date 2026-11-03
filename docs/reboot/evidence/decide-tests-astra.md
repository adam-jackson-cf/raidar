## 1. Adaptation

**Vote: adopt FXL’s framework, refactor the entire suite first, then start contract `1.1.0`.** All changes below are proposed; no files edited.

**Tiers and proposed budgets**—measured wall time on the designated CI runner with dependencies/images already provisioned:

| Tier | Markers | Permitted work | Budget |
|---|---|---|---|
| Fast | No tier marker | In-memory decisions, injected runners/clocks, a few small temporary files; no processes, networking, sleeps or run-directory construction | **<30 s**, including collection and coverage |
| Slow | `slow` | Real local processes, Git repositories, directory scenarios, loopback servers and necessary real-clock seams; no Docker or external services | **<300 s** |
| Quality | `quality` | Real canaries proving quality rules accept/refuse correctly; isolated tool processes and workspaces | **<600 s** |
| Docker | `docker`; optionally `quality` | Real container/Harbor seams using a selected healthy target; no real models | **<1,800 s** |
| Security exit | `docker, security` | Existing planted-code/container security scenarios | **<1,800 s** |
| Performance exit | `docker, perf` | Existing no-model fixtures at concurrency 1 and 3 | **<1,800 s**, **plus unchanged accepted performance budgets** |

- These elapsed-time limits are proposals, not measured feasibility claims. Record provisioning separately; never increase existing performance thresholds to make tests pass.
- Register markers and enable strict-marker checking. Use file-level marks; never mix execution tiers within a file.
- Fast selection: `not slow and not quality and not docker and not security and not perf`.
- **Process guard:** default-deny during collection and fast-test setup/call/teardown, including fixtures. Cover `subprocess`, `os.system`, `posix_spawn`, fork/exec and asynchronous/multiprocessing launch paths. Injected runners remain allowed; real launches require a non-fast execution scope.
- Move import-time Docker/Bash probes into marked fixture setup. A fixture must not bypass the guard merely because another test uses it.
- Docker targets must fail—not skip—when the selected target/image is unavailable. Unselected Docker tests are deselected, not evidence of passing.

**Coverage decision:** **95% fast-only line coverage and 95% fast-only branch coverage at refactor completion**, not an immediate 100% mandate.
- Current configuration establishes neither branch coverage nor a fast-only baseline. [INFERENCE] Immediate 100% would encourage coverage-driven tests before behavioral classification is sound.
- Preserve the entire current production scope: `orchestrator/src`, `contract/src`, and every discovered `lab/stages/*/*/tool/src`. No blanket exemption for shipped stage tools or primitive payloads.
- Enable branch measurement; enforce line and branch percentages separately—coverage’s combined percentage can conceal a weaker branch result.
- During migration, measure both baselines; neither may decrease. Completion still requires both ≥95%.
- Thereafter ratchet each floor to `max(previous_floor, floor(measured_percentage × 100) / 100)` after each accepted change; never lower it. Target 100%; newly introduced/extracted decision logic requires complete line/branch coverage.
- Only fast-test data earns the fast floor. Keep other tiers’ coverage separate; do not combine it to conceal missing fast coverage.

**Public commands:**
- `make quality`: retain structure, smoke-dry-run, releases, experiment-locks, ledger, Ruff, import-boundary, complexity and mypy checks; replace the undifferentiated pytest run with fast-only coverage.
- Add `make test-fast`, `make test-slow`, `make test-quality`, `make test-docker TARGET=…`.
- `test-quality` selects `quality and not docker`; `test-docker` selects `docker and not security and not perf`, including container-based quality canaries.
- Preserve `make security-gate TARGET=…` and `make perf-gate TARGET=…`; keep them separate from `make quality`.
- Run slow and host quality tiers on push; run affected Docker canaries before merge and all tiers at refactor exit.

**Exact skill adaptations:** copy the FXL skill to `.agents/skills/test-patterns/SKILL.md`, retaining unchanged guidance except these replacements:
- Description → “Use when creating, moving or modifying Raidar tests, fixtures, test support or quality-gate configuration.”
- Tier-reference link → `[Test tiers](references/test-tiers.md)`. Put the canonical table, selectors, coverage ratchet and budgets there; link it from README. No `docs/` access needed.
- Replace FXL’s tier/guard bullets with the rules above; replace FXL source paths and fixed-100 wording with Raidar’s full production scope and ratchet.
- Replace the FXL gate-pack exemption with: “Do not exempt Raidar stage tool sources or trusted payloads. Fixture programs are inputs, not production coverage targets; their real execution belongs in slow, quality or Docker scenarios.”
- Retain the hard prohibitions on wiring-only/mock-echo tests, weakened assertions, new coverage pragmas, widened omissions and task-shaped production code. Delete genuinely unreachable code rather than manufacture coverage.
- Replace raw pytest commands with the public make targets above; retain duration inspection and the **0.1 s fast-test investigation threshold**.
- Replace FXL hook/path claims with Raidar triggers: quality runner/check scripts, primitive gate/payload code, stage tooling, policy, test support and Python/tool/lock upgrades require relevant quality/Docker canaries.
- Add to both the skill and repository rules: “Read `~/.agents/skills/coding-guidance/references/regression-coverage.md` and `…/verification.md`. Name the broken behavioral rule; extend an existing behavioral scenario rather than pinning a bug value or wording. If none exists, report the coverage gap. Scale verification to blast radius.”
- Preserve “fast behavior first” and thin I/O boundaries; necessary slow seams must prove observable outcomes, not duplicate unit logic.

## 2. C1

**Cause confirmed in installed source: threshold comparison rounds using precision zero.**
- `quality.py:136–142` supplies `--cov-fail-under=95`, without precision; `pyproject.toml` supplies no coverage precision.
- Installed versions: **pytest-cov 7.0.0**, **coverage 7.13.4**.
- `pytest_cov/plugin.py:266–270,365–370` obtains coverage precision and calls `should_fail_under`; `coverage/results.py:483–502` returns `round(total, precision) < fail_under`.
- The terminal summary separately compares the raw total (`plugin.py:408–416`), explaining how failure-looking output can accompany a successful exit.
- **Exact minimal fix:** add `[tool.coverage.report]` with `precision = 2` and `fail_under = 95` to `orchestrator/pyproject.toml`; pass `--cov-config=pyproject.toml --cov-precision=2` explicitly from the quality runner.
- Read-only boundary exercise confirmed precision 2 rejects **94.49, 94.50, 94.94 and 94.99**, and accepts **95.00 and 95.01**. No suite rerun.
- Two decimals meet the stated acceptance criterion but still round values within 0.005 points. For the proposed separate line/branch ratchets, compare integer covered/total counts directly against the configured percentage; use precision only for display.

## 3. C2

**Keep 300 s. Do not change it in contract `1.1.0`.**
- `lab/stages/README.md:88–95,208–213` explicitly specifies the whole-check maximum and acknowledges that the spike’s 600-second watchdog was not preserved.
- `lab/stages/raidar/verify/stage.yaml:10–13` assigns 300 s to each check. This is separate from the package’s 60-second default **per-test** timeout.
- Raidar’s own test-tier refactor does **not** demonstrate that an agent-produced suite fits within 300 s. The two suites have different owners and purposes.
- Retain the explicit parity exception; do not claim identical spike timeout behavior. Do not evade the cap by chaining checks.
- If representative trial evidence justifies a larger cap, make it a **`stage/2` MAJOR** decision: two votes, FXL consumer-impact review, release candidate/migration notes, all active packages migrated, dependent workflows re-released and relocked. Preserve historical identities.

## 4. Plan

**Observed inventory**—AST counts exclude embedded fixture-program strings; paths below are under `orchestrator/tests/`:
- Collection succeeded: **3,379 cases, 168 test files**; **2,129 test-function definitions**, plus **30 support/config modules**.
- Markers: **320 `parametrize` references; 14 `skipif` references across 12 files; zero execution-tier markers**.
- Processes: **52 direct launch callsites in 27 files**, including **20 test files**, all lacking tier markers; additionally multiprocessing in `test_assessment_store_concurrency.py`. Helper-mediated launches make this a lower bound.
- Docker: **49 collected cases across eight files** identified, including five `*_docker.py` files, the perf fixture, two step-reset cases and one copy-sandbox case; none has a Docker tier marker.
- At least **seven mixed-tier files**: `test_cli_commands.py`, `test_primitive_payload_boundaries.py`, `test_code_quality_boundaries.py`, `test_workspace_seed.py`, `test_step_reset.py`, `test_verifier_copy_sandbox.py`, `test_jev_client_and_cache.py`.
- Filesystem setup: **1,092 test functions request `tmp_path`; 308 direct `mkdir` calls across 100 modules**. Of **72 fixtures**, **47 request `tmp_path`**, and **nine directly create/copy directories**; examples: `gate_env`, `lab`, `home` in primitive-gate, workflow-package-runner and define-shim tests.
- Timing: **21 sleep calls across 13 files**, including cooperative yields; **24 clock reads across nine files**. Examples: concurrency-store races, liveness waits, HTTP trickling, workflow deadlines and reply-parser timing.
- Wiring: at least **two wiring-only functions/13 collected cases** in `test_cli_commands.py:281–307,426–470`; a mock-echo assertion appears in `test_quality_gate_commands.py:7–20`, alongside a worthwhile failure-path assertion.
- Regression overlap: **one verified overlapping rule pair**, malformed criterion IDs in `test_define_compact.py:287–289` and `test_define_review_fixes.py:418–428`; preserve both classification and single-report guarantees when consolidating. The latter file contains **51 review-fix cases**, not 51 proven duplicates.
- Coverage exemptions: **zero explicit `no cover`/`no branch` pragmas found in inspected production roots; no coverage-omit configuration in the inspected project configuration**. Historical omission widening was not checked.
- Actual slow-test durations and total redundant-test counts remain **unmeasured/unestablished**; process/sleep inventories identify review candidates, not timings.

**Parallel slices, after one shared owner lands the conventions:**
- **Shared owner first:** skill/reference, repository rules/README, markers, collection-safe fixtures, process guard, coverage comparison/ratchets, make selectors and CI. Assign every file/support module exactly one owner.
- **A — primitives/workflows:** `test_primitive_*`, workflow/package-runtime tests, criteria and execution support; separate pure refusals/decisions from real process lifecycle seams.
- **B — definition tools:** `test_define_*` and define support; consolidate review-fix cases into behavioral families, preserve parser/state-transition boundaries.
- **C — grading/problems:** verifier, dependency, submission, problem-import/validation tests and their support; isolate real Git/uv/grader executions.
- **D — engine/agents:** trial, target, liveness, adapters, reset, provisioning and container tests; replace incidental sleeps with controlled synchronization.
- **E — measurement/evaluation:** scorer, assessment, analysis, health, Jev and review-projection tests; retain real concurrency/storage seams separately.
- **F — repository interface:** CLI, release, registry, structure, fan-out, imports and ledger tests; remove wiring/text pinning while retaining public refusal and execution outcomes.

**Acceptance per slice:** every case classified or explicitly justified for deletion; no mixed-tier files; fast guard passes; behavioral/error/boundary assertions preserved; coverage ratchets do not regress; affected real seams exercised; durations reported. Shared-source changes require broader dependent-tier verification, not merely the owner’s tests.

**“All green” before `1.1.0`:** the integrated revision passes `make quality`, slow, quality, Docker, security and performance tiers; fast line/branch coverage each ≥95%; proposed elapsed budgets and unchanged performance budgets pass; required cases actually execute, with no missing-target skips or unexplained exclusions. Required `mbp` evidence remains required unless explicitly waived. Frozen release bytes stay untouched; runtime extraction cannot silently alter released contracts/packages.

**Verification performed:** collection, static inventories, source inspection and the proposed precision boundary exercise only. No full suite, Docker, `make quality`, file edits or green-suite claim.
