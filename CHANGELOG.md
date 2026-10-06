# Changelog

All notable changes to this project are documented in this file.

## Unreleased - guardrails explore iteration 3 results (iteration 3b)

### Added

- `python-guardrails@4` (`raidar.components.builtins.guardrails_v4`, payload `payloads/python_guardrails_4`): `@3` byte for byte except the gate's scope and its consumers. A Python file is also an extensionless regular file whose first line is a `#!` line naming `python`, or the in-workspace target of a `*.py` symlink (once, at the target's path). Ruff gets the extensionless files explicitly beside `.`, findings reported through a link are deduplicated, and the tests check's change detection sees the same files. Fixes `smoke-03` r2, where an extensionless `forge` script with a `forge.py` symlink was checked as 0 files. Two canaries (`SCOPE.shebang-script`, `SCOPE.py-symlink`; `specs-4.json`, `# canary-symlink:` fixture header) and a scope mutant in `make guardrails-verify`.
- `continue-on-stall@2` (`raidar.components.builtins.stall_v2`, wrapper `raidar.components.continue_on_stall_v2`): `@1` plus problem-agnostic refusal patterns (first-person "can't/cannot/unable to ... without", "not enough/missing context or information", "... isn't defined/present", "doesn't define/specify") and at most one continuation per step: only the step's first session (no earlier record in the step's logs) is eligible; later sessions are recorded with `eligible: false` and `would_stall`.
- `transient-retry@1` (`raidar.components.builtins.retry`, wrapper `raidar.components.transient_retry`): a session wrapper that reruns a session Harbor classified as a transient provider error (`ApiOverloadedError`, `ApiConnectionClosedError`, `ApiResponseStalledError`, `ApiInternalServerError`, `ApiRateLimitError`) after 60 s and then 180 s, at most twice, with a fixed preface; other errors pass through unchanged. Every attempt's logs (`transient-retry/attempt_<n>`), error class, wait and usage are recorded and summed; an outer wrapper can read the usage through a fresh instance from the record.
- `gate-effort@5` (`raidar.scorers.gate_effort_v5`, store-bound): `@4`'s values with each gate run's evidence summary counting `TEST.*` in the `tests` family. Analysis, the manipulation check, the review surface and pre-fix grading accept `@5`.

### Changed

- `python__forge__guardrails` (explore iteration 3b, `rationale.md` §13, lock `8d8ad43b53d8`): both arms use `python-guardrails@4` and share `continue-on-stall@2` and `transient-retry@1`; scorer `gate-effort@5`. `rationale.md` §12.7 records the `smoke-03`/`smoke-03-r2` process check (no valid r2 trial; the C1 scope hole; per-session stall exposure; missed refusals).

### Removed

- The stale `isolation__env-6e9a50ac38-main-1` container (a 2026-10-02 pytest leftover of this worktree) from OrbStack.

## Unreleased - guardrails explore iteration 3

### Added

- `python-guardrails@3` (`raidar.components.builtins.guardrails_v3`, payload `payloads/python_guardrails_3`): `@1`'s gate, rules, pins and protected configs byte for byte, plus the `tests` check (`guardrails.tests_check`): the agent's own tests run like a repository's commit check, `unittest` always and pytest 9.1.1 for pytest-style tests, under coverage 7.16.2 (Python subprocesses included) with the project's interpreter, in a throwaway copy of the workspace.
  - Blocking findings: `TEST.missing` (a production Python change and no test ran; no Python change since the last commit is exempt), `TEST.failed`, and `TEST.coverage-minimum` (owned line coverage below 85%, the original rule catalogue's threshold, `f61d81b` `enaible-rules.json`). A root under a verifier path is a gate error (F4).
  - Installed once per trial at environment start (`raidar.components.provision_v2`, `RunScopedDockerEnvironment.add_post_start_hook`): uploaded payload, root-owned hash-pinned tree, an install record with the version, a smoke run as the agent user and a digest of the whole tree. Each step's check runs no tool; it compares the tree digest with the install's (`protected-tree-modified` flag after step 1).
- `guardrail-gate@3` (`raidar.components.guardrail_gate_v3`): `@2` plus a snapshot of `/app` before each fix session (the verifier's artifact mechanism and excludes), recorded with its tree digest in `pre_fix_snapshots`; its inner agent may be a session wrapper (`raidar.components.session_chain`).
- `continue-on-stall@1` (`raidar.components.builtins.stall`, wrapper `raidar.components.continue_on_stall`): a session that changed no workspace file and ended asking a question or for clarification gets one continuation session (no human is available; proceed on the specification and record assumptions in `ASSUMPTIONS.md`). Deterministic detection, recorded per session. A session wrapper: the execution layer nests it around the native agent, inside the condition's workflow wrapper (`apply_session_wrappers`), so it composes with `guardrail-gate@3` without a second workflow wrapper.
- Offline pre-fix grading (`raidar.verifier.prefix_grade`, store contract `raidar.verifier.prefix_store`): `raidar assess --rescore` grades every not-yet-graded pre-fix snapshot into `<store>/pre-fix-grades/` with the step's own bundle, sandbox and timeout when a declared scorer reads them; `make assess-regrade ... STORE=` regrades them beside the final snapshots.
- `gate-effort@4` (store-bound): `@3` plus the tests check (`first_run_tests_passed`, `first_run_tests_missing`, `first_run_coverage`, `tests_passed_at_step_end`, `tests_run_at_step_end`, `coverage_at_step_end` and their trajectory rates and means), `pre_fix_pass_rate`, `correctness_delta` and the trajectory `fix_round_correctness_delta` (not applicable to a shadow gate; an ungraded snapshot is unavailable and the Assessment provisional). `TEST.*` findings count in the `tests` family.
- `process-compliance@3`: `@2` plus `stalled`, `stall_resolved`, `stall_rate` and `stalled_steps` from the stall records.
- Analysis: `gate.*` outcomes are read per declared gate-effort version (`@4` adds the tests check and `gate.fix_round_correctness_delta`); `process.stall_rate` is a new outcome group (per-arm descriptives and paired effects) when the lock declares `process-compliance@3`.
- Canaries: `lint.per-file-ignores`, `explicit_package_bases` and `disable_error_code` now have their own (no pack config entry is uncovered); `@3` adds seven tests-check canaries. `make guardrails-verify` proves both payloads (`@1`, which `@2` installs, and `@3`) in the pack image, with a coverage-minimum mutant for `@3`.

### Changed

- `python__forge__guardrails` (explore iteration 3, `rationale.md` §12, lock `bbefb1c79a7c`): both arms share `continue-on-stall@1`; r1 and r2 use `python-guardrails@3` and `guardrail-gate@3`; the scorers are `process-compliance@3` and `gate-effort@4`. No job has run under this lock.
- The Quality run view reads `gate-effort@4` first.

## Unreleased - guardrails main-02 results

### Added

- `gate-effort@3` (`raidar.scorers.gate_effort_v3`): `@2` with one change. A step whose production and test files equal the previous snapshot's (or the starter's, for the first step) is `not_applicable` for `first_pass_clean` and is left out of `first_pass_clean_rate`. The commit gate commits with `--allow-empty`, so `@2` counted a no-edit step as clean. Analysis, the manipulation check and the review surface read `@3` before `@2`.

### Changed

- `python__forge__guardrails` declares `gate-effort@3`. The relock kept `lock_sha256` `28c51440f74a`. `main-02` was rescored and the replay was identical. `rationale.md` §11 records the `main-02` outcome; `docs/reboot/gate-packet.md` §15.7 has the signals and the next iteration.
- Analysis markdown:
  - The errata line says the primary is an errata outcome, with its raw counterpart beside it, whenever `analysis.primary_outcome` is an `*_errata` outcome.
  - The McNemar table has an `Outcome` column.
- `make guardrails-verify` and the canary-spec test derive the expected rule set from the shipped pack (`ruff.toml` entries, `mypy.ini` keys and the gate library's diagnostic codes) instead of a hard-coded 73. A pack rule without a canary now fails. Three config entries without their own canary are listed as known gaps (`lint.per-file-ignores`, `explicit_package_bases`, `disable_error_code`).

## Unreleased - guardrails explore iteration 2

### Added

- `guardrail-gate@2` (`raidar.components.builtins.gate_v2`, wrapper `raidar.components.guardrail_gate_v2`, run `raidar.components.gate_run_v2`): `@1`'s loop with:
  - `mode: shadow`: the same commit check, run once at each step's end against a git directory outside the workspace. It is recorded in full and never enforced: no refusal, no fix session, nothing written to `/app`. The record's `shadow_verdict` holds what the gate decided, and `gate_clean_at_step_end` is `None`.
  - Git evidence of every commit attempt: each gate run's `git` block (`HEAD` before and after, `git log --oneline`, `git status --porcelain`) and the step's `git_evidence` (hook and `git commit` exit codes, commits recorded), written outside the workspace.
- `python-guardrails@2` (`raidar.components.builtins.guardrails_v2`): the `@1` payload byte for byte. `with.workspace_configs: false` puts no config copy in the workspace (probe: none at step 1; integrity: protection flags only).
- `gate-effort@2` (`raidar.scorers.gate_effort_v2`): the same metrics; reads enforced records exactly as `@1`.
  - A shadow step measures `gate_runs`, `first_pass_clean`, `gate_error` and `first_run_blocking` (with families); every fix and enforcement outcome is `not_applicable`.
  - Analysis, the manipulation check and the review surface read `@2` before `@1`. The Quality run view labels a shadow gate and shows its first-run families.
- `analysis.primary_outcome` (experiment schema v2): the pre-registered primary outcome, validated against the trial outcome ids. It defaults to the progression's (`solve_rate`/`reached`).
  - The analysis report (`primary_source`), its markdown, `raidar analyze` and the review surface's Quality page label it as primary. An errata-applied primary is always reported with its raw counterpart beside it (`Comparison.primary_raw`).
  - It is analysis configuration, so `lock_sha256` does not move when it is set.
- Detached jobs: `make experiment-execute ... DETACH=1` (`raidar.execution.detach`) runs the job under a supervisor in its own session, kept awake with `caffeinate -i` where available, with its pid, log and exit record in `<job dir>/detach/`.
  - `make experiment-status JOB=<job dir>` reports `running`, `finished`, `failed` or `died`, the trials recorded of those selected, and the log tail.
  - `raidar run` accepts a job directory that holds only `detach/`.
  - AGENTS.md now requires long jobs to be launched this way and monitored to completion.

### Changed

- `python__forge__guardrails` (explore iteration 2, `rationale.md` §10, lock `28c51440f74a`, before `main-02`):
  - r1 gets `python-guardrails@2` without workspace copies and `guardrail-gate@2` in shadow mode, so its agent's experience is unchanged.
  - r2 moves to `python-guardrails@2` (with copies) and `guardrail-gate@2` (commit mode).
  - The experiment scores with `gate-effort@2` and pre-registers `analysis.primary_outcome: solve_rate_errata`.
  - Every other experiment was relocked for the spec change, and its `lock_sha256` is unchanged.
- The python-guardrails rule verification is Raidar's own. `make guardrails-verify` runs `run_canaries.py` over the 73 implemented rules' canaries in the pack image, plus the mutant-pack negative control. The planned pytest/coverage pack is now `python-guardrails@3`.

### Removed

- The vendored setup-repository-guardrails skill files under `orchestrator/tests/fixtures/python_guardrails/`: its validator (`validate_rule_coverage.py`), the Enaible rule catalog (`enaible-rules.json`), the coverage ledger and the not-applicable evidence. The skill was used once to set the initial verification; the files stay in git history (`f61d81b`).

## Unreleased - python-guardrails@1 and the guardrails experiment

### Added

- `python-guardrails@1` (`raidar.components.builtins.guardrails`, payload `raidar/components/payloads/python_guardrails_1/`): the Python guardrails pack. It installs `guardrail-check` once per trial, root-owned in `/opt/raidar-tools/python-guardrails` and linked to `/usr/local/bin/guardrail-check`:
  - Ruff 0.8.6 and mypy 2.3.1 from hash-pinned per-platform locks, in the pack's own venv;
  - protected configs and bespoke structure, source, fan-out and lockfile checks;
  - byte-identical `ruff.toml` and `mypy.ini` copies placed in the workspace.
- **Gate behaviour.** Check-only and cache-free. Exit 0 passes, 1 refuses, and 126 is a gate error, as is any crash. The JSON report (`python-guardrails.gate-report/1`) goes to `--json-out`, `--json-dir` or `$RAIDAR_GATE_REPORT_DIR`. It records whether the workspace copies still match the protected configs and is never printed.
- **Discovery probe.** Before every step's agent: the pinned versions at the link, and a gate smoke (a clean file passes; an unused import is refused with `F401`). At step 1 also: the protected bytes, a tool tree the agent cannot write, and the overlay.
- **Rule coverage.** All 92 Enaible rules are covered by the approved ledger (`orchestrator/tests/fixtures/python_guardrails/`):
  - 73 are implemented.
  - 16 are not applicable, with approval recorded.
  - 3 are not enforced: `PRETTIER.version` and `SECURITY.dependency-audit` are deferred; `TEST.coverage-minimum` with pytest is planned for `python-guardrails@2`.
- `make guardrails-verify` runs the bundled validator over every canary against the installed pack in the pack image, plus negative controls (OrbStack and PyPI; not in `make quality`).
- Integrity flags (`raidar.components.integrity`, `ProbeSummary.flags` on each step result) record agent tampering without failing the step. The python-guardrails flags cover:
  - edited workspace config copies (at step start or at any gate run);
  - protected configs or the tool tree changing after step 1;
  - refused suppression comments;
  - trace evidence of hook bypass attempts.
- Experiment `experiments/ab-test/python__forge__guardrails/`: r1 (shared framing) against r2 (`python-guardrails@1` plus `guardrail-gate@1` with the hook command `guardrail-check`, at most 3 fix rounds). It runs 4 attempts (explore) and declares `gate-effort@1` and the manipulation check `gate_clean_at_step_end` (`min_share` 0.8).
  - `rationale.md` pre-registers the primary `solve_rate_errata` (raw `solve_rate` beside it), the secondary and gate-effort outcomes, and the smoke rule for the mypy strict annotation codes.
  - Locked. Smoke `smoke-01` (2 r2 trials, 16 gated steps): rounds exhausted 0 of 16, so the pre-registered smoke rule keeps the mypy annotation codes at BLOCK; every step was refused once and cleared in one fix round (fix sessions $0.0853 of $0.159). Recorded in `rationale.md` §9.
  - Main job not yet run: `main-01` was terminated before any trial finished and contributes no data; the rerun (`main-02`, same lock) waits on removing its two idle containers.

### Removed

- Experiments `python__forge__ruff-lint` and `python__forge__ruff-lint-r2`; their jobs stay on disk. `lint-tool@1` and `@2` stay registered because those jobs' locks reference them. Gate packet §14.4 and §14.12 are now short historical notes.

### Documentation

- Gate packet §15: the guardrails iteration (pack and approvals, gate mechanism, smoke outcome and rule decision, main-run status, next-iteration changes including `python-guardrails@2` with pytest and coverage). `rationale.md` §9 states that the pre-registered primary is the `solve_rate_errata` Effects row, although the analysis labels raw `solve_rate` as primary.
- `python__forge__guided-r3/rationale.md`: its `process-compliance@1` references are marked historical; the experiment and its rescored jobs use `@2`.

## Unreleased - gate effort as a quality signal

### Added

- `gate-effort@1` (`raidar.scorers.gate_effort`): a deterministic scorer over each step's `guardrail-gate@1` record. Per checkpoint: gate runs, fix rounds, `first_pass_clean`, `gate_clean_at_step_end`, `unresolved` (fix rounds exhausted), `gate_error`, `rounds_to_clean` (clean steps only), blocking findings of the first run and over every run, in total and per rule family, and the fix sessions' input/output tokens, cost and wall time (the work session excluded), plus gate-run time. Per trajectory: rates over decided steps (numerator and denominator reported), mean rounds to clean and totals. Findings come from the run's `python-guardrails.gate-report/1` report when present, else from `guardrail-check`'s printed listing; family counts are `unavailable` when the listing was capped and no report names every finding. A trajectory with no gate record is `not_applicable` throughout, never 0.
- `analysis.manipulation_checks` (optional; omitted from a lock when unset, so existing locks are unchanged): pre-registers the manipulation checks and an optional `min_share`. `gate_clean_at_step_end` (from `gate-effort@1`) joins `tests_run_before_finish` and `plan_written`. Against a baseline without the gate, the check is the treatment's pooled checkpoint share against `min_share` (`basis: threshold`); with both arms gated it must also separate. Validation refuses an unknown check or one whose scorer is not declared.
- Analysis (report schema 3): with `gate-effort@1` declared, every arm carries descriptive `gate_effort` (n, mean, SD, interval and gaps by status per `gate.*` metric), every comparison carries paired `gate_effects` for the gate metrics both arms measured (else a `gate_note` saying why), and benchmark ranking rows carry `gate_means`. `gate.*` metrics are valid `min_effects` and `rank_by` ids.
- Review surface: the Quality run page and run detail's Quality panel show a gated trial's commit-gate effort: totals, a per-checkpoint table (how the gate ended, runs, blocking first/all, findings per family, fix cost and time) and, per step, each gate round with its families and log. An ungated trial shows no gate section.

### Changed

- `guardrail-gate@1` records each session's start, end and duration and each gate run's duration and report dir, and exports `RAIDAR_GATE_RUN` and `RAIDAR_GATE_REPORT_DIR` (`guardrail-gate/gate_run_<n>`, kept with its run) to the gate command. No lock used the component yet.
- The jobs whose experiment locks declare `process-compliance@2` were rescored under it (Assessments added only): pilot-v2 `forge-02`, `forge-03`, `oneshot-01`; guided-r3 `r3-01`, `r3-04`, `r3-05`, `r3-06`; `ruff-lint-r2-01`. `ruff-lint-02` was executed under lock `cc2a4b147487`, which declares `@1`, so it was left as is.

## Unreleased - Jev parked, process-compliance@2

### Breaking Changes

- Jev is parked (user decision 2026-10-04; requirements change log, F7): `code-quality.rubric-smells@3` and `process-compliance.residue@2` are `mode: "off"`. The shadow versions' audits and labels stay on file.
- `process-compliance@2` (`raidar.scorers.process_checks`) replaces `@1` in every experiment. It runs deterministic trace checks only: a check the trace cannot decide is `insufficient_evidence` with an `undetermined: ...` reason, never a value or a zero, and it has no `jev_ambiguous_compliance` metric and no Jev runtime. The five ab-test locks were relocked (assessment section only; `lock_sha256` unchanged); rescore their jobs under `@2`. `@1` stays registered, dormant, so its Assessments and locks stay readable.

### Changed

- Analysis reads `process-compliance@2` (or `@1` where a lock declares it) for the manipulation check, which counts deterministic checks only.
- The review surface's Quality run page shows a trial's past Jev decisions as collapsed, read-only history that is part of no score. The labelling panel and label API stay, dormant.
- `make help` marks the `jev-*` targets dormant. A new import-linter contract keeps `raidar.scorers.process_checks` free of `raidar.jev`.

## Unreleased - r3 and ruff-r2 results, Jev first-pass labels

### Documentation

- `docs/reboot/gate-packet.md` §14.8-§14.15: the spec-determinacy audit and forge errata (`9a6dec369dea`, 25 cases), verifier dependency parity (FCR-007), pilot v2 forge raw vs errata-applied, the r3 results (jobs `r3-01`/`r3-04`/`r3-05`/`r3-06`, lock `d4e2c9171a28`: primary and errata outcomes inconclusive, every pre-registered process criterion for FCR-003 to FCR-006 and FCR-008 met, E worse and R improved), the `lint-tool@2` ruff A/B (historical since 2026-10-04: §14.12 now points to the guardrails experiment), the Jev evaluation verdict, the first-pass labels and the decisions now open.
- `docs/reboot/jev/labels/firstpass-2026-10-04.{jsonl,md}`: 68 first-pass agent labels (51 `code-quality.rubric-smells`, 17 `process-compliance.residue`) with their method, stances and blindness record. They are a proposal and are not in the Jev label store.

## Unreleased - pilot refinement: Jev relabel, fault catalog and r3 proposal, ruff A/B

### Breaking Changes

- The smoke experiment is `experiments/smoke/python__circuit_eval-cp1__agent-health/` (was `...__harness-health`, the last pre-v2 word in an experiment id); relocked as `e6e0988b09f9`. `SMOKE_EXPERIMENT` defaults to it.
- `SECRETS=dotenv` fails when `orchestrator/.env` does not exist, instead of printing `.env` as the source and running without it. Under `SECRETS=auto` with neither Infisical nor `.env`, targets print `secrets: none` and go on with the shell environment and file-backed Codex auth; the credential preflight still refuses an arm that has no credential.
- A make variable under its pre-v2 name now fails when it comes from the environment too (`RUNS_DIR=x make help`), not only from the command line.
- `NAME1` applies to any experiment file below an `experiments/` directory, at any depth: it must be `experiments/<kind>/<id>/experiment.yaml`. Copies outside every `experiments/` directory are still checked by `NAME2` only.

### Features

- Spec-determinacy audit in `make problem-validate` (`raidar.problems.determinacy`, `variants`, `audit`; report section `spec_determinacy`, `AUDIT_ONLY=1` for the audit alone): static rules classify every error-text and output-byte assertion against the origin checkpoint's and earlier specifications, and reference solutions are graded with spec-conformant variants (neutral error text, re-serialised JSON). A case is flagged when it breaks on a variant and its assertion is undetermined. The audit never changes the validation verdict.
- Versioned problem errata (`problems/registry/<dataset>/<problem>/<version>.errata.yaml`, `raidar.problems.errata`, `make problem-errata-draft`): `exclude-from-solved` / `informational` cases with reason and evidence. The lock pins the errata's sha256 in the problem reference metadata (execution identity); the plan refuses an errata changed since the lock; the verifier records the errata-applied verdict beside the raw grade (rewards unchanged); `correctness@1` adds `solved_errata`, `solved_checkpoints_errata` and `reached_errata` (config `errata` comes from the lock only; an explicit one is refused) and the analysis reports `solve_rate_errata`, `reached_errata` and `final_solved_errata` beside the raw outcomes. `scb/forge@38d627ecf668` has an errata from its audit.
- `raidar migrate-job` moves a schema v2 job onto a relock that differs only in problem errata (errata adoption): the executed lock is kept in `lock-history/`, `job.json` records `lock_migrations`.
- Forge experiments relocked for the errata (execution changes by `metadata.errata` only; every lock's assessment and toolchain sections also moved with the new `correctness@1` metrics): `python__forge__guided-pilot` `015c57e4f59c` -> `15c1eb4e3a1b` (pilot v2 jobs `forge-02`/`forge-03` migrated by errata adoption; their raw analysis reproduces `gate-evidence/schema-v2/analysis/forge` exactly), `python__forge__guided-r3` `12f2b2fb05fc` -> `d4e2c9171a28`, `python__forge__ruff-lint` `cc2a4b147487` -> `f870d2c6ff73` (job `ruff-lint-02` not migrated; it stays analysable under its own `job.lock.json`), `python__forge__ruff-lint-r2` `5d4b2f59b74a` -> `bb4b7edf5996`. The circuit_eval locks keep their `lock_sha256` (the audit flagged nothing there, so it has no errata).
- FCR-007: the grader image carries pinned offline copies of the packages a problem's SCB configuration names (`test_dependencies`; PyYAML 6.0.3), and a snapshot that declares one of them (`pyproject.toml` or `requirements.txt`) is graded with it on the program's `PYTHONPATH`; declared packages Raidar cannot provide are recorded as `unprovided` in the step record (`submission_dependencies`). Grading stays `--network none`.

- `lint-tool@1`: a hash-pinned Ruff (0.8.6) installed once per trial, as root, in the agent container under `/opt/raidar-tools/<slug>/` and linked to `/usr/local/bin/ruff`, outside the agent image (the pack audit's "no scorer tools in the agent image" holds). Its probe checks, before every step, the version at the provisioned path and, at step 1, that Ruff resolves the workspace `ruff.toml`. Components opt into this through `raidar.components.provision` (`install` once, `check` per step, one evidence record per step).
- `lint-tool@2` (`raidar/components/builtins/tools_v2.py`; `lint-tool@1` is unchanged, so its component digest does not move): @1's install plus a root-owned `.pth` in each image interpreter's site-packages pointing at the install, so the pinned Ruff runs as `ruff`, `python -m ruff`, `python3 -m ruff` and `uv run ruff` (through `PATH`), still outside the agent image. Its check runs every invocation's `--version` before each step's agent (`uv run` with `--no-sync` and a throwaway `UV_PROJECT_ENVIRONMENT`, so nothing is written to the workspace) and records whether the workspace is a uv project and whether a project `.venv` has its own Ruff. The probe `lint-tool-invocable` enforces, at step 1, that `ruff` on `PATH` is the provisioned link, that every invocation answers the pinned version and the configured `ruff.toml`; later steps enforce the install only and report which invocations still run the pin. `python -m ruff` inside a virtual environment the agent creates is not supported.
- Experiment `python__forge__ruff-lint-r2` (historical): removed 2026-10-04 and replaced by `experiments/ab-test/python__forge__guardrails/`. Its job `ruff-lint-r2-01` stays on disk (gate packet §14.12).
- Jev calibration ledger (`raidar.jev.ledger`): `make jev-supersede-labels` takes labels out of calibration without deleting them (append-only), `make jev-record-split` records a calibration/evaluation split before labelling (`jev-promote` defaults to the latest), and `make jev-export-blind ... REQUEST_IDS=` exports chosen decisions. Promotion, calibration, blind export, label export and the Quality projection read labels through `current_labels`, so superseded labels never count.
- Agent skills `fault-catalog` (analyse a job into conceptual fault categories and propose component changes as the next revision) and `experiment-observations` (report what a completed experiment observed, reconciling fault evidence through `fault-catalog`) under `.agents/skills/`. They are first versions and need refining.
- Experiment `python__forge__ruff-lint` (historical): removed 2026-10-04 and replaced by `experiments/ab-test/python__forge__guardrails/`. Its job `ruff-lint-02` stays on disk (gate packet §14.4).
- The proposed `experiments/ab-test/python__forge__guided-r3/` (pilot guided condition vs a candidate built from the pilot v2 fault catalog; locked `12f2b2fb05fc`, not executed; `rationale.md` beside it).

### Fixes

- Step evidence under concurrent trials. Between steps Harbor moves the mounted `/logs/agent` into `steps/<step>/agent`; on an OrbStack bind mount the container could keep a cached entry for a moved name, so the next step's `raidar-reset.json` was written into the previous step's archived copy (overwriting it) and was missing from its own step. `RunScopedDockerEnvironment` now removes, by name and as root, every archived top-level log name the host mount no longer holds before each step's `setup.sh` (`rm -f` for files, `rmdir` for directories, so archived evidence is never touched). No component, setup script or digest changed. Three concurrent runs of the real-trial step-reset and lint-tool tests went from 3/3 failing to 6/6 passing (two rounds); the isolation test now also asserts step 1's report predates step 2's. An audit of the recorded jobs (pilot v2, `ruff-lint-02`; 391 steps) found no overwritten or missing report outside one aborted job.
- Review-surface wording follows the vocabulary: "Trial not found", "Failed to load trial(s)", "No projected trial with id ...", "Loading trial…", "trial time", "trial json", "Synthetic fixture trial", "No scored trials", "open trial review", and the wireframes' "Trials" label and help texts. Routes (`/runs/:runId`) and `data-run-id` stay as the internal contract.
- Docstrings in `scorers/rescore.py` and `scorers/trial_inputs.py` say trial (scorer identities checked unchanged).

### Documentation

- README and AGENTS.md name the remaining frozen pre-v2 words and why: the observation subject `trajectory`, the evidence input `oracle.cases` and the `@1` scorer descriptions (hashed into Assessment keys), and the problem store's `oracle/` bundle directory (inside each problem's pinned digest).
- README: the repository ships no `orchestrator/.env`; the blind-labelling protocol gives a labeller the export alone, never beside a round's selection files (which carry Jev's scores).
- `docs/reboot/gate-packet.md` §14 (pilot refinement), `docs/reboot/pilot-v2-fault-catalog.md` and `docs/reboot/jev-spot-check.md`.

## Unreleased - schema v2: Harbor vocabulary, experiment kinds, runtime-compatibility gate

### Breaking Changes

- Harbor's vocabulary replaces Raidar's wherever Harbor has a term (requirements change log 2026-10-02). Old to new:

  | Was | Now |
  |---|---|
  | run, `runs/`, `run.json`, `run.lock.json`, `RUN=`, `RUNS=`, `RUN_ID=`, `RUNS_DIR=`, `--run-id`, `--runs-dir`, `--allow-dev-runs` | job, `jobs/`, `job.json`, `job.lock.json`, `JOB=`, `JOBS=`, `JOB_ID=`, `JOBS_DIR=`, `--job-id`, `--jobs-dir`, `--allow-dev-jobs` |
  | trajectory, `TrajectoryRecord`, `trajectories/<trial>.json` (`raidar-trajectory`) | trial, `TrialRecord`, `trial-records/<trial>.json` (`raidar-trial`, schema 2) |
  | `CheckpointAttempt` (`raidar-checkpoint-attempt`), record field `attempts` | `StepResult` (`raidar-step-result`), record field `step_results` |
  | blocks, `analysis.blocks`, `--blocks`/`--block`, `BLOCK=`, `SMOKE_BLOCKS=` | attempts, `analysis.attempts`, `--attempts`/`--attempt`, `ATTEMPT=`, `SMOKE_ATTEMPTS=` |
  | oracle (hidden grader), `raidar.oracle`, `RaidarOracleVerifier`, `OracleBundle`, `ORACLE_BUNDLE=` | verifier, `raidar.verifier`, `RaidarVerifier`, `VerifierBundle`, `VERIFIER_BUNDLE=` |
  | harness (`codex-cli`), `agents[].harness`, factor `agent.harness`, `raidar harness ...`, `make harness-list/harness-validate`, `HARNESS=`, `HARNESS_VERSION=` | agent name (Harbor `AgentName`: `codex`, `claude-code`), `agents[].name`, factor `agent.name`, `raidar agent ...`, `make agent-list/agent-validate`, `AGENT=`, `AGENT_VERSION=` |
  | `runtime: {language, environment, compose_init}` | `runtime: <pack id>` and `environment: {type, compose_init}` |
  | headline policy, `assessment.headline_policy`, `ScorePolicy`, `raidar.spec.policies`, reference kind `policy` | headline metric, `assessment.metric`, `Metric`, `raidar.spec.metrics`, reference kind `metric` (ids `scb-canonical@1/@2` unchanged) |
  | validity checks `oracle_verdicts`, `oracle_infra`, `no_oracle_leak`, `harness_version`; phases `oracle_leak_check`, `oracle_run` | `verifier_verdicts`, `verifier_infra`, `no_verifier_leak`, `agent_version`; `verifier_leak_check`, `verifier_run` |
  | `REVIEW_RUNS_ROOT=`, `QUALITY_RUNS=`, `--runs-root` | `REVIEW_JOBS_ROOT=`, `QUALITY_JOBS=`, `--jobs-root` |

  A make variable under its old name fails with its new name. Kept: *problem*, *checkpoint*, *experiment*, *arm*, *condition*, *component*, *factor*, *lock*, *verdict*; Harbor's `oracle` agent (`AGENT_OVERRIDE=oracle`); and, byte for byte, the frozen artifacts (released components' fingerprint sources, the grader plugin, the Python pack image recipe and the verifier evidence names inside trial directories).
- `experiment.yaml` is `schema_version: 2`: a required `kind` (`ab-test`, `benchmark`, `smoke`; `research-loop` reserved), a required `runtime`, and the renamed fields above. Schema v1 files are read-only: `raidar validate` accepts them, `lock`/`plan`/`run` refuse them; `raidar upgrade-spec` (`make experiment-upgrade`) converts one.
- Experiments live at `experiments/<kind>/<id>/experiment.yaml` with ids `<runtime>__<subject>__<intent>` (`NAME1`, `NAME2`). `examples/` is gone: `examples/pilot/experiment.yaml` is `experiments/ab-test/python__forge__guided-pilot/`, `examples/pilot/oneshot.experiment.yaml` is `experiments/ab-test/python__circuit_eval-cp1__guided-pilot/`, `examples/smoke/experiment.yaml` is `experiments/smoke/python__circuit_eval-cp1__agent-health/` and `examples/smoke/agents.experiment.yaml` is the benchmark `experiments/benchmark/python__circuit_eval-cp1__agents/`. `experiments/` is tracked; `experiments/research_loops/` and `experiments/operational_smoke/` stay ignored.
- Lock version 3: the execution section records `kind`, `runtime`, `environment` and agent names (`versions.agents`); the assessment section records `metric`. Every experiment was relocked. `raidar migrate-run-lock` is replaced by `raidar migrate-job`.
- The analysis report is `schema_version` 2: `runs` -> `jobs`, `headline_policy` -> `metric`, `replaced_trajectories` -> `replaced_trials`, `dev_run` -> `dev_job`, plus `experiment_kind`, `citable` and `ranking`.
- The Quality projection's run summaries use `job`, `attempt` and `trial_record`; its experiments list `jobs`.
- Review-surface labels follow the vocabulary: Trials (navigation, trial detail, "Annotate trial", "trial id"), attempts, jobs and "Headline metrics differ in this comparison". The flow, signals and persona questions are unchanged and still asserted by the Playwright specs.

### Features

- Experiment kinds. An `ab-test` compares conditions; a `benchmark` compares agents under one condition, keeps the paired verdicts against its reference agent (the baseline arm) and adds a ranking view (`analysis.rank_by`, default the primary outcome): per agent the mean with its 95% interval, pass@k for every k up to the attempts, and mean cost, tokens and agent seconds. A `smoke` report is stamped not citable. Kind rules (`KIND0`-`KIND3`) forbid only contradictions; a benchmark below 5 attempts validates with a warning.
- Runtime-compatibility gate in `build_lock` (`RT0`-`RT6`), reported as field-level errors all at once and not bypassable through `--set`, local `path:` problems (which now declare `[metadata] raidar_runtime`), one-shot refs or the smoke targets: unvalidated binding, binding runtime, stale binding contract, component runtimes and agent support, scorer and headline-metric runtimes, agent version against the pack pin, and a missing pack. `plan`/`run` load the pack of the lock's runtime and refuse pack drift; `assess --rescore/--regrade` check scorers and `--bundle` against the job's lock (`AS1`). Resolvers report these facts in `ResolvedReference.binding`, which the lock never records, so the gate changed no execution digest.
- Runtime packs are loaded through `raidar.packs.load_runtime_pack(runtime)`; `problems/sources.yaml` keys bind a runtime (`runtime:`, default `python`), and the importer refuses another pack.
- `raidar migrate-job` / `make experiment-migrate-job JOB= LOCK= [EXECUTED_LOCK=]` moves a pre-v2 job onto its experiment's v2 lock after proving the translated execution section equal, keeping the legacy files in `legacy-v1/` with their sha256 in `job.json`. The pilot v2 jobs forge-02, forge-03 and oneshot-01 were migrated with it.

## Unreleased - reboot Phase 1 cutover (WS-L)

### Breaking Changes

- The scenario epoch's authoring and execution paths are removed. Raidar now has one authoring convention: `experiment.yaml` (problems, agents, conditions built from components, arms, scorers, analysis), locked by `raidar lock` and run by `raidar run`.
- Removed CLI commands: `raidar scenario ...`, `raidar info`, `raidar matrix ...`, `raidar experiment run`, `raidar experiments list|prune`, and the `raidar run --scenario` compatibility branch.
- Removed make targets, with no refusing stubs left behind. Old to new:

  | Removed target | Replacement |
  |---|---|
  | `scenario-list` | `make problem-list` |
  | `scenario-info` | `make problem-info PROBLEM=<ref>`, and `make experiment-plan EXPERIMENT=<path>` for an experiment's resolved contract |
  | `scenario-validate` | `make experiment-validate EXPERIMENT=<path>` and `make problem-validate PROBLEM=<ref>` |
  | `scenario-init` | `make experiment-init EXPERIMENT=<dir>/experiment.yaml` |
  | `scenario-clone-revision` | no target: add a condition and an arm inside the existing `experiment.yaml` |
  | `experiment-run`, `matrix-run` | `make experiment-execute EXPERIMENT=<path>` |
  | `runtime-stack-scenario-smoke` | `make pack-audit` and `make component-probe-trials` |
  | `experiments-list`, `experiments-prune` | none: `runs/` is a plain directory; `make harbor-cleanup` removes leftover trial environments |
  | `smoke-matrix SMOKE_MATRIX_SELECTOR=` | `make smoke-agents SMOKE_ARM=codex\|claude` (the Gemini selector is gone; the Python pack bakes in only codex-cli and claude-code) |
  | `benchmark-fixture-synthetic` | none: the scenario-epoch review read path is removed (see below); `make review-fixture-synthetic` writes the experiment fixture |

- Review surface variables and flags: `REVIEW_EXPERIMENTS_ROOT`, `python -m raidar.review_projection --experiments-root` and the fixture's scenario-epoch experiments are removed with the scenario-epoch read path (next entry). `make review-surface-test` always runs on the synthetic review fixture, and it takes one `REVIEW_STORE` for every `REVIEW_RUNS_ROOT` or one per root, failing on any other count instead of leaving given runs out of the Quality data.
- The scenario-epoch review read path is removed: `raidar.review_projection.scenario_epoch` (with its frozen fixture), `REVIEW_LEGACY_ROOT`, `python -m raidar.review_projection --legacy-root`, `REVIEW_FIXTURE` (`legacy` mode) and the scenario taxonomy in `raidar.schemas.review` are gone. `make review-surface-data` now requires `REVIEW_RUNS_ROOT`.
- The review projection is `schema_version=2`: `scenario` -> `problem`, `revision` -> `arm` (the arm's condition label), `scenario_meta` -> `problem_meta` (display name, description, difficulty, languages, checkpoints, timeout), `revision_diffs` -> `arm_diffs` (`problem`, `from_arm`, `to_arm`, `files.condition`), `parent_revision` -> `parent_arm`, and the `initial-revision` change category -> `baseline`. The surface's TypeScript types, data build, pages, `/wireframe` pages and wireframe artifacts use the new names; "Revision movement" reads "Arm movement".
- The review fixture's headline-policy mismatch now compares two experiment locks of `synthetic/policy-mix@1` (`scb-canonical-oneshot@1` and `scb-canonical@1`), and its `review-only` run root (projected for review, left out of the suite's Quality data) shows a run and a family without Quality data.
- The review surface's primary pages use experiment-model wording: "Problem families" anchors, the "problem / agent spec / id" runs filter, problem-scoped finding hints and failure patterns, and a "contract" tab for the scenario-epoch contract diff. The `schema_version=1` wire keys are unchanged; the Playwright specs assert the same signals under the new labels.
- Retired directories: `scenarios/`, `matrices/`, `environments/` and the Phase 0 `spikes/`, plus `raidar-revision-cleanup.md`. Nothing referenced them, so they are removed from the tree; the last tree containing them is tagged `archive/scenario-epoch`. The two spike artifacts the ADR and the N1 budgets cite are kept as `docs/reboot/evidence/a-prime-RESULTS.md` and `docs/reboot/evidence/overhead-baseline-summary.json`. The oracle-validation reproduction steps now use `make problem-validate`. The Node/Bun environment becomes a runtime pack in Phase 2.
- Removed modules: the scenario runtime pipeline (`raidar.runtime`: task bundles, task images, workspace phases, scorecards), `raidar.application`, the scenario and scorecard schemas, the scenario-attached weighted scorers (code-task, design-to-code, plan-to-code, test-generation, requirements, resource-efficiency, and the Codex-in-Docker `llm-as-judge` default), the matrix schema, starters, the gate watcher, the evidence-runner asset, and the repository-local Harbor CLI agents and routing.
- `raidar harbor cleanup` now removes the trial environments (run-scoped Compose projects) a stopped `raidar run` leaves behind: all of a named run's (`--run`), or every one with nothing running. It no longer matches scenario-era container names.

- `make perf-check` / `raidar perf check` is report-only on real experiment runs (user decision on N1 overhead budgets, 2026-10-01): it reports every phase against its accepted budget, prints breaches (over budget, `insufficient`, `missing`) as warnings and exits 0. Malformed or empty input still exits non-zero. `PERF_STRICT=1` (`--strict`) restores the failing check. The enforced overhead check is now `make perf-gate`.

### Features

- `make perf-gate [PERF_GATE_DIR=.tmp/perf-gate]`: the enforced N1 overhead gate. It runs the no-LLM fixture (`make perf-fixture`) at concurrency 1 and then 3, each checked with `--strict` against the accepted budgets (unchanged), and fails on any breach. It needs Docker (OrbStack), so it is a release/gate check and is not part of `make quality`. Each concurrency leaves its strict JSON report at `<PERF_GATE_DIR>/concurrency-<n>/perf-check.json`.
- `raidar perf check` records each run's contention context: the configured trial concurrency (`run.json`), the peak number of trials running at once in the checked runs, the peak including other Raidar runs under the same runs root that overlapped in time (listed by path), and how many trials had no start/finish timestamps. Docker work outside Raidar runs is not recorded by runs, and the report says it is not detected. For each p95 budget that is over, the report re-judges it separately for trials that ran with at most 3 and with 4 or more trials running at once.
- `raidar problem list` / `make problem-list` lists every registered problem with its mode, checkpoints, validation verdict (or why it has no usable one) and import state. `raidar problem info` / `make problem-info PROBLEM=` shows one problem's steps, source pin, pack binding, manifest digest and validation report.
- `raidar init` / `make experiment-init EXPERIMENT= [PROBLEM=] [EXPERIMENT_ID=]` writes a two-arm starter experiment (baseline vs `rules-file@1`, agent version from the pack pin) and a stub rules file. It validates as written and refuses an existing path.
- Experiment-path smoke targets: `make orchestrator-smoke`, `make agent-smoke` and `make smoke-agents` run `examples/smoke/` through lock and Harbor. `smoke-agents` has one arm per pack harness (`codex`, `claude`).
- `make experiment-assess RUN= STORE= SCORERS=` rescores a run's stored trials into an Assessment store (`raidar assess --rescore`).
- `make harbor-cleanup RUN= DRY_RUN=`.
- Secrets come from Infisical (project `mac-os-cf`, environment `dev`, bound by `orchestrator/.infisical.json`). Targets that run agents or Jev wrap `raidar` in `infisical run --env=dev`. `SECRETS=auto` (default) uses Infisical when the CLI is installed and logged in and otherwise falls back to `orchestrator/.env`; `SECRETS=infisical` fails instead of falling back; `SECRETS=dotenv` skips Infisical. Only the source is printed, never a value.
- `raidar run` runs a presence-only credential preflight for every selected arm before any trial starts, and exits 2 naming the missing variables. It does not check that an account is funded.
- `make agent-smoke HARNESS=claude-code` defaults to `claude-haiku-4-5` with no reasoning effort, and the `smoke-agents` claude arm uses the same model. An empty override (`REASONING_EFFORT=`) unsets the key.
- Jev calibration: labels record a `human` or `model` reviewer (and the model id) through import, export, readiness and the review-surface labels API. `make jev-export-blind` / `make jev-import-blind` run blind labelling. `make jev-promote` fits and freezes a threshold on a calibration/evaluation split and records metrics, a regression corpus and reviewers; `make jev-verify-promotion` re-derives them. Rubric question set `scb-rubric@2` asks codebase-scope criteria with an indexed focus.
- Scoring alignment: correctness C is the SCB fork's mean per-checkpoint case pass rate everywhere S is computed, and analysis reports C as `correctness`. New headline policies `scb-canonical@2` and `scb-canonical-oneshot@2` score S = 0 when C = 0; the pilot experiments declare `@2`.

### Bug Fixes

- `make jev-promote` no longer writes `mode: enforce` while the scorer minor bump the promotion names is still missing. Writing it then left a repo policy that `make jev-verify-promotion` rejects. The command now exits 1 with `"blocked_on_scorer_bump": true`; `ALLOW_SCORER_BUMP=1` (`--allow-pending-scorer-bump`) writes anyway, for the "promote, then bump the scorer" sequence.

### Tests

- An end-to-end analysis test checks the `scb-canonical@2` zero-correctness rule through rescore and `raidar analyze`: C = 0 trajectories with a quality gap score S = 0 and are paired, C > 0 ones stay unassessed, and `@1` leaves all of them unassessed. No pilot trajectory has C = 0, so this path was otherwise covered only by unit tests.

### Documentation

- Gate packet §12 closes Phase 1: Infisical secrets, Claude Haiku readiness (blocked on Anthropic account credit), live shadow Jev (15,516 provider calls, about $2.33), labelling provenance and its limits, the promotion outcome (not promoted), scoring alignment and cleanup. §6 and §11.15 no longer say live Jev is blocked, and §11.17 states what the labelling artifacts can and cannot prove.

### Chores

- Generated coverage data (`.coverage`, `orchestrator/.coverage`) is no longer tracked and is ignored.
- The component proof fixtures (`examples/pilot/components/probe/`) move to `orchestrator/tests/fixtures/components/probe/`; the component tests compile them from there.
- The quality gates' generated-artifact guard covers `runs/` as well as `experiments/`, and the Python check scripts exclude `runs/`, `problems/store/` and `problems/cache/` instead of `scenarios/`.
- CI no longer installs Bun; only the removed scenario starters used it.
- `AGENTS.md` is rewritten for the experiment, problem, component and scorer model (workflow rules, known experiment configs, smoke testing). `CODEOWNERS` drops the nonexistent `/.configs/` and `/benchmark-view/` and names the current top-level surfaces.
- The README, `make help`, the review-surface README and the import contracts describe the experiment, problem, component and scorer model.

## 0.14.0 - 2026-07-03

### Chores

- chore: capture orchestration and wireframe updates

- wireframe: add runs review surface
- wireframe: simplify expanded run summary
- wireframe: tint expanded row panel
- wireframe: remove run criteria label
- wireframe: align run criteria headers
- wireframe: remove redundant criterion score pills
- wireframe: add run criterion matrix
- wireframe: clarify expanded row evidence
- wireframe: left align movement value cells
- wireframe: remove neutral movement arrows
- wireframe: simplify neutral movement values
- wireframe: derive output tokens from runs
- wireframe: left align token movement headers
- wireframe: split token spend movement
- wireframe: show runtime before movement
- wireframe: remove duplicate native tooltips
- wireframe: default trends to spend
- wireframe: add revision chart filter
- wireframe: inline trends subtitle
- wireframe: inline section subtitles
- wireframe: align rotated pattern header anchors
- wireframe: bottom align pattern headers
- wireframe: hide pattern header question marks
- wireframe: clarify patterns agent spec count
- wireframe: simplify patterns revision count label
- wireframe: explain pattern criterion sources
- wireframe: clarify trends scenario scope
- wireframe: show latest pattern revisions first
- wireframe: scope revision filter to movement
- wireframe: normalize section vertical padding
- wireframe: align trends container padding
- wireframe: reduce trends container padding
- wireframe: restore trends container border
- wireframe: wrap charts in trends section
- wireframe: align patterns subtitle style
- wireframe: explain runtime and spend columns
- wireframe: remove redundant pattern evidence row
- wireframe: show visible pattern runs
- wireframe: simplify checks chip
- wireframe: count visible pattern revisions
- wireframe: refine patterns subtitle
- wireframe: clarify revision sort tooltip
- wireframe: explain revision row sorting
- wireframe: add revision row sorting
- wireframe: rank revision rows by scenario
- wireframe: remove run column from revisions
- wireframe: add movement arrows to revision rows
- wireframe: move revision selector to scenario header
- wireframe: merge revision movement and rows
- wireframe: center evidence map labels
- wireframe: remove evidence map legend
- wireframe: mark scenario titles with mint dot
- wireframe: distinguish scenario title color
- wireframe: add recovery color for patterns
- wireframe: align section title styling
- wireframe: align cleared pattern color
- wireframe: title case evidence map label
- wireframe: label criterion checks
- wireframe: clarify patterns map labels
- wireframe: space patterns summary controls
- wireframe: widen patterns subtitle
- wireframe: inline patterns summary counts
- wireframe: simplify patterns map panel
- wireframe: tune patterns map headers
- wireframe: align patterns map labels
- wireframe: add patterns evidence map
- wireframe: add patterns panel design artifacts
- wireframe: clarify patterns and reorder family controls
- wireframe: add recurring patterns panel
- wireframe: split chart and movement evenly
- wireframe: align movement beside charts
- wireframe: link diff pills to tabs
- wireframe: tint diff pills cyan
- wireframe: simplify revision diff pills
- wireframe: show all selected movement pairs
- wireframe: rank revision movement rows
- wireframe: reuse agent spec labels
- wireframe: refresh revision movement table
- wireframe: match family body vertical padding
- wireframe: align scenario outer padding
- wireframe: tighten spend chart padding
- wireframe: balance spend chart layout
- wireframe: align chart legend dots
- wireframe: match chart subtitle typography
- wireframe: show chart tab button states
- wireframe: match runtime chart edge padding
- wireframe: balance runtime plot edges
- wireframe: tighten runtime chart right edge
- wireframe: balance runtime chart padding
- wireframe: correct chart left alignment
- wireframe: align chart header rows
- wireframe: label spend chart outcome axis
- wireframe: align spend chart subtitle
- wireframe: rename token chart tab
- wireframe: use outcome rings on token chart
- wireframe: populate token spend chart data
- wireframe: extract token bar chart component
- wireframe: revise token spend chart axis
- wireframe: add token chart tab

## 0.14.0 - 2026-06-14

### Features

- feat(synthetic): add an unscored run to the bugfix-low spec
- feat(synthetic): populate scorer metric_contributions
- feat(synthetic): multi-spec, multi-revision fixture at volume
- feat: plain-language nav copy and Runs landing guidance
- feat: refine issues triage, revision-movement readability
- feat: semantic verdict layer and progressive-disclosure review UX
- feat: link runs back to their experiment review
- feat: efficiency contrast in diagnosis and benchmark-row messaging
- feat: replace experiments metric table with scenario board and experiment review
- feat: add synthetic visual-ui benchmark fixture with screenshot evidence
- feat: family quick-nav, token delta sign fix, and docs for ported comparisons
- feat: port benchmark-view's comparison value into review-surface
- feat: keyboard navigation, run dates, and persona doc updates
- feat: scale span-tree navigation for debuggers
- feat: give benchmark reviewers scenario context and ranking deltas
- feat: add scorecard breakdown and gate chips to run review
- feat: add persona-aware review-surface app over Raidar benchmark evidence
- feat: scaffold review-surface data projection and API server
- feat: surface review findings in benchmark-view with synthetic fixtures
- feat: project run and experiment evidence into deterministic review findings
- feat: add bugfix-ledger-balance scenario exercising bugfix scorer
- feat: ingest scenario-declared retained evidence into scorer context
- feat: activate concrete scorer definitions

### Bug Fixes

- fix: persist execution trace events on run records
- fix: improve task image caching and coverage gates

### Documentation

- docs: update runLabel example to current synthetic id format
- docs: record review-surface delivery against charter backlog items
- docs: record charter-review execution results and follow-on backlog
- docs: record charter-review implementation goal and phase-1 plan

### Refactoring

- refactor: ground repeatability tier in Raidar's variance threshold; doc redesign
- refactor: drop unused IdChip from Verdict
- refactor: deprecate benchmark-view in favor of review-surface
- refactor: harden review-surface UX from browser persona testing

### Tests

- test(review-surface): committed Playwright e2e functional suite (S1)

### Chores

- chore(docs): remove superseded review-surface and migration todo specs
- chore: add charter review goal assets

- revert: roll back scenario-board redesign and visual fixture

## [0.13.0] - 2026-06-09

- feat: migrate scorer definitions to coded scorers
- feat: move matrix execution to stored configs
- refactor: realize scorer metric contracts
- refactor: make scorers canonical
- refactor: organize scorer implementations
- docs: organize todo references
- chore: add scorer platform goal
- chore: strengthen python quality gates
- chore: ignore generated runtime outputs

## [0.12.1] - 2026-05-20

- refactor: introduce scorer-based metrics

## [0.12.1] - 2026-05-19

- fix: stabilize agent smoke warm prep
- refactor: enforce runtime fan-out boundaries
- refactor: normalize duplicated orchestrator paths
- refactor: clean up orchestrator dispatch and release bumping
- refactor: implement code quality cleanup plan
- refactor: clean up orchestrator execution contracts
- chore: commit outstanding workspace updates

## [0.12.0] - 2026-05-14

- feat: improve benchmark web view ux
- feat: further improvements to benchmark web view
- feat: add benchmark scenario iteration tooling
- refactor: consolidate harbor cli smoke adapters
- refactor: improve benchmark dashboard filters
- chore: ignore infisical state file
- chore: remove stale benchmark review artifacts

## [0.11.1] - 2026-04-21

- docs: add harness optimization notes
- chore: restore coverage database

## [0.11.1] - 2026-04-21

- fix(runner): retry transient workspace prune failures
- refactor(models): separate provider and reasoning config

## [0.11.0] - 2026-04-20

- feat(runner): harden harbor fast-mode execution

## [0.10.0] - 2026-03-28

- feat(codex): add oauth onboarding and smoke auth defaults

## [0.9.3] - 2026-03-28

- fix(scenarios): keep homepage iterations in revision roots
- docs(agents): remove temporary sync notes
- chore(main): reconcile non-autoresearch local work

## [0.9.2] - 2026-03-28

- fix(ci): remove deleted auto_researcher sync step
- fix: route public smoke targets through fast agents
- fix(orchestrator): rewrite canonical verifier artifacts
- fix: align homepage loop and low-model orchestration
- refactor: remove autoresearch surface from main
- docs: add branch syncing guidance
- docs(readme): trim benchmark and mode guidance
- chore: remove stale plan and scenario artifacts
- chore: ignore autoresearch objective artifacts

## [0.9.1] - 2026-03-27

- fix(auto-researcher): use starter-local temp env
- fix(orchestrator): use workspace-local temp env for starter prep
- fix(make): use repo-local temp env for public commands
- fix(orchestrator): restore api-key-only codex harbor auth
- fix(orchestrator): support codex auth file for harbor smoke
- fix(orchestrator): tolerate ps permission errors in harbor cleanup
- docs(readme): align raidar mode experiment flows

## [0.9.0] - 2026-03-26

- feat: add codex gpt-5.4-mini support
- perf: reuse orchestrator prep caches and add smoke matrix
- refactor: default smoke tests to codex gpt-5.4-mini
- refactor: rename smoke target to orchestrator-smoke
- refactor: add dedicated make agent smoke target
- refactor: route agent smoke checks through make targets
- docs: add orchestrator reuse execplan package
- ci: install Bun in quality gates workflow
- chore: ignore local cache artifacts
- chore: tighten quality gates for implementation-agnostic debt smells
- test: add ci smoke dry-run drift checks
- test: cover smoke repeat and parallel make flows

## [0.8.0] - 2026-03-16

- feat: tighten homepage scenario baseline contract
- refactor: strengthened the homepage scenario revision 001 to be a better baseline and ensure metric usage is suitable/working

## [0.7.2] - 2026-03-14

- fix: preserve provider env for standard Harbor runs

## [0.7.1] - 2026-03-11

- fix: gate fast smoke env wiring and document env surface
- docs: trim env example to common local setup

## [0.7.0] - 2026-03-11

- refactor: align terminology around harnesses and agent specs
- chore: checkpoint current repo state

## [0.7.0] - 2026-03-11

- docs: reorganize review surface artifacts

## [0.7.0] - 2026-03-11

- refactor: simplify repo docs and scripts

## [0.7.0] - 2026-03-10

- feat: add codex gpt-5.4 thinking support
- fix: isolate experiment run directories by model
- fix: recognize heredoc verification commands in codex traces
- fix: pass rerun metadata to experiment summaries
- refactor: finish migration naming cleanup
- refactor: finish unscored rerun migration
- refactor: finish scenario migration cleanup
- refactor: remove legacy comparison package
- refactor: migrate scenario experiment trace vocabulary
- docs: record homepage analysis artifact
- docs: record codex migration validation
- docs: finalize migration wording cleanup
- docs: mark naming migration complete
- docs: close remaining migration cleanup
- docs: refresh codex script command surface
- docs: refresh codex migration plan and scripts
- docs: rename new scenario reference
- docs: finish scenario migration reference docs
- chore: restore coverage database

## [0.6.0] - 2026-03-08

- chore: ignore visual explainer artifacts

## [0.6.0] - 2026-03-08

- refactor: remove opik workflow
- test: enforce argv-only task commands
- refactor runner phase-2 complexity slices

## [0.6.0] - 2026-03-04

- chore: sync uv lockfile version metadata

## [0.6.0] - 2026-03-03

- docs: align metrics v2 guidance and analyzer prompt

## [0.6.0] - 2026-03-03

- feat: add modular metrics schema and artifact presence module
- docs: align reference guides with module-driven metrics
- docs: add metrics module setup guidance for tasks
- docs: backfill readme for required metrics modules

## [0.5.0] - 2026-03-02

- feat: added more rigour to the odiff comparisons

## [0.4.0] - 2026-03-01

- refactor: remove provider probe preflight and refresh eval guidance

## [0.4.0] - 2026-02-24

- feat: add adapter-driven provider probe preflight
- chore: add fast pre-commit quality preflight

## [0.3.0] - 2026-02-24

- Update README.md

## [0.3.0] - 2026-02-24

- Update README.md by removing sections and adding details

## [0.3.0] - 2026-02-24

- feat: add claude sonnet 4.6 and gemini 3.1 pro support

## [0.2.4] - 2026-02-24

- fix: enforce single-source version wiring
- chore: enforce locked uv sync in git hooks

## [0.2.3] - 2026-02-23

- refactor: rename agentic_eval to raidar and standardize evals layout

## [0.2.3] - 2026-02-23

- fix: use StrEnum for harness agent enum
- docs: update AGENTS project map and workflows
- build: track uv lockfile and enforce frozen sync

## [0.2.2] - 2026-02-22

- fix: remove frozen sync requirement from quality gates

## [0.2.1] - 2026-02-22

- fix: align local and ci gate sync flow
- refactor: migrate execution maintenance into raidar cli

## [0.2.0] - 2026-02-22

- docs: update readme strapline

## [0.2.0] - 2026-02-22

- chore: rename project branding to Raidar

## [0.2.0] - 2026-02-22

- feat: finalize multi-provider harness support and smoke validation
- feat(orchestrator): enforce harness-model pairing
- feat(harness): add adapters for cursor copilot pi
- feat(orchestrator): enforce harness adapters and scaffold catalog
- feat(parser): implement Gemini parser and fix Claude Code multi-block handling
- feat(compliance): add structured judge response parsing with retry logic
- feat(config): add centralized configuration with pydantic-settings
- fix: cap void retries to one attempt
- fix: addressed various architecture smells
- fix(harness): support runtime validation plus sample run
- fix(pre-commit): exclude scaffold and generated folders from checks
- refactor: remove scaffold manifests and track workspace diffs
- refactor: finalize v1 execution model and evidence pipeline
- refactor: split run validity and performance gates across scoring pipeline
- refactor: migrate orchestrator ops to cli and untrack generated artifacts
- refactor: align harbor execution with scaffold task bundles
- refactor: require argv commands in task yaml
- refactor: split runner phases and add lizard gate
- chore: add changelog automation and root project readme
- chore: added enaible to ignore
- chore: ignore next-session
- chore: centralize quality gates
- chore(auth): load .env and document codex oauth plan
- chore(devex): add uv setup and harbor prep
- chore: delete actioned implementation plan
- chore: add pre-commit configuration with ruff and pytest
- test: add comprehensive unit test suite with 64 tests
- commit clean ahead of big refactor
- Add context note to implementation plan
- Fix validation and lint script issues
- Add list-agents and info CLI commands from impl-4
- Enhance schemas with @computed_field pattern
- Add comparison/aggregation module from impl-4
- Add vitest test infrastructure from impl-2
- Rename namespace to agentic_eval
- Add configuration matrix and run aggregation
- Add scoring system with multi-dimensional evaluation
- Add scaffold audit, schemas, task definition, and CLI
- Add orchestrator Python project and Next.js scaffold template
- Add research document and implementation plan

## [0.1.0] - 2026-02-22

- Initial orchestrator and task evaluation framework baseline.
