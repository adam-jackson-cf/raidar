# Changelog

All notable changes to this project are documented in this file.

## Unreleased

### Grading and problem fast coverage closure

- Cover retained pre-fix grading, offline coverage reuse and parity, verifier/regrade refusals,
  problem validation, resolution, audit, errata and CLI outcomes with injected fast tests.
- Remove the unreachable negative-repeat outcome comparison: varying cases are already
  quarantined before repeats are compared. Grading and primitive identity bytes stay unchanged.

### Test-suite integration and identity preservation

- Separate fast decisions from slow process seams and quality canaries; keep
  Docker, security and performance checks on the local target without model calls.
- Restore the validated grading implementation and test it through existing
  interfaces and monkeypatched runners. Test-only changes must preserve grading,
  primitive and released-package identities; deliberate identity changes require
  separate review with before/after values and scheduled revalidation.
- Remove the unused primitive resolver, auth metadata method, unused reasoning
  override and redundant credential-redaction guard as reviewed dead-code cleanup;
  regenerate affected execution locks without changing released package bytes.
- Ratchet fast-only coverage floors from 85.20% line / 75.86% branch to
  93.64% line / 89.58% branch; both remain below the 95% closure target.
- Known deferred resolver gap: malformed `uv --version` output and unreadable
  generated locks can escape as parser/index/key errors instead of
  `DependencyInfraError`. Error normalization is deferred to a deliberate grading
  change with revalidation; it is not part of this test-only integration.
- Close engine-and-agent fast-tier coverage gaps with behavioral planning, admission,
  detached-job, trial-failure, provisioning, authentication and image-boundary scenarios.
  Inject runners, clocks and virtual task/job filesystems; preserve production source,
  grading/primitive identities, released packages and the existing coverage floors.

### Phase-5 local exit evidence (phase 5B step 7)

- Local OrbStack security: 38 cases passed, including all seven AT3 cases; AT4 passed 46
  identity/refusal cases. All three package fixture suites passed.
- Repeat no-model define/build/judge mechanics on the final locks: verified, rework, reply repair
  and refused-gate outcomes; positive/refused real verify and docs commits; primitive health
  evidence deletion checks and gate-record effort readings. Scripted agents omit build/session
  audit evidence, so their W1/W2 violations remain an explicit harness limit.
- Keep normally locked ChatGPT-authenticated codex AT1/AT2 commands for later authorization;
  no real-model trial or external publication was performed. Remove throwaway execution scripts,
  retaining records, reports and review screenshots under `var/`.
- Strict local `perf-gate` passed unchanged N1 reference budgets at concurrency 1 and 3, ten
  trials each: warm-start p95 4.07/4.70 seconds, fixed-overhead p95 5.01/5.65 seconds.
  This checks run plumbing; assessment/scorer orchestration is not measured by that gate.

### Final contract artifacts and phase-5 release pins (phase 5B step 6)

- Prepare `raidar-contract==1.0.0` with the public supported-version `CONTRACTS` map and exact
  wheel/sdist hashes in `contract/releases/1.0.0.requirements.txt`. Private artifacts live under
  `var/contract/v1.0.0/`; nothing is published externally. Contract builds refuse existing output.
- Finalize the new phase-5 scorer closures against the contract package; preserve archived
  superseded releases and unchanged stage-package bytes.
- Add `raidar/docs-fix-smoke@1` and the AT1/AT2 smoke definitions for codex `gpt-6-luna`, low
  reasoning. These real-model experiments are prepared only, not executed.

### Complete spike health consumer deletion (phase 5B step 5)

- Remove the stage-keyed definition health reader and validity entry; required package outputs,
  stop calls and terminal states are checked by the primitive-keyed W rows.
- Read judge closure evidence from `jev-judge/1` and instruction provenance from component
  placements only. Delete the legacy stage-instruction path, without a compatibility reader.
- Retain historical G/E family labels only for loading stored health reports; no live producer remains.

### Primitive-keyed required health evidence (phase 5B step 4)

- Re-key W3–W10 by locked contract ids: `gate/1`, `setup/1`, `stop/1`, `outputs/1`,
  `acceptance-reply/1` and `jev-judge/1`, never by package names or stage types.
- Derive required setup/stop/output declarations from each digest-checked package manifest
  into host-written trial config only. Missing or inconsistent declarations fail closed.
- Check qualified call identities, retained output digests, stop-state consistency and reply/judge
  artifacts. A cleanly refused starting canary is healthy; instrument failures still invalidate.
- Preserve legitimate closed and cut outcomes; agent-writable hook-log gaps remain readings.
- Remove the obsolete stage-keyed gate health reader and its validity entry in this cutover;
  package trials have one primitive evidence path, not parallel legacy and primitive readers.

### `verification-effort@1` and pre-fix grading from `gate/1` records (phase 5B step 3)

- Replace the scorer `gate-effort` with `verification-effort@1` (`raidar.scoring.verification_effort`).
  It reads the host-written `gate/1` record of each step's `workflow/workflow.json` (seals, their
  checks, retained pre-fix trees) and the `gate_fix` turns' tokens, cost and time: fix rounds,
  first-pass and step-end cleanliness, failed blocking checks, fix cost and the fix turns'
  correctness. A trajectory without a `gate/1` package is `not_applicable`; a malformed record is
  `unavailable`, never an empty gate. No shadow gate, rule families, autofix or refusal-kind metrics
  remain: `gate/1` records none of them. Outcome ids stay `gate.<metric>`.
- Move pre-fix grading from `raidar.grading.prefix` to `raidar.grading.pre_fix`: it grades the trees
  `gate/1` retained before each fix turn (only when `max_fix_rounds` is above zero) with the step's
  own bundle, sandbox and timeout into the Assessment store. A retained path outside the agent logs
  is an error, never a grade. Stored grades use schema 3, so earlier grades are not read.
- Migrate analysis (`ArmSummary.verification_effort`, descriptive and paired gate effects, the
  manipulation check's `gate_clean_at_step_end`, the Markdown gate table) and the review surface's
  Quality gate view (a failed-blocking-checks table with per-seal rounds) to the new records and
  delete the old families, autofix, refusal and shadow views.
- Delete `gate-effort@15` and its modules, tests and fixtures.
- Read coverage inputs from verified `criteria/1`, captured `tokens.UNITS`, reply maps and retained
  sources, without importing package implementation code; read resource judge calls from `jev-judge/1`.
- Archive and supersede every changed scorer closure: `correctness@10`, `code-quality@9`,
  `resource-usage@8`, `process-compliance@12`, `definition-coverage@6` and `test-coverage@6`.

### Portable reply/judge execution and package-only runtime (phase 5B step 2)

- Wire `acceptance-reply/1` and `jev-judge/1` to validated, digest-checked `criteria/1` data.
  Preserve closure thresholds, pass limits, rework, regression baselines and the fit rule.
  The package lifecycle seals before stop/reply processing; a requested gate fix precedes
  reply repair. A closed producer ends as `no_criteria`; missing or changed evidence after
  `done` is an invalid instrument, never a zero-criterion success.
- Lock workflow-selected reply/judge primitive identities. Keep package requirements separate
  so authored packages cannot implement or override those Raidar-owned consumers.
- Fix the live canary identity boundary: compare stage-check evidence with the loaded packages'
  adapter-independent primitive hashes. Locked arm identities still include the native adapter
  and remain authoritative for pooling, admission and health.
- Bring the coupled spike deletions and caller migrations into this step: the portable API
  and legacy classes cannot coexist without forbidden compatibility adapters.
- Delete `StageType`, the `Define`/`Verify`/`Build` classes, `verify.py`, `define.py`, the closed
  stage and settings models and the schema-1 workflow loader, `factory.py`, the spike entries of
  the code-identity inventory (`SPIKE_STAGE_ENTRIES`, `spike_code`, the spike payload) and the
  schema-1 test fixtures. The runner drives the one `PackageStage` directly, every workflow is
  `schema: 2`, and an arm's `primitive_code` is the runtime primitives it runs (so each arm
  relocks). `define_tool` is no longer on the production editable path: its unit tests keep
  importing the define package's tool source in place.
- Publish the primitives' evidence with the workflow record. The runner used to delete its
  staging directory after publishing only `workflow/`, so `outputs/1` recorded absolute host paths
  to files that no longer existed. It now publishes `stages/`, `gate/`, `calls/` and `install/`
  beside `workflow/` in the step's agent logs and records each staging-owned `host_path` as its
  path below those logs, in `workflow.json` and the context metadata, so an offline reader can
  verify and copy the retained evidence. Behaviour tests cover the path, the hash, and the
  replacement of anything the agent planted under a published name.

### Verified local imports (phase 5B step 1)

- Add `stage-import` and `workflow-import`: preserve exported bytes, verify the supplied
  digest, require registered byte-identical workflow dependencies and refuse unsafe trees,
  `raidar/`, and divergent existing folders/releases.
- Record verbatim, unverified single-line provenance in `lab/imports.yaml`, one entry per
  digest. Provenance is outside release, lock, pooling and admission identity.
- Extend release checks to every imported folder and historical release digest.

### Shipped workflows on schema 2 (phase 5A step 6)

- Archive the three namespaced schema-1 `@1` workflows immutably, then register schema-2
  `raidar/one-shot@2`, `raidar/one-shot-verify@2` and `raidar/define-build-verify@2`.
  Preserve all build prompt strings and judge rework bytes, model, thresholds and round bounds.
  Unmarked arms retain the no-stage baseline; authoring defaults to the verify workflow.
  Move active experiment references and regenerate all ten locks through public commands.
- Keep `define-build-verify@2` registered and lockable, but explicitly refuse its trial
  execution until phase 5B wires acceptance replies and the judge to `criteria/1`.
  Retain schema-1 spike fixtures only for parity coverage pending 5B deletion.
- Teach health to read authoritative schema-2 definitions and require generic primitive
  records without package-name special cases. Missing evidence, runtime invalidity and gate
  instrument errors remain invalid; the full W3–W10 primitive-row re-key stays in phase 5B.
- Fix reproducible installs with uv's `--no-installer-metadata`; varying cache timestamps
  previously changed installed-tree hashes for identical wheels. All installed files remain
  integrity-checked. Add a failing-before/passing-after fresh-install timestamp regression;
  regenerate every shipped package's local installation proof and the define canary.
- A local draft no-model smoke passed 16 real Harbor-container scenarios through actual
  package prepare/check/close, including accepted/refused agent commits and unchanged
  workspace files. The fixture overriding the locked timeout is covered by stage-check,
  not silently applied to the locked workflow. No native session, model or judge proof claimed.

### Real docs-check and package copying (phase 5A step 5)

- Ship `raidar/docs-check@1`, invoking pinned codespell 2.4.1 over repository text with six
  offline fixtures, including binary, zero-byte, non-ASCII and nested-misspelling cases.
- Replace `stage-new`'s generated template with a byte-preserving copy of `FROM=<ns>/<name>`,
  defaulting to the real `raidar/docs-check` package. Delete `cli/stage_template.py`.
  Change only the manifest name, preserving commands, skills, fixtures, modes and YAML
  presentation (comments, anchors/aliases, scalar styles and CRLF), even for read-only manifests.
  Refuse traversal, overwrites, symlinks and special files instead of copying unsafe trees.
- Keep stage namespaces/names out of Python directory naming checks without relaxing the
  package/module rules inside `tool/src`. Remove obsolete template and affected wiring assertions.
  A real default-source `stage-new` followed by local `stage-check` passes on a renamed copy.

### Define stage package (phase 5A step 4)

- Ship `raidar/define@1` with setup, a sealing prerequisite, a bounded stop and `criteria/1`
  plus definition outputs. Move the 21 existing `define_tool` modules into the package's
  tool source, migrate every caller and retain their unit-test collection in root quality.
  There is no old module-path alias or duplicate implementation.
- Give setup, gate and stop calls 60-second budgets and allow three stop resumes. Preserve
  unit rules 2 and two accepted clarification rounds. Malformed agent drafts request one
  repair and then close, while corrupt trusted context remains an instrument failure.
- Retained spike callers import the package-owned tool through the editable source path.
  Their identities explicitly inventory those moved bytes, without normalizing them back to
  old paths; regenerate locks for the real source-identity change. Phase 5B removes this
  legacy host import along with the spike, and wires reply/judge consumption of `criteria/1`.

### Verify stage package (phase 5A step 3)

- Ship `raidar/verify@1`: the spike's lint, format, strict-mypy and pytest checks on pinned
  ruff 0.8.6, mypy 2.3.1 and pytest 9.1.1, including excluded-directory handling and individual
  pytest outcomes. Offline gate fixtures cover every refusal, empty collections and actual
  per-test timeout overrides; Docker parity compares each check with the spike on identical trees.
- Pytest-kind package checks now honor positive-integer `params.test_timeout`; the verify
  default is 60 seconds. The parameter does not replace the whole-check budget. Packages
  without it retain the existing `timeout_s // 2` per-test default (minimum one second).
- Keep the binding `stage/1` ceiling: all verify checks have 300-second total budgets,
  rather than the spike's 600-second watchdog. Portable contract and stage-owned Python tool
  sources remain in lint, type, complexity, fanout and coverage gates after ownership moves.

### Schema-2 admission and workflow namespaces (phase 5A step 2)

- Workflow references now require a namespace (`ns/name@N`; folders `lab/workflows/<ns>/<name>/`).
  The three schema-1 workflows move unchanged to `lab/workflows/raidar/` (manifest names stay the
  leaf, so definition digests are unchanged) and start new series `raidar/one-shot@1`,
  `raidar/one-shot-verify@1` and `raidar/define-build-verify@1` at the digests of the former bare
  `one-shot@1`, `one-shot-verify@3` and `define-build-verify@4`. The bare register entries stay
  as frozen history and never resolve. The default workflow is `raidar/one-shot@1`;
  `workflow-new` defaults to `raidar/one-shot-verify` and takes `<ns>/<name>`; `check-structure`
  refuses workflows outside `lab/workflows/<ns>/<name>/`.
- Validate `build.reply` and judge criteria/model/threshold/pass/rework configuration.
  Refuse missing structured replies or `criteria/1` producers and ambiguous pytest
  evidence as ST3; reply/judge execution is refused until the 5B wiring, not omitted. Primitive-owned judge
  constants pin three retries, the question-set id and the 60-second request window.
  Gate contexts publish a root-owned starting `base` before checks, override authored
  values, and retain that base after the agent commits.

### Fixes

- Phase 4/4b Docker gate follow-up: run `stage-check` gate calls as UID 1000 by
  default while preserving explicit root installs and the gate's not-root check.
  Resolve declared gate commands through the installed wrappers and their package
  venvs, matching setup and stop. Require passing controls for planted exit/timeout
  tests; exercise wrapper/venv resolution. Use `acme/<name>` consistently in AT3
  installations and replay captures, and disable caching for root-only seed pip
  upgrades so agent-owned uv caches stay untouched. Regenerate all ten experiment
  locks for the changed primitive identities; released definitions remain frozen.
  Local OrbStack security passed all 38 tests; gate-check, note-state replay and
  fresh docs-check fixtures passed. By unanimous vote, remove stage-check's generic
  `wrong-state` case: Raidar has no package-independent semantic state schema.
  Each stop package defines wrong state through its mandatory `closed` fixture
  and `resume` when `max_rounds > 0`. No release or lock changes are needed.
  The `mbp` re-run remains pending; problem revalidation and performance checks
  run separately.

### Features

- Extract the portable stage, workflow, criteria, release and agent contracts into
  `raidar-contract` (`1.0.0rc1`), with no Raidar or Harbor imports. Migrate every
  orchestrator consumer to that implementation and include the distribution in
  source inventories and quality gates. `make contract-build` writes wheel/sdist
  artifacts and SHA-256 hashes under `var/`, without publishing. Frozen release
  register digests remain unchanged; execution and grader source identities change.

- Record the adopted 2026-10-31 FXL pivot decisions and phase-5 scope in
  `split-design.md`, with the three vote records preserved as evidence. Accept
  `FXL-` fault/remedy IDs and FXL experiment/question citations, including tagged
  cross-repository evidence references. Historical record import remains phase 8;
  the phase-5 package cutover is not implemented by this change.

- Phase 4b A: label the primary reference-solvable SCB success; pin/report determinacy
  flags and assertion coverage, with zero coverage unmeasured. Skip JSON reformat probes
  when the spec fixes the format. Release `correctness@8`.
- Phase 4b B: default to five reference repeats and retain reference/environment-scoped
  "no variation observed in N runs" observations. Agent submissions remain single-graded.
  Five-repeat problem revalidation is pending while `mbp` is offline.
- Phase 4b C: pin outcome support from bound checkpoint counts; use exact McNemar or
  sign-flip tests for verdicts, including aggregates. Intervals stay descriptive.
  Report exclusions per arm/cause; refuse without a pre-registered material attrition
  difference or when it is exceeded. Exact-enumeration limits refuse, never approximate.
- Phase 4b D: pin immutable grader environment and implementation identities; runtime
  refuses incomplete/mismatched evidence. A starter-completion canary attributes submission
  grading crashes/timeouts in the same sandbox configuration. Resolver timeouts allow one
  immediate identical retry (two 120 s attempts per declared set and architecture), recording
  attempts; exhaustion is infrastructure, never `cap-exceeded`. Old records are not
  retroactively certified.
- Phase 4b E: halt on the solvable verdict; bind P8 instructions to origin and provable
  pre-turn content, leaving unsupported transcripts unmeasured. First rework estimates use
  `first_build`; an unknown estimate refuses scheduling. Include recorded judge spend,
  leaving sent attempts with unknown charges unavailable, and price native agent tokens
  through the shared LiteLLM table. Narrow V3 to ledger/stored-snapshot consistency; record
  H5's setup limit. Release `resource-usage@7`, `codex@3` and `claude-code@2`.
- Phase 4b F: remove the root hook broker. Hook-log findings become readings; independent
  tool/seal/session integrity remains validity. Stage references require `ns/name@N` and
  exactly `lab/stages/<ns>/<name>/`; CODEOWNERS protects the upstream `raidar` namespace.
  Release `one-shot-verify@3` and `define-build-verify@4`; regenerate all ten experiment locks.
  Local no-model smoke passed; Docker security/performance checks remain pending offline.
- Phase 4b voted follow-up: retain the per-experiment exclusion bound with no default;
  equality passes, and missing bounds leave comparative verdicts `insufficient`. Phase-9
  controls must declare a bound. Retain refusal of rework without a first-build estimate,
  with no fallback floor. Execute lock-pinned excluded steps normally under halt progression,
  preserving workspace and budget; report `excluded` and advance using `halt_gate` plus
  `solvable_excluded`, never a fabricated solvable score. Unknown pins, infrastructure and
  missing rewards still halt. Release `correctness@9`, archive `correctness@8`, migrate
  active consumers and regenerate all ten experiment locks. Preserve the three vote records.
  Local no-model runtime smoke exercised both excluded checkpoint shapes and fail-closed
  boundaries; Docker security/performance checks and revalidation remain pending offline.

- Published the external-author `stage/1` contract at `lab/stages/README.md` before
  implementation: strict manifest, primitive call/stop/output protocols, bounded state,
  canary attribution and fixtures, `criteria/1`, stage identity, releases and author commands.
  Shipped spike classes remain until their phase-5 package ports.
- Added pure package/criteria validation, `stage-tree/1` hashing and the six generic primitives:
  framed prompts, root-owned offline wheel installs, confined setup/gate/stop calls, canary
  attribution, bounded replay snapshots and validated output copies. Shared wheel policy,
  cache and install flags now live below both grading and subject tooling.
- Relocated the package-capable gate payload without changing the S6 broker/client functions.
  Generic lifecycle evidence is keyed by primitive; shipped workflows still use spike stages.
  Declared outputs retain immutable per-call snapshots, including intermediate setup/stop results
  and individual gate checks at seals and commits. Transport loss stays integrity-invalid even
  below resource floors; output absence alone does not invalidate setup, resume or closed.
- Added the `stages:` release register, strict `ns/name@N` references, workflow package/param
  identity, ST1–ST7 refusals, and `packages`/`primitive_code` execution identity and pooling.
  Expected installed trees are pinned rather than trusted from a new trial's self-baseline.
  Immutable installs are measured and reused across checkpoints; terminal package prerequisites
  cannot reopen when another package resumes. Canary state and closed flags are checkpoint-local.
  Reserved task slots and duplicate declared tokens are refused before locking.
- Added stage creation, wheel locking, offline container fixtures/replay, listing/info, workflow
  creation and smoke-only draft locks under `var/tmp/`; no author code runs on the host.
- Enforced primitive import inventories, install ownership, the single container-call boundary,
  record-only health imports and private native-agent adapters. Credential preflight and reset
  state paths now belong to the native-agent protocol. Definition health reads the inspector's
  pinned state/input/render evidence instead of executing the method on the host.
  Package instructions use the agent definition's isolated-home placement, never task AGENTS.md.
- Added AT3 no-model Docker scenarios for setup/canary failures, one junk-result repair,
  terminal round exhaustion and agent-planted pytest exit 126/timeouts; wired into security-gate.
  Docker proof and the execution-path perf-gate are pending while `mbp` is offline.
  Local phase-4 quality passed: 3,103 tests, 48 skips, 95.18% coverage. AT4 identity/stale-lock
  refusals and public authoring commands passed locally; no real-model trials were run.

- Problem onboarding from one pinned SCB source through `problem-list`/`problem-info`,
  bulk `problem-import SOURCE=… PROBLEMS=all|a,b,c`, and target-bounded parallel
  `problem-validate PROBLEMS=… REPEATS=5`. Generated pins require no per-problem source file.
- Task instructions now preserve each upstream checkpoint spec byte-for-byte except its
  two entrypoint substitutions, including canaries; pins record the grader entry command
  and `instruction_sha256` remains the rendered per-checkpoint spec hash.
- Repeated reference validation records per-step solvable sets and their digests,
  nondeterministic cases and excluded empty sets, including regression cases.
- `correctness@3` adds lock-pinned solvable-set solve metrics. The solvable-set checkpoint
  solve rate is the default primary outcome with raw SCB beside it; missing/empty sets are
  unavailable, never zero. Errata remain an explicit pre-registered primary-outcome option.
- Problem bindings accept `checkpoints: k` for a locked first-k multi-step task;
  `MAX_CHECKPOINTS` is refused outside smoke experiments.
- `lab/environments/README.md` states the six-capability runtime-pack contract. Python is
  the only implemented runtime; forge remains historical spike evidence, not a default.
- AT-N1 smoke definition binds generated dag_execution and log_query pins, with
  dag_execution's first two checkpoints, on one-shot/codex/gpt-6-luna/low; execution requires
  user approval.

- `make releases-check` refuses a removed or re-hashed release: the register is append-only
  against the merge base with `main` (or `HEAD`'s parent on `main`; `RELEASES_BASE` overrides).
  CI checks out full history for it.
- `make security-gate`: the Docker security regressions (verify-gate attacks, tool-tree T1) as a
  phase-exit check; a missing Docker target fails instead of skipping (`RAIDAR_REQUIRE_DOCKER=1`).
- The binding design moves to `split-design.md` at the repository root; `AGENTS.md`, README and
  the package guidance point to it.
- Native-agent protocol for isolation, process facts, usage, final text, resume stores and
  session audits; workflow and P8 consumers no longer parse Codex-specific records.
- `claude-code@1` at the existing pack pin `2.1.285`, with isolated config, strict MCP,
  auto-updates disabled, Sonnet model metadata and forge smoke definitions. Isolation proof
  is partial because native records list skill names without their roots.
- P8 retains developer/system message digests by harness version and validates audit source hashes.
- Experiment field `varies` (`harness`, `model`, `reasoning`): an experiment that declares `harness` may compare arms whose agents declare different capability manifests. Every arm must still declare all nine capabilities; the arms share canonical model id and reasoning unless `model` / `reasoning` are declared too. Admission and pooling apply the rule; the plan records `varies`, each arm's canonical model and reasoning. Specs that omit it keep their lock bytes.
- Define stage in the primary session: FXL `ac5db0f` command grammar, unit rules 2,
  deterministic checks and renderer on an offline, root-owned CPython 3.12.14 tool environment.
- `define-build-verify@1`: four workflow-owned skills, proposal acceptance, one definition
  repair and `define.unsealed` commit refusals; one verification pass, no judge.
- Health E1–E3 audit definition validity, call/state consistency and unusable endings;
  workflow component bytes and digests are retained for P8 instruction isolation.
- `define-build-verify@2`: structured replies with bounded repairs, merged test mappings,
  recorded host-side aspect judging and up to three build/rework passes in one session.
- `definition-coverage@1` and `test-coverage@1`: assessment-time per-unit and per-criterion
  Jev readings, with raw evidence and no uncalibrated pass/fail cut-offs.
- Health J1–J3 audit judge evidence, closure and undecided aspects; host judge windows
  pause agent-idle accounting and rework obeys the remaining phase budget.
- The Jev key is refused at sandbox boundaries; names-only agent, grader and coverage
  environment records are retained without secret values.
- `one-shot-verify@2`: record-only final seals, no automatic fix turns; verify defaults to
  `max_fix_rounds: 0`. `define-build-verify@3` preserves two fixes and three judge passes with
  corrected runner responsibilities; earlier releases stay registered.
- Agent-user baseline, seal and definition inspection; immutable tools, a root-owned hook
  broker that drops privilege for checks, and host-authoritative records. Setup has its own
  deterministic, task-code-free check. Gate Git calls disable external hooks/fsmonitor and
  build raw trees without repository filters. Gate processes disable Linux dumpability to
  prevent checks reopening parent record pipes through procfs.
- Health W1–W11 enforce enabled-stage evidence completeness and integrity, with real Docker
  rewrite/write-and-restore attacks against tools, hook state, drafts and session logs.

### Fixes

- K4 validation replaces the previous-reference exclusion with a step-local
  `no_new_scoreable_tests` gate. Byte-identical references pool positive and negative
  repeats; any varying case leaves every solvable set where it appears. Previous-reference
  novelty is a reading only; source-reuse evidence is committed. A spec-to-test audit is deferred.
  Generated reports add per-step `exclusion` and `reference_digests`; excluded steps leave
  the solvable-outcome denominator without changing the raw SCB outcome.
  Re-released as `correctness@6`, `code-quality@7`, `resource-usage@5`,
  `process-compliance@10`, `gate-effort@14`, `definition-coverage@4` and `test-coverage@4`;
  the latter six carry transitive grading-fingerprint changes. Previous releases are archived.
- Accepted `mbp` warm environment-start p95 budget raised from 5 to 5.5 s on 2026-10-28:
  measured spread ±0.4 s across runs that did not touch container start; profiling and
  steadier measurement in phase 7. No other threshold changed.
- Structure permits `lab/stages/` before the separately committed `stage/1` contract page;
  package shape and import enforcement follow in phase 4.
- Locked checkpoint prefixes now govern assessment subjects, solve-rate denominators and
  analysis solvable-set summaries from retained trial plans, rather than the full problem
  bundle. Smoke-only `MAX_CHECKPOINTS` tails remain planned and `not_attempted`.
  Released as `correctness@4`, `code-quality@5`, `resource-usage@3`, `process-compliance@8`,
  `gate-effort@12`, `definition-coverage@2` and `test-coverage@2`; previous releases are archived.
- Reference validation uses the canonical grader's offline dependency handling instead
  of rejecting every reference with a requirements file.
- `declared-wheels/1` provisions graded code's declared packages: uv Python 3.12, PyPI-only,
  wheels-only (no source builds), both manylinux_2_28 arches, UTC import cutoff, caps of 200
  packages / 1 GiB / 120 s. Source-only and unsupported requirements are refused with reasons;
  hash-checked offline installs are cached by dependency digest and regrades reuse the
  recorded lock. Outages are infrastructure failures. Health D1/D2 are descriptive, never
  invalidating, with a per-arm share warning. The old fixed pins, `provisioned_pins`,
  per-package directories and `dockerfile_layer` are removed.
  Released as `correctness@5`, `code-quality@6`, `resource-usage@4`, `process-compliance@9`,
  `gate-effort@13`, `definition-coverage@3` and `test-coverage@3`; the seven scorer releases are
  transitive fingerprint changes with unchanged formulas.
- A halt cannot turn a missing or empty planned solvable set into a measured zero rate.
- Read startup sandbox environment names with Docker inspect instead of running Python inside
  the container; workflow-owned `.claude` skills are excluded from graded snapshots.
- Final seals always rerun checks and fingerprint the checked workspace, including ignored
  and unstaged inputs; they never commit, restore, modify the agent index or reuse verdicts.
- Terminal unusable definitions cannot be reopened within a checkpoint, and empty judge
  criterion sets cannot produce `verified`.
- Fault recording interprets idle findings only for the liveness check, retaining gate and
  definition violations without treating their text findings as idle events.
- Preserve grader and coverage secret-isolation records beyond temporary sandbox cleanup.

## 0.15.0 - 2026-10-24

### Documentation

- docs: explicit lock resolution, grading owners, final-pass record (design §11)
- docs: tiers, singular names and the lab data locations (AGENTS, README, structure design §10)

### Refactoring

- refactor(target): select targets explicitly; drop implicit Docker-context fallback and python resolver script
- refactor(engine): behaviour-named job/trial tests, TASKS_DIR in trial run, module paths in docstrings
- refactor(contract): Harbor result.json reading and timing arithmetic in one module (keeps collect within the fan-out limit)
- refactor(engine): trial records, timing and result reading move down to contract; the reaper joins health, provenance joins job, the plan and job context join trial
- refactor(design): compiling an experiment's arm into treatment components moves up from subject to design/experiment; arm blueprints leave the job plan
- refactor(scoring): headline metrics, ATIF and the scorer registry machinery sit in framework; built-in registrations, outcome metric catalogue and manipulation checks form the catalog tier
- refactor(contract): canonical hashing, source digests and registry references move down to contract; import order and long-line fixes
- refactor: move run-time data out of the code package into lab/ (treatment payloads, pack recipe, judge policies); fingerprints are repo-relative
- refactor(raidar): singular folder names; drop parent-name prefixes from modules
- refactor(raidar): rewrite imports in cli and tests for the stage layout
- refactor(raidar): evaluation stage (analysis, review, ledger)
- refactor(raidar): grading and scoring stages (verifier, scorers framework and packages, judge)
- refactor(raidar): engine stage (targets, jobs, trials, health, perf)
- refactor(raidar): contracts, design and subjects stages (core, spec, problems, agents, components, packs)

### Chores

- chore(lab): relock experiments for the tier layout
- chore(imports): import-linter layers enforce the group tiers and the sub-tiers of engine, scoring, subject, design and evaluation
- chore(lab): relock experiments for the stage layout
- chore: stage layout config and docs (import contracts, structure-check, entry points, AGENTS, README, design record)

### Other Changes

- pack recipe: restore identity-bearing bytes (comment edit re-tagged the image and broke problem pins)
- structure-check: grading/prefix and grading/coverage are owner folders
- Scoring and trial config guidance fixes left uncommitted by the parallel passes; relock experiments
- Move review fixture generators to tests; review-surface data errors explicit; fix job_layout lock doc
- Resolve analysis verifier bundles from the problem store; drop bundle search
- Tool cache under var root; plural lab/treatments and lab/environments; drop ticket tags from pack headers
- Name metric tests for the floor policy, not versions
- Group offline grading into prefix/ and coverage/, name tests for behaviour, explicit artifact path
- cli, contract: drop dead snapshot helper and workstream labels; one resolver source per kind
- evaluation/review: group fixture and synthetic generators in folders
- review-surface: share one JSON data reader
- review-surface: replace utils grab-bag with presentation folder and api types
- uv.lock: sync project version 0.15.0

- analysis: bundle resolution from the problem store within the fan-out and complexity limits; type two test helpers
- analysis: assessment settings come from the lock or an explicit file, never the job's copy
- cli: assess takes the explicit or current lock only
- cli: name the command runner and git-state modules for what they hold
- design: rename compat gate to runtime_gate, drop dead code and ticket refs
- evaluation: resolve a job's lock by explicit reference only; one reader for job files
- evaluation: drop workstream tags from docstrings; correct Quality C wording
- gitignore: drop scaffold 'coverage/' rule that hid raidar.grading.coverage/**init**.py from CI
- problems: pin the imported task stub digest; record the stub and validation-snapshot findings
- problems: imported task stub keeps its pinned bytes (fresh imports reproduce the registry); validation trials download their snapshot explicitly
- problems: resolve the store root explicitly (argument, env, lab/problems); no upward search
- review: docs name job roots, not runs roots
- Scoring: group gate-effort autofix and cross-mode, type precisely without ignores, trim wiring tests
- Subject: builtin_definition name, merged prompt truncation, no PATH/version-command fallbacks, gate_without_autofix field
- tests: validation trials use the download snapshot source
- tests: shorten floor-policy analysis test name
- tests: runtime gate refusals pass --lock explicitly
- tests: behaviour names for review projection, quality projection and floor-policy analysis tests
- Verifier: a missing or unusable /app artifact is an infra failure, never a download

## 0.15.0 - 2026-10-24

### Features

- feat: generated data root var/ (RAIDAR_VAR), make release-archive and structure-check
- feat(health): run-health audit, host liveness (observe), T1 tool-tree hashing, smoke-before-measure (mechanics-v1 step 5)
- feat(ledger): tracked fault/remedy ledger, raidar ledger check/index, make ledger-check in quality
- feat(analysis): job provenance, records manifests and a pooling gate in analyze
- feat(skills): raidar-onboarding behaviour skill (repo setup and ready checks)
- feat(reboot): guardrails iteration 4 (gate autofix) and main-04 results
- feat(reboot): guardrails iteration 4
- feat(reboot): guardrails iteration 3b-n and main-03 results
- feat(reboot): guardrails iteration 3b.5 — code-quality@3 case-level coverage parity (pre-registration)
- feat(reboot): guardrails iteration 3b.4
- feat(reboot): guardrails iteration 3b.3
- feat(reboot): guardrails iteration 3b.2
- feat(reboot): guardrails iteration 3b design — scope fix, stall@2, transient retry, gate-effort@5 (pre-registration)
- feat(reboot): guardrails explore iteration 3 — tests in gate, pre-fix snapshots, no-stall continuation, per-trial pack install
- feat(reboot): guardrails main-02 results
- feat(reboot): shadow gate, configurable primary outcome, git evidence, detached jobs; de-vendor guardrails skill
- feat(reboot): guardrails ab-test results
- feat(reboot): python-guardrails@1 pack, gate-effort metrics, guardrails ab-test (replaces ruff experiments)
- feat(reboot): park Jev; harness-agnostic guardrail commit gate

### Bug Fixes

- fix(analysis): a metric with no better direction never reads improved/worse; cli: problem --store via store_at, shared ANY_RUNTIME
- fix(problems): store_at keeps an explicit lab/problems root's generated content under var/
- fix(jev): always remove the temporary cache file after publishing a decision
- fix(packs): ignore OS and cache files in the pack image context digest
- fix(problems): refuse a committed errata that still carries the drafted placeholder reason
- fix(components): review-loop accepts a session-wrapper inner agent; share helpers, fix stale docs
- fix(review-surface): static path prefix check, var/tmp projection default, drop dead StatBlock field
- fix(analysis): refuse arms with no shared attempt instead of IndexError; job layout constants
- fix(ledger): match ids with fullmatch; require an existing --root
- fix(gate-effort): emit new_* gaps for a reached step without a gate record; drop dead scorer code
- fix(targets): validate target names/hosts, no leaked tunnel on context failure, private state dir

### Documentation

- docs: structure design accepted, implementation record
- docs: identity-bearing artifacts follow the edit-in-place release rule
- docs: README component row lists the surviving component versions
- docs: repository structure, var/ root, release-archive and lab/ paths
- docs: update AGENTS.md and README for moved packages and removed legacy targets
- docs: repository structure, primitives and versioning design (accepted)
- docs: ADR-002 A.8 mechanics v1 declared; forge smoke known config
- docs: run mechanics v1 (health, liveness, pooling, ledger, smoke admission); correct design-memo Harbor backend claim
- docs(verifier): drop stale bind-mount wording (scorer digests unchanged)
- docs(reboot): ADR-002 A.3 remote bind-mount guard and gate result
- docs(reboot): execution targets in AGENTS/README, per-target budget policy (mbp proposed, report-only), ADR-002 A.3
- docs(reboot): mechanics-v1 step 0 spike evidence (copy path parity, liveness prototype)
- docs(reboot): ADR-002 mechanics v1, design r2, close guardrails series
- docs(reboot): guardrails iteration 3 write-up

### Refactoring

- refactor: split fan-out and complexity offenders (assess job lookup into analysis.sources, sandbox _run, check_l2, PhaseMonitor host)
- refactor(components): one provision lister, shared ANY_RUNTIME, drop unused missing_secrets
- refactor(cli): public load_cli_experiment instead of a cross-module private
- refactor(spec): import ANY_RUNTIME from core.runtimes
- refactor(review): one definition of scorer refs and job layout names; tolerate unparseable Harbor result.json
- refactor(core,problems): remove dead changed_repo_paths and report_sha256; fix docstring
- refactor(agents): drop unused model-profile labels and ModelTarget.from_string; fix stale CLI names
- refactor(spec): drop dead naming flag, lock version parameter and unused constants; fix stale docs
- refactor(cli): drop mutable agent-choices global and duplicate suite reruns in quality gates; use job layout constants; verifier wording
- refactor(scorers): describe current behaviour only in gate-effort and process-compliance descriptions
- refactor: analysis, review, spec and verifier read the single code-quality, process-compliance and gate-effort refs
- refactor: collapse code-quality into one unversioned scorer and drop per-version registry glue and the scorer Jev store binding
- refactor: flatten lint-tool@2 and prune component entry points to the surviving versions
- refactor: move experiments, problems pins and ledger under lab/
- refactor: flatten guardrail-gate into one unversioned module set at version 4
- refactor: collapse process-compliance into one unversioned scorer package
- refactor: keep the click error conversion of offline grading in cli/grading.py
- refactor: group raidar into core, cli, agents, components/{definition,runtime,catalog}, execution/{health,perf} and review/{projection,quality}
- refactor: delete legacy schema-v1 loader, job migration, dormant process-compliance@1 and code-quality sensitivity
- refactor(reboot): cut mechanics out of scorer keys (mechanics-v1 step -1)

### Chores

- chore(components): drop plan/version-era names from component tests and docs
- chore: fix version-wiring CLI path, stale skill examples, changelog for restructure and mechanics v1
- chore: Makefile help and assess recipe follow the scorer collapse
- chore: remove finished ab-test experiments (kept at tag archive/pre-structure-reset)
- chore: relock experiments after the package restructure
- chore: CLAUDE.md -> AGENTS.md and .claude/skills -> .agents/skills symlinks

### Other Changes

- relock experiments after review fixes
- Update tests to the lab/ and var/ layout
- Split modules to meet fan-out and complexity limits
- Delete versioned python-guardrails tests
- Flatten python-guardrails and provision into unversioned modules and one payload tree
- Collapse gate-effort@1..@10 into one unversioned gate_effort package
- Flatten continue-on-stall@2 and review-loop@2 into unversioned modules
- Liveness enforce by default (idle kill classified like an agent timeout); smoke admission requires agent work
- Per-target N1 budgets: accept the mbp set, select by recorded execution_target
- mechanics-v1: runner dirty flag ignores docs/; forge smoke gets pilot-v2 starter framing; ADR-002 A.6-A.7; skill health.json path
- Make the copy-only data path the only data path (mechanics-v1 steps 2-3)
- Refuse bind-mount work on remote targets until the copy path lands
- Make the Docker execution target configurable per machine (mechanics-v1 step 1)

- detach: read the supervisor command line at unlimited ps width (procps truncates at 80 columns)
- execution: report failed compose down, harden detach supervisor, drop dead helpers
- experiments: add forge mechanics-v1 declaration smoke
- health: keep the idle kill verdict when the phase ends mid-kill; seal symlinked dirs; L2 needs a phase; harden admission and poll script
- make: quality runs lizard once (raidar quality gates already does)
- restructure: drop leftover versioned tests; relock remaining experiments
- scorers: import ANY_RUNTIME from core.runtimes
- scorers: drop dead code-quality branches and stale docs, hash scorer identity once per rescore, no temp-file leak in store writes
- style: ruff import block
- tests: run and errata tests no longer depend on host codex auth
- tests: fork parity compares only the interpreter language level; docs: round 2 grouping proposal
- tests: fork parity ignores the host interpreter identity (fails on any host but the recording one)
- tests: build the problem-store fixture in tmp; drop the committed lab/problems/fixtures copy
- tests: sort imports
- verifier: reap grader processes, catch escaped leak copies, treat malformed ledgers as infra, write store files atomically, drop cross-module private names

## Unreleased - repository restructure and mechanics v1

### Changed

- Clean start (design `docs/reboot/repo-structure-design.md`): no version in any module, class, folder or file name; a scorer, component or headline metric is one module edited in place and its version is the manifest's `@N`. `make release-archive REF=<name>@<N>` tags `archive/<name>@<N>` before an in-place edit. Earlier versioned modules named below (`gate_effort_v10`, `guardrails_v8`, `gate_v4`, `code_quality_v3` and similar) are now `raidar.scorers.<name>/` and `raidar.components.{definition,runtime,catalog,payloads}`. Finished experiments, jobs and stores left the tree (tag `archive/pre-structure-reset`; local data in `var/archive/pre-v1/`).
- Layout: tracked definitions under `lab/` (`lab/experiments/<kind>/<id>/`, `lab/problems/`, `lab/ledger/`), all generated data under the git-ignored `var/` (`RAIDAR_VAR` relocates it). `raidar` packages are grouped by stage (`agents, analysis, cli, components, core, execution, jev, ledger, packs, problems, review, scorers, spec, targets, verifier`). `make structure-check` (part of `make quality`) enforces the root and package allowlists, a depth limit and the no-version-in-names rule.
- Vocabulary follows Harbor (job, trial, step result, attempts, verifier, reward; schema v2); see `AGENTS.md`.

### Added

- Mechanics v1 (ADR-002): Docker targets (`~/.config/raidar/targets.toml`, `make target-*`, trusted-target gate for hidden tests and credentials, lost-trial reaping); per-trial run-health `health.json` (`make health`); host-side liveness monitor (`RAIDAR_LIVENESS`); smoke-before-measure admission (`ALLOW_UNSMOKED=1` overrides, recorded); runner/recipe/limit/target provenance in `job.json` with pooling refusals in `make experiment-analyze` (`POOL_OVERRIDE=`); N1 overhead budgets per target (`make perf-fixture`, `make perf-gate`, `make perf-check`); detached jobs (`DETACH=1`); the tracked fault and remedy ledger (`make ledger-index`, `make ledger-check`).

## Unreleased - guardrails iteration 4 record: smoke-4, main-04 and gate-effort@10

### Added

- `gate-effort@10` (`raidar.scorers.gate_effort_v10`, store-bound) is `@9` plus `new_autofix_lines_changed` and `new_autofix_removed_blocking` (in total and by family), with `total_*` sums. Each value is the step's first autofix pass minus what the previous gated step left for autofix: 0 for an enforced gate, whose tree was fixed, and the previous dry run for a shadow gate. Unlike `@9`'s summed autofix counts, which re-count a shadow gate's never-fixed tree at every step, these compare across gate modes. The analysis, manipulation check, review gate view and pre-fix grading read `@10` first.
- Verifier sensitivity kind `randomness`. `scb/forge@38d627ecf668` adds `test_blueprint_list_uuid_tiebreak_when_created_at_and_name_collide` and `test_allocation_list_ordering`. Both are spec-determined, so not errata; on a program without the `uuid` tie-break, their verdict depends on random `uuid4` order. The upstream draft note gains a qualification and a note on these two cases.
- A detached launch's `detach/launch.json` records `git: {commit, dirty, error}`, so the commit a job ran from, and whether its tree was clean, are on record.

### Changed

- The analysis marks the gate totals that a shadow gate sums over its never-fixed tree with "†" and a legend: `total_first_run_blocking` and `total_autofix_rounds`/`_files_changed`/`_lines_changed`/`_removed_blocking`. Under `@10` the autofix family table shows "sum / new".
- `python__forge__guardrails` now uses scorer `gate-effort@10`. Only the lock's assessment section moved (document `c19711ac463e` → `5f8405fa0a79`); execution `0e597e33e8b4` is unchanged. `smoke-4` and `main-04` were rescored with every `@9` observation unchanged.
- `rationale.md` gains four sections. §18.9 records the `smoke-4` process check, which was clean. §18.10 records `main-04` (8 of 8 valid) and the answer to §18.2: no. r2's fix cost was $0.098 against `main-03`'s $0.098, with 8.5 rounds in both; only wall time fell, from 826 s to 732 s. The coverage minimum drives 85% of fix cost. §18.11 and §18.12 record this amendment and its verification. `docs/reboot/gate-packet.md` §18 summarises the iteration and gives the recommended next step.
- Liveness `enforce` is now the default (`RAIDAR_LIVENESS`; `observe` and `off` opt out). An enforce kill is classified like an agent timeout: the step is verified and graded on what exists and the trial stays valid. The `agent_idle` validity check is gone; L2 is a pass with the finding `idle_killed` (idle seconds, killed pids) and a provisional fault candidate. Calibration: ADR-002 A.9.
- Smoke-before-measure admission requires agent work: a smoke qualifies only if, in every trial, every executed step's `/app` snapshot differs from the previous step's (the first from the empty tree). The refusal names the job, trial and step.

## Unreleased - guardrails explore iteration 4

### Added

- `python-guardrails@8` (`raidar.components.builtins.guardrails_v8`, payload `payloads/python_guardrails_8`): `@7` byte for byte except the version strings, plus `lib/guardrails/autofix.py` and `guardrail-check --autofix`, the pack's safe auto-fixers only (`ruff format`, `ruff check --fix --no-unsafe-fixes`, `ruff format`; protected config; the scope's Python files only; run in a throwaway copy and written back only when every fixer succeeded, never through a symlink; exit 0 done, 126 error) with a report (`python-guardrails.autofix-report/1`: files and lines changed, codes fixed, all and block-tier). Config files are `@7`'s bytes. The install adds an autofix smoke (`I001` and `FORMAT` fixed, the unsafe `F841` kept); the probe requires it. Host tests run `--autofix` with this environment's Ruff; `make guardrails-verify` runs `@7`'s rule canaries against `@8`'s install plus `--autofix` canaries with the pinned Ruff and a mutant allowing unsafe fixes as the negative control.
- `guardrail-gate@4` (`raidar.components.builtins.gate_v4`, wrapper `raidar.components.guardrail_gate_v4`, script `raidar.components.autofix_run`): `@3` plus `autofix_before_check` (default false) and `autofix_command`. Enforced: the command runs in `/app` before every commit check, then `git add -A` and the commit with the gate as the pre-commit hook as in `@3`; a failed pass is typed (`ERROR_TYPES`) and that check falls back to `@3`. Shadow: the command runs on a throwaway copy outside `/app` and the same shadow check runs on the fixed copy (`post_check`); `/app` stays byte-identical. Every pass is recorded under `autofix` in the gate record. Pack-image trial `tests/test_guardrails_v8_trial.py`.
- `gate-effort@9` (`raidar.scorers.gate_effort_v9`, store-bound): `@8` plus the autofix passes (rounds, errors, files and lines changed, block-tier findings fixed by family), first-pass clean and blocking counts after autofix and before it (comparable across an enforced and a shadow gate), and each fix session's cost and wall time by the kind of refusal that started it, with the coverage-minimum share. Analysis (new tables: autofix removals by family, fix cost by refusal kind), the manipulation check, the review gate view and pre-fix grading read `@9` first.
- Known verifier sensitivities (`raidar.problems.sensitivities`, `problems/registry/<dataset>/<problem>/<version>.sensitivities.yaml`): spec-determined cases whose verdict can depend on timing, a typed note that changes no value. `scb/forge@38d627ecf668` records `test_blueprint_list_orders_by_created_at_name_uuid`. The analysis describes each arm's `verifier.grading_coverage_disagreements` (code-quality@3's `regression_parity_excluded_cases` summed per trial; never compared) with the known sensitivities beside it. Upstream draft note (not filed) in `docs/reboot/upstream/scb-forge-unspecified-assertions.md`.

### Changed

- `python__forge__guardrails` (explore iteration 4, `rationale.md` §18): both arms use `python-guardrails@8` and `guardrail-gate@4` with `autofix_before_check: true` (r2 commit, r1 shadow); scorer `gate-effort@9`. Errata, pin, primary and every other component and scorer unchanged; `code-quality@3`'s digest is left as recorded in §17.8 (§18.6). No job has run under this lock.

## Unreleased - guardrails explore iteration 3b.5 and main-03

### Added

- `code-quality@3` (`raidar.scorers.code_quality_v3`, store-bound): `@2` whose G, when a step's coverage parity failed only because at most 3 grader cases changed outcome between the grading run and the coverage run (reasons `ledger_mismatch`/`outcome_mismatch` only; environment, corpus, invocation, process ledger and collected cases equal), leaves those cases out of both runs instead of reading the step, and through `problem_regression` the trial's headline score, as `ineligible`. Every other parity failure stays `ineligible`. New transition metric `regression_parity_excluded_cases` (count; node ids in the reason). In `smoke-3b-4` one timing-sensitive case removed r2's headline; under `@3` r2's G is `ok` at every transition and r1's equals `@2`'s. `make experiment-assess` makes offline coverage runs for `@3`; analysis and the review surfaces read `@3` before `@2` and `@1`.

### Changed

- `python__forge__guardrails` (explore iteration 3b.5, `rationale.md` §17): scorer `code-quality@3`. Only the lock's assessment section moved (document `bab09ca20ed3`, then `27bc858c3ee5` after `code-quality@3` was split into `parity`, `sources`, `observe` and `transition` modules for the fan-out limit with identical rescored values, §17.8); the execution identity stays `c15512778075`. `rationale.md` §16.8 records the `smoke-3b-4` process check, §17.6 its rescore under `@3`, and §17.7 `main-03` (4 attempts per arm, 8 of 8 valid). `docs/reboot/gate-packet.md` §17 summarises iterations 3b to 3b.5 and `main-03` against `main-02`.

## Unreleased - guardrails explore iteration 3b.4

### Added

- `python-guardrails@7` (`raidar.components.builtins.guardrails_v7`, payload `payloads/python_guardrails_7`): `@6` byte for byte except the version strings, plus `lib/agent_tools/expose-test-tools`. The install links the pack's pinned pytest, coverage and pytest's runtime dependencies (only those) into `/opt/raidar-tools/python-guardrails/agent-site` and names it in a root-owned `.pth` file in the system `python3`'s site-packages, so `python -m pytest` and `python3 -m coverage` work with the image's Python in both arms (in `smoke-3b-3` all four of r2's `python -m pytest` commands failed with "No module named pytest"); the probe also requires the `python -m` smokes, a failing `import mypy`, the `.pth` file and an unchanged agent-site listing. Its integrity flags compare with `@7`'s own bytes. `make guardrails-verify` runs `@6`'s canaries against `@7`'s install; pack-image trial `tests/test_guardrails_v7_trial.py`.
- `process-compliance@6` (`raidar.scorers.process_checks_v6`): `@5` plus `work_session_tests_run_any` and `work_session_tests_after_last_code_edit` (edits to test files after the last run do not fail it), with their rates; analysis compares `process.work_session_tests_run_any_rate` and `process.work_session_tests_after_last_code_edit_rate`.
- `gate-effort@8` (`raidar.scorers.gate_effort_v8`, store-bound): `@7` plus `first_run_blocking_net`, `step_end_blocking_net`, `new_first_run_blocking_net`, `total_new_first_run_blocking_net` and `final_step_end_blocking_net`: the cross-mode counts without the `no-untyped-call` cascade and with a refused suppression comment's `MYPY.unused-ignore` at its `SUPPRESS` line folded into it.
- Analysis, the manipulation check, the review surface's gate view and pre-fix grading read `gate-effort@8` and `process-compliance@6` (newest first).

### Changed

- `python__forge__guardrails` (explore iteration 3b.4, `rationale.md` §16, lock `c15512778075`): both arms use `python-guardrails@7`; scorers `process-compliance@6` and `gate-effort@8`. `rationale.md` §15.7 records the `smoke-3b-3` process check; §16.1 records that the four cp3 cases its review blamed for a capped primary are already errata (errata, pin and primary unchanged). No job has run under this lock.

## Unreleased - guardrails explore iteration 3b.3

### Added

- `python-guardrails@6` (`raidar.components.builtins.guardrails_v6`, payload `payloads/python_guardrails_6`): `@5` byte for byte except the version strings and `config/ruff.toml`, where `PLR0904` (too-many-public-methods) joins the per-file-ignores of `test_*.py`, `*_test.py`, `**/tests/**` and `**/test/**`. In `smoke-3b-2` the rule refused r2's `unittest` class at three steps and a fix session hid every test from pytest to get under it. Its config-copy integrity flags compare with `@6`'s own bytes. New canary `RUFF.LINT.per-file-ignores.test-public-methods` (`specs-6.json`, `make_fixtures.py --pack 6`) and a mutant with `@5`'s ignores in `make guardrails-verify`; pack-image trial `tests/test_guardrails_v6_trial.py`.
- `gate-effort@7` (`raidar.scorers.gate_effort_v7`, store-bound): `@6` plus `first_pass_clean_except_autofix`, `first_pass_clean_except_tests` and `first_pass_clean_except_autofix_tests` (per step and as rates; auto-fixable codes `FORMAT`, `I001`, `W291`-`W293`) and the cross-mode counts without the `MYPY.no-untyped-call` cascade (`first_run_blocking_ex_cascade`, `step_end_blocking_ex_cascade`, `new_first_run_blocking_ex_cascade`, `total_new_first_run_blocking_ex_cascade`, `final_step_end_blocking_ex_cascade`).
- `process-compliance@5` (`raidar.scorers.process_checks_v5`): `@4` plus `work_session_tests_run_before_finish` and `work_session_tests_run_rate` (the session given the step's first prompt; a gate's fix sessions do not count); `compliance_rate` counts `plan_written` only with `plan_in_compliance_rate: true`.
- `raidar assess --concurrency N` (`make experiment-assess ... CONCURRENCY=N`, `make assess-regrade ... COVERAGE_CHECK=1 CONCURRENCY=N`; `raidar.verifier.per_trial`): pre-fix grades, offline coverage runs and coverage checks of up to N trials at once, each trial's steps in order, entries in trial order. Default 1.
- Analysis, the manipulation check, the review surface's gate view and pre-fix grading read `gate-effort@7` and `process-compliance@5` (newest first); `process.work_session_tests_run_rate` joins the process outcomes.

### Changed

- `python__forge__guardrails` (explore iteration 3b.3, `rationale.md` §15, lock `f600b96bc7db`): both arms use `python-guardrails@6`; scorers `process-compliance@5` and `gate-effort@7`. `rationale.md` §14.7 records the `smoke-3b-2` process check. No job has run under this lock.

## Unreleased - guardrails explore iteration 3 results (iteration 3b)

### Added

- `python-guardrails@4` (`raidar.components.builtins.guardrails_v4`, payload `payloads/python_guardrails_4`): `@3` byte for byte except the gate's scope and its consumers. A Python file is also an extensionless regular file whose first line is a `#!` line naming `python`, or the in-workspace target of a `*.py` symlink (once, at the target's path). Ruff gets the extensionless files explicitly beside `.`, findings reported through a link are deduplicated, and the tests check's change detection sees the same files. Fixes `smoke-03` r2, where an extensionless `forge` script with a `forge.py` symlink was checked as 0 files. Two canaries (`SCOPE.shebang-script`, `SCOPE.py-symlink`; `specs-4.json`, `# canary-symlink:` fixture header) and a scope mutant in `make guardrails-verify`.
- `continue-on-stall@2` (`raidar.components.builtins.stall_v2`, wrapper `raidar.components.continue_on_stall_v2`): `@1` plus problem-agnostic refusal patterns (first-person "can't/cannot/unable to ... without", "not enough/missing context or information", "... isn't defined/present", "doesn't define/specify") and at most one continuation per step: only the step's first session (no earlier record in the step's logs) is eligible; later sessions are recorded with `eligible: false` and `would_stall`.
- `transient-retry@1` (`raidar.components.builtins.retry`, wrapper `raidar.components.transient_retry`): a session wrapper that reruns a session Harbor classified as a transient provider error (`ApiOverloadedError`, `ApiConnectionClosedError`, `ApiResponseStalledError`, `ApiInternalServerError`, `ApiRateLimitError`) after 60 s and then 180 s, at most twice, with a fixed preface; other errors pass through unchanged. Every attempt's logs (`transient-retry/attempt_<n>`), error class, wait and usage are recorded and summed; an outer wrapper can read the usage through a fresh instance from the record.
- `gate-effort@5` (`raidar.scorers.gate_effort_v5`, store-bound): `@4`'s values with each gate run's evidence summary counting `TEST.*` in the `tests` family. Analysis, the manipulation check, the review surface and pre-fix grading accept `@5`.

### Changed

- `python__forge__guardrails` (explore iteration 3b, `rationale.md` §13, lock `8d8ad43b53d8`): both arms use `python-guardrails@4` and share `continue-on-stall@2` and `transient-retry@1`; scorer `gate-effort@5`. `rationale.md` §12.7 records the `smoke-03`/`smoke-03-r2` process check (no valid r2 trial; the C1 scope hole; per-session stall exposure; missed refusals).
- `docs/reboot/gate-packet.md` §16: explore iteration 3 write-up (the iteration 3 changes, `smoke-03`/`smoke-03-r2` signals including the provider-capacity voids, the stall-detector miss, unequal stall exposure and the symlink scope hole, and the iteration 3b design); §15.8 now points to it.

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

  | Was                                                                                                                                                           | Now                                                                                                                                                                             |
  | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | run, `runs/`, `run.json`, `run.lock.json`, `RUN=`, `RUNS=`, `RUN_ID=`, `RUNS_DIR=`, `--run-id`, `--runs-dir`, `--allow-dev-runs`                              | job, `jobs/`, `job.json`, `job.lock.json`, `JOB=`, `JOBS=`, `JOB_ID=`, `JOBS_DIR=`, `--job-id`, `--jobs-dir`, `--allow-dev-jobs`                                                |
  | trajectory, `TrajectoryRecord`, `trajectories/<trial>.json` (`raidar-trajectory`)                                                                             | trial, `TrialRecord`, `trial-records/<trial>.json` (`raidar-trial`, schema 2)                                                                                                   |
  | `CheckpointAttempt` (`raidar-checkpoint-attempt`), record field `attempts`                                                                                    | `StepResult` (`raidar-step-result`), record field `step_results`                                                                                                                |
  | blocks, `analysis.blocks`, `--blocks`/`--block`, `BLOCK=`, `SMOKE_BLOCKS=`                                                                                    | attempts, `analysis.attempts`, `--attempts`/`--attempt`, `ATTEMPT=`, `SMOKE_ATTEMPTS=`                                                                                          |
  | oracle (hidden grader), `raidar.oracle`, `RaidarOracleVerifier`, `OracleBundle`, `ORACLE_BUNDLE=`                                                             | verifier, `raidar.verifier`, `RaidarVerifier`, `VerifierBundle`, `VERIFIER_BUNDLE=`                                                                                             |
  | harness (`codex-cli`), `agents[].harness`, factor `agent.harness`, `raidar harness ...`, `make harness-list/harness-validate`, `HARNESS=`, `HARNESS_VERSION=` | agent name (Harbor `AgentName`: `codex`, `claude-code`), `agents[].name`, factor `agent.name`, `raidar agent ...`, `make agent-list/agent-validate`, `AGENT=`, `AGENT_VERSION=` |
  | `runtime: {language, environment, compose_init}`                                                                                                              | `runtime: <pack id>` and `environment: {type, compose_init}`                                                                                                                    |
  | headline policy, `assessment.headline_policy`, `ScorePolicy`, `raidar.spec.policies`, reference kind `policy`                                                 | headline metric, `assessment.metric`, `Metric`, `raidar.spec.metrics`, reference kind `metric` (ids `scb-canonical@1/@2` unchanged)                                             |
  | validity checks `oracle_verdicts`, `oracle_infra`, `no_oracle_leak`, `harness_version`; phases `oracle_leak_check`, `oracle_run`                              | `verifier_verdicts`, `verifier_infra`, `no_verifier_leak`, `agent_version`; `verifier_leak_check`, `verifier_run`                                                               |
  | `REVIEW_RUNS_ROOT=`, `QUALITY_RUNS=`, `--runs-root`                                                                                                           | `REVIEW_JOBS_ROOT=`, `QUALITY_JOBS=`, `--jobs-root`                                                                                                                             |

  A make variable under its old name fails with its new name. Kept: _problem_, _checkpoint_, _experiment_, _arm_, _condition_, _component_, _factor_, _lock_, _verdict_; Harbor's `oracle` agent (`AGENT_OVERRIDE=oracle`); and, byte for byte, the frozen artifacts (released components' fingerprint sources, the grader plugin, the Python pack image recipe and the verifier evidence names inside trial directories).

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

  | Removed target                          | Replacement                                                                                                                        |
  | --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
  | `scenario-list`                         | `make problem-list`                                                                                                                |
  | `scenario-info`                         | `make problem-info PROBLEM=<ref>`, and `make experiment-plan EXPERIMENT=<path>` for an experiment's resolved contract              |
  | `scenario-validate`                     | `make experiment-validate EXPERIMENT=<path>` and `make problem-validate PROBLEM=<ref>`                                             |
  | `scenario-init`                         | `make experiment-init EXPERIMENT=<dir>/experiment.yaml`                                                                            |
  | `scenario-clone-revision`               | no target: add a condition and an arm inside the existing `experiment.yaml`                                                        |
  | `experiment-run`, `matrix-run`          | `make experiment-execute EXPERIMENT=<path>`                                                                                        |
  | `runtime-stack-scenario-smoke`          | `make pack-audit` and `make component-probe-trials`                                                                                |
  | `experiments-list`, `experiments-prune` | none: `runs/` is a plain directory; `make harbor-cleanup` removes leftover trial environments                                      |
  | `smoke-matrix SMOKE_MATRIX_SELECTOR=`   | `make smoke-agents SMOKE_ARM=codex\|claude` (the Gemini selector is gone; the Python pack bakes in only codex-cli and claude-code) |
  | `benchmark-fixture-synthetic`           | none: the scenario-epoch review read path is removed (see below); `make review-fixture-synthetic` writes the experiment fixture    |

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

## 0.14.0 - 2026-07-09

### Refactoring

- refactor: migrate verifier runtime architecture

- wireframe: refine review surface theme

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
