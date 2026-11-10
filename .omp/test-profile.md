# Repo test profile

Fill every section with values. `none: <owner-approved reason>` is allowed only in Verification
surface, Nightly scheduler (a Run events field), and Identity-bearing artefacts; elsewhere a
missing value is a reported gap.

## Tiers

- T1 fast: no marker; selector
  `not slow and not quality and not docker and not security and not perf`;
  location `orchestrator/tests/`; command `env -u AGENT make test-fast`.
- T2 slow: marker `slow`; selector `slow and not quality and not docker`;
  location `orchestrator/tests/`; command `env -u AGENT make test-slow`.
- T2 quality: marker `quality`; selector `quality and not docker`;
  location `orchestrator/tests/`; command `env -u AGENT make test-quality`.
- T2 Docker: marker `docker`; selector `docker and not security and not perf`;
  location `orchestrator/tests/`; command `env -u AGENT make test-docker TARGET=mbp`.
- T2 review surface: location `review-surface/tests/`; selector Playwright `chromium` project;
  command `env -u AGENT make review-surface-test`.
- T3 security: markers `docker`, `security`; selector `security`;
  location `orchestrator/tests/`; command `env -u AGENT make security-gate TARGET=mbp`.
- T3 perf: markers `docker`, `perf`; selector `perf`;
  location `orchestrator/tests/`; command `env -u AGENT make perf-gate TARGET=mbp`.
- Perf target selection: `orchestrator/tests/test_overhead_fixture_run.py`, concurrency `1`, `3`;
  output `var/tmp/perf-gate/concurrency-1`, `var/tmp/perf-gate/concurrency-3`.
- Marker registry: `orchestrator/pyproject.toml`, `--strict-markers`;
  file inventory: `scripts/checks/repository-owners.json` field `test_tiers`.
- Public entry point per package: `raidar`: `raidar.cli:main` (`raidar` CLI);
  `raidar-review-surface`: `node server.mjs`, browser `http://localhost:5950`.
- Public entry point per package: gap: approved `raidar-contract` and stage-package entry points.

## Run events

- Commit command: `env -u AGENT make gate-commit` (`.omp/gate.json` field `checks.commit`).
- CI command: `env -u AGENT make gate-ci` (`.omp/gate.json` field `checks.ci`).
- Commit gate: path policy, identity, test evidence, rule coverage, `quality`, budget checker,
  dependency audit and affected local slow/quality seams.
- CI event: every push and pull request; commit gate with `AUDIT_ARGS=--ci`, full slow and quality.
- Additional boundary gate: gap: Docker and review-surface inclusion in commit/CI event commands.
- Nightly scheduler: gap: no scheduled workflow or owner-approved exemption recorded.
- Nightly branches: gap: active-branch enumeration for a nightly event.
- Separate performance workflow: `.github/workflows/perf-fixture.yml`, dispatch and path-filtered
  pull requests to `main`, `master`; `make perf-fixture`, target `ci`, not the full perf gate.

## Fast-tier budget

- Per machine: current calibration profile `ubuntu-24.04`, platform `ubuntu24`, architecture
  `X64`, runtime Python `3.13.7`, workers `1`; policy `.omp/test-budgets/limits.json`.
- Per machine limits: wall `null`, per-test `null`, status `provisional`, exceptions `{}`;
  baseline files `0`; checker calibration minimum `5` complete same-revision observations.
- Goals, not enforced limits: wall `30 s`, per-test `0.1 s`.
- Calibration formulas: wall `ceil_s(1.25*max)`;
  per-test `ceil_10ms(1.5*p95(test_maxima))`; exception `ceil_10ms(1.5*test_max)`.
- Per machine owner approval reference: gap: no approved local-machine baseline or limits.
- gap: current limits are CI-calibrated, local timing advisory and CI timing blocking;
  global local-machine blocking/report-only-CI rule is not implemented by this tooling.
- Duration-report command: `env -u AGENT make test-fast` (`--durations=10`);
  producer `scripts/checks/run-budgeted-fast.py`;
  report `var/tmp/tests/budgets/$TEST_BUDGET_INVOCATION/report.json`.
- Checker command: `env -u AGENT make test-budget-check`;
  invocation-bound producer/checker pair within `env -u AGENT make gate-commit`.
- Measurement workflow: `.github/workflows/test-budget-measure.yml`, `workflow_dispatch`,
  exact `ref`, `run_count` default `5`, sequential jobs, artifact retention `90 days`.
- Import target: `make test-budget-record`, variable `RUNS` containing GitHub measurement run IDs;
  records `.omp/test-budgets/baselines/`, approvals `.omp/test-budgets/approvals/`.
- Approval binding: `repository`, `pullNumber`, `reviewId`, `commit`, `policyDiffDigest`;
  accepted measurement reference `measurementApproval`.
- Other tier limits: gap: no enforced slow/quality/Docker/security wall caps in tier targets;
  affected local T2 invocation timeout `900 s`.

## Coverage

- Scope: fast tier `orchestrator/src`, `contract/src`, every existing
  `lab/stages/*/*/tool/src` beside a `stage.yaml`; fixture programs outside the denominator.
- Line floor: `99.43%`; branch floor: `98.72%`.
- Where defined: `orchestrator/pyproject.toml` section `[tool.raidar.coverage]`;
  integer-count enforcement `orchestrator/src/raidar/cli/quality.py`.
- Ratchet: upward after each accepted change; fast-only configured floors;
  diagnostic T1+T2 coverage: gap: no combined diagnostic command in current tier targets.
- Coverage artefacts: `var/tmp/tests/coverage/.coverage`, `var/tmp/tests/coverage/coverage.json`;
  relocation through `RAIDAR_VAR`.

## Guards

- Fast-tier process guard location: `orchestrator/tests/conftest.py` loads
  `tests.process_guard`; `orchestrator/tests/process_guard.py` audit hook covers full protocol.
- Denied events: `subprocess.Popen`, `os.system`, `os.posix_spawn`, `os.fork`, `os.forkpty`,
  `os.exec`, `os.spawn`, `pty.spawn`; exempt markers `slow`, `quality`, `docker`, `security`, `perf`.
- Fast-tier network guard location: gap: no network denial in the process-guard plugin.
- User-data guard: gap: no session-wide live-user-file mutation guard established.

## Environments

- Required environments: host Python `3.13.7`, uv `0.10.0`, orchestrator dev dependencies;
  CI Node `22` and root `npm ci --ignore-scripts`; review surface Chromium.
- Docker environment: trusted target `mbp`, ready data check and built Python pack image;
  target definitions `~/.config/raidar/targets.toml`, override `RAIDAR_TARGETS_FILE`.
- Require flag: `RAIDAR_REQUIRE_DOCKER=1` (Docker, security and perf fixture targets).
- Readiness probe: `tests.docker_trial_support.docker_target_ready()`;
  `ensure_target(docker_target())` plus Docker image inspection, timeout `30 s`.
- Readiness timing: first selected `docker` item's autouse fixture, once per session;
  missing environment fails with require flag, otherwise skips.
- Provisioning command: `env -u AGENT make docker-check TARGET=mbp`;
  image provisioning in Docker/security/perf-fixture targets: `raidar pack build python`.
- Review-surface readiness: `http://localhost:5950/api/runs`, timeout `30 s`;
  `REVIEW_SURFACE_PORT`, optional `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`.

## Verification surface

- Lever command: existing `env -u AGENT make review-surface-test`;
  gap: designated verification lever covering all app features.
- Feature map path: gap: executable feature-to-test map not established.
- Journey file pattern: existing `review-surface/tests/*.spec.ts`;
  gap: feature-map journey registration.
- Data directory argument: existing `REVIEW_FIXTURE_DIR`, default `var/tmp/review-fixture`;
  gap: isolated whole-surface data-root argument.
- Evidence directory argument: gap: explicit isolated evidence-directory argument;
  existing Playwright trace setting `retain-on-failure`.

## Ownership areas

- Map of T2 files to areas: `scripts/checks/repository-owners.json`, fields `files`,
  `source_directories`, `test_tiers`, `seam_owners`.
- Affected T2 test selection: `scripts/checks/run-affected-seams.py`, invoked by `gate-commit`;
  file-level `(slow or quality) and not docker`, union of boundary-matched `seam_owners`.
- Selection input: changed paths against `HEAD` plus untracked paths;
  unknown, unsafe, empty or stale ownership selects all discovered local T2 seams.
- gap: `seam_owners` has only five script keys; complete boundary-area mapping, including Docker
  and review-surface tests, is not established by this local slow/quality selector.

## Identity-bearing artefacts

- Record `.omp/identity.json`; check `env -u AGENT make identity-check`.
- Grading digest: `grading_execution_digest()` in `orchestrator/src/raidar/grading/records.py`;
  inventory `orchestrator/src/raidar/grading/identity.py`: grading import closure,
  shipped payload bytes, host Python and dependency versions.
- `primitive_code`: `orchestrator/src/raidar/subject/workflow/identity.py`;
  primitive/native-adapter import closures, payload Python sources, Harbor version;
  implicit `frame/1`, `install/1`, `runner/1`.
- Released stage bytes: `lab/stages/*/*/`, registered in `lab/releases.yaml`, `stage-tree/1`;
  identity check also records spec and registered scorer digests.
- Test-only preservation: grading scope, primitive closures and released package bytes unchanged;
  changed-identity procedure owner `.agents/skills/raidar-release/SKILL.md`.
- Package boundary check: `env -u AGENT make stage-check NAME=raidar/define TARGET=mbp`;
  package selection variable `NAME`; fixtures/replay offline in pack container, UID `1000`.

## Layout and naming

- Support directory: `orchestrator/tests/`, reusable modules `*_support.py`.
- Fixtures directory: `orchestrator/tests/fixtures/`; stage inputs `lab/stages/*/*/fixtures/`;
  review fixture builder `orchestrator/tests/review_fixtures/`.
- Test file patterns: `orchestrator/tests/test_*.py`, `contract/conformance/**/*.py`,
  `lab/stages/**/tests/**/*.py`, `review-surface/tests/*.spec.ts`.
- Placement inventory/check: `scripts/checks/repository-owners.json` field `test_tiers`,
  `scripts/checks/check-repository-paths.py`.

## Command notes

- Working directory: repository root; prefix `make`, `uv`, `pytest` calls with `env -u AGENT`.
- Host provisioning: `env -u AGENT uv sync --project orchestrator --all-extras --frozen`;
  CI adds `--python 3.13.7`.
- Hook install command:
  `env -u AGENT uv run --project orchestrator --extra dev pre-commit install --hook-type pre-commit`.
- Hook dispatch: `.omp/gate.json`, check-only `scripts/checks/run-repository-gate.py`.
- Report validity: schema `1`, current revision and invocation ID, complete successful inventory,
  every case passed, finite setup/call/teardown and wall durations, profile and actual machine facts.
- Budget checker exit codes: `1` validity/policy/approval failure or CI timing breach;
  `2` infrastructure/profile mismatch; local timing breaches advisory.
- Generated roots: `RAIDAR_VAR` default `var/`, `REPO_TMP_DIR` default `var/tmp/`,
  `REPO_CACHE_DIR` default `.cache/`; make exports repo-local temp/cache directories.
