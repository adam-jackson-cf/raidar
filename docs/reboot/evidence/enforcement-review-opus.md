**Activity Summary**
- Outcome: a read-only enforcement review of branch `lab-workflow-design` against `docs/reboot/split-design.md`, which I read in full. I edited no repository file.
- Inputs: `README.md`, every `AGENTS.md`/`CLAUDE.md`, `Makefile`, `scripts/checks/`, `orchestrator/.importlinter`, `.github/`, `codereview.md`, `CHANGELOG.md`, the test layout, and the code under `subject/`, `engine/health/`, `design/experiment/` and `scoring/`. The `docs/` reads cover only `split-design.md`, `repo-structure-design.md` and the status headers of the documents that README cites. I mapped the code by search, not codegraph.
- Checks run: `env -u AGENT make structure-check` printed `ok`. `make releases-check` printed `releases: ok`. `lint-imports --no-cache` kept all 16 contracts, with 0 broken. There is no `make import-lint` target (`No rule to make target`). I did not run `make quality` because nothing changed.

# Does the repository enforce the approved design?

## 1. Verdict

The repository enforces the spike's shape well, but steers weakly toward the new design. The existing mechanisms all pass: 16 import contracts, the structure allowlist, the release register and the lock currency check. They encode the old model, though. `subject.workflow` is one layer with no internal boundary. Stage types are closed `Literal`s. Code identity comes from a hand-kept module inventory. Health rows are keyed by stage name. [OBSERVED `orchestrator/.importlinter:42-47`, `subject/workflow/definition.py:58`, `subject/workflow/stages.py:28-75`, `engine/health/evidence.py:332-344`]

Guidance makes this worse. Coding agents are forbidden to read `docs/` without consent, and nothing outside `docs/` points to `split-design.md`. [OBSERVED `AGENTS.md:3-4`, `CLAUDE.md:3-4`; grep for "split-design" in README, AGENTS, src, CHANGELOG and `.agents` finds nothing]

The three biggest risks of drift:
1. **New stages land as Python in `subject/workflow/`.** `verify.py` and `define_tool` are the only working examples, and no contract stops a third one.
2. **Identity drift.** A primitive module can be left out of the inventory, and a released `@N` can be silently re-hashed. Either way, jobs pool across a change in behaviour.
3. **Security guarantees regress silently.** UID 1000, no package code on the host, no network installs and key isolation are proven only by Docker tests. `make quality` and CI skip those tests.

## 2. Findings

| ID | Area | Finding | Evidence | Recommendation | Pri | Eff |
|---|---|---|---|---|---|---|
| A1 | abstraction | Raidar host code imports and runs package code (`define_tool`). Once define becomes a package, health, judging and acceptance would run package code on the host | [OBSERVED] `engine/health/definition.py:33-35,234`; `subject/workflow/acceptance.py:18-19`; `judging.py:32-33`; `define.py:164-167` | Move the `criteria/1` and `acceptance-reply/1` parsers into `raidar.contract`, owned by Raidar. Compute the units tokens with the package's `setup/1` call inside the container | P1 | M |
| A2 | abstraction | The stage set is closed in code. The `Stage` ABC is an open extension point for code-defined stages, and the build frame is found by its name | [OBSERVED] `definition.py:58-60,71,90-94,164-165,172,191,252-260`; `stage.py:98-101,137`; `runner.py:60,109,356,428,446`; `factory.py:15-35` | Delete `Stage`, `StageType`, `factory.py` and the settings models. The runner iterates over packages × primitives from manifest data, as design §5.5 describes | P1 | L |
| A3 | abstraction | `verify.py` mixes `gate/1` mechanics (broker, seal, root install) with Python check content and the judge loop. The judge's config sits inside verify's settings and needs define enabled | [OBSERVED] `verify.py:112-115,256-272,492-`; `definition.py:128-166,189-201,274-284` | Split into the `gate`, `gate_tool` (shipped to the container) and `judge` primitives. ruff, mypy and pytest move to `lab/stages/verify/stage.yaml` | P1 | L |
| A4 | abstraction | The code-identity inventory is hand-written and untested. A missing module means a behaviour change outside the identity | [OBSERVED] `stages.py:28-96`; no test imports `stages.py` (grep); consumers `design/experiment/arm.py:101-123`, `evaluation/analysis/pooling.py:46,172`, `contract/trial.py:46`, `engine/trial/plan.py:213` | Keep one explicit `primitive_code` inventory (`workflow/identity.py`). Add a test that each primitive's import closure, computed with grimp (installed with import-linter), is a subset of its inventory | P1 | S |
| A5 | abstraction | Agent-specific code lives outside the native-agent protocol (`subject/agent/protocol.py`), and the protocol has no credential or state-reset member | [OBSERVED] `engine/job/credentials.py:17,48-88`; `engine/trial/agent_config.py:23,122-135`; `engine/trial/collect.py:77-80`; `engine/health/step_reset.sh:9-40`; `cli/agent.py:13-15`; `cli/env_harbor.py:16`; `protocol.py:88-105` | Move every codex and claude module under `subject/agent/adapters/` and protect it (contract C3). Add `credentials` and `state_paths` to the protocol, or record an owner exemption | P1 | M |
| A6 | abstraction | Health is keyed by stage names (W3–W9, G, J, E) | [OBSERVED] `evidence.py:148,333-343`; `health/gate.py:82-84`; `health/judge.py:280-282`; `health/definition.py:160-167` | In phase 4, write `workflow.json` keyed by primitive (`gate/1`, `stop/1`, `jev-judge/1`, call records) so phase 6 doesn't need a second record migration | P1 | M |
| A7 | abstraction | "Judges and scorers stay Raidar-owned" holds by construction today: registrations live in code and the workflow schema refuses extra keys. One gap: any installed distribution can register a `scorer` resolver entry point | [OBSERVED] `scoring/catalog/builtin.py:49-53`; `definition.py:78-79`; `contract/references.py:110`; `orchestrator/pyproject.toml` `[project.entry-points."raidar.contract.resolvers"]` | Keep `stage/1` strict (`extra="forbid"`, no `judge`/`scorer` keys) and test that ST1 refuses them. Optionally accept entry points only from the `raidar` distribution | P2 | S |
| A8 | abstraction | gate-effort@11 and `grading/prefix` read `guardrail-gate` records that no current code writes | [OBSERVED] `scoring/gate_effort/records.py:32-33`; `grading/prefix/grade.py:62`; `grading/offline.py:39`; grep of `subject/` and `engine/` finds no producer; README:56 still advertises it | Delete them in the `verification-effort@1` port (design §7.1). Update `.importlinter:33-40` | P1 | M |
| V1 | verification | Nothing enforces that a released `@N` is frozen. `releases-check` compares only the current hash, so rewriting an existing line passes | [OBSERVED] `subject/releases.py:1-10`; `contract/releases.py:60-66` | Make the register append-only in `releases-check`: compare against `git show <base>:lab/releases.yaml` and refuse changed or removed entries. This matters more once `stages:` lands | P0 | S |
| V2 | verification | The trial installs tools from the network. The design deletes this path | [OBSERVED] `verify.py:256-272,410` | Add an `install/1` contract test under `--network none`. Add a static rule in `check-python-quality.py`: `pip install`, `uv pip install` and `uv sync` literals may appear only in allowlisted modules (pack provisioner, workspace seeding) | P1 | S |
| V3 | verification | The Docker attack regressions don't run in any routine gate | [OBSERVED] `tests/test_workflow_verify_docker.py:42-45` skips without Docker; the Makefile has only `perf-gate` (`Makefile:481-484`) | Add a `make security-gate` target (Docker, runs `*_docker.py`) and make it a phase-exit rule for changes to primitives, the runner or `engine/trial`. Extend it for packages: calls run as UID 1000 under `env -i`, no judge key, canary | P0 target / P1 cases | S/M |
| V4 | verification | No check enforces "package code never runs on the host". One test runs the define tool on the host on purpose | [OBSERVED] `tests/test_define_shim.py:1-30` | Add one regression: a fixture package whose tool writes a marker when imported. Static `stage-check`, lock, health and assess on the host must leave no marker | P1 | S |
| V5 | verification | No check enforces "every call recorded host-side". `exec` calls are scattered | [OBSERVED] `verify.py:785-789`; `define.py:202` (also has no timeout) | AST rule: inside `subject.primitive` and `subject.workflow`, `.exec(` and `.upload_file(` may appear only in `primitive/calls.py` and in the runner's agent-turn path | P1 | S |
| V6 | verification | A ban on `Literal` stage enums is easy to evade (dicts, string compares) | [INFERENCE] | The real guard is a behaviour test: a fixture package with a fresh name passes static `stage-check`, lock and a fake-environment runner with no source edit. This keeps AT1 permanently. Optional extra: an AST rule that refuses string constants equal to in-tree package names in `orchestrator/src`, with an allowlist | P1 | S |
| V7 | verification | The structure check has no `stages` lab directory and no package shape rules | [OBSERVED] `scripts/checks/check-structure.py:56-58,65-90` | Allow `lab/stages/[ns/]<name>/`. Require `stage.yaml` and `prompt.md`, fixed allowed children, folder name = manifest name, no symlinks. Add the new `subject` subpackages | P1 | S |
| V8 | verification | ST1–ST7 don't exist yet | [OBSERVED] design §5.6; there is no ST code | One refusal test per ST code, in the style of `test_runtime_gate_refusals.py`. ST5 runs `uv lock --check --offline` for each `tool/`, inside `make quality` with no Docker | P1 | M |
| V9 | verification | No test makes sure every health row can fire | [OBSERVED] `evidence.py:332`; `test_health_evidence.py` never iterates `ROWS` | One parametrised test: every `ROWS` id has a fixture that violates it, checked by set equality. This is the phase 6 exit | P1 | S |
| V10 | verification | CI runs only on `main`/`master`. Enforcement on this branch is local hooks, which `--no-verify` skips | [OBSERVED] `.github/workflows/ci-quality-gates-incremental.yml:3-7`; `.pre-commit-config.yaml:15-24` | Phase commits quote their `make quality` result. Owner decides on branch CI | P2 | S |
| V11 | verification | Once define's tool moves to `lab/stages`, about 20 modules leave lint, typecheck and coverage | [OBSERVED] `cli/quality.py:20-26,99-113`; `check-python-quality.py:18-23` | `stage-check` runs the package fixtures. Owner decides whether in-tree packages also get ruff/mypy | P1 | S |
| V12 | verification | Contract-schema tests exist for the workflow schema only, and they pin the closed set | [OBSERVED] `tests/test_release_definitions.py:54` | One contract-test file per primitive (`test_primitive_<name>.py`), as the phase 4 exit requires | P1 | M |
| G1 | guidance | Agents can't read the approved design. README points to superseded documents | [OBSERVED] `AGENTS.md:3-4`; `CLAUDE.md:3-4`; `README.md:18` (frozen v1 requirements, implementation-plan, ADR-001, design-memo) | Give standing consent for `docs/reboot/split-design.md`, or put the binding contract page outside `docs/` (see Q1). Fix `README:18` | P0 | S |
| G2 | guidance | README contradicts §5.1 and lacks the design's terms, while `AGENTS.md:6` makes README Concepts the binding vocabulary | [OBSERVED] `README.md:5` ("staged agent sessions such as a review loop"); `README:50` (fixed define/build/verify); there is no Stage package, Primitive, Build frame or primary Session row | Now: fix `README:5,18`. Phase 4: add the Concepts rows, then rewrite `README:50,56,165,228,240-249,256` as each phase lands | P0 / P1 | S |
| G3 | guidance | The `src` boundary guidance is stale or ambiguous | [OBSERVED] `src/raidar/AGENTS.md:4` omits `judge` (the tiers contract includes it, `.importlinter:8-20`); `:9` says "workflow > agent"; `:14` has no `lab/stages`; `:19` says "Keep Jev dormant", which conflicts with `jev-judge/1` (README:59 limits "parked" to the policy machinery) | Rewrite with the rules in §3. Scope `:19` to the scoring-policy machinery | P0 | S |
| G4 | guidance | Nested guidance is stale | [OBSERVED] `scoring/AGENTS.md:6` (process-compliance@7, which §7.1 retires); `lab/environments/AGENTS.md:4` ("before Phase 1 process sign-off", against the §3.2 item 6 pack contract); `AGENTS.md:8-9` (`components/**` "treatments": no such directories exist) | Fix `:8-9` and `environments:4` now. Fix `scoring:6` in phase 6 | P1 | S |
| G5 | guidance | Docstrings cite design section numbers, and the citations have already drifted | [OBSERVED] `stage.py:1` cites "§5.1" for the stage-type interface that §5.1 now replaces; `pooling.py:7` cites "5.4" | Cite contract ids (`gate/1`) and the contract page instead. Don't add a section-number convention | P1 | S |
| G6 | guidance | The design's review checklist doesn't exist. `codereview.md` is a dated audit | [OBSERVED] `codereview.md:1` | Add a five-line "before completing" list to the root `AGENTS.md`: identity → relock; new primitive module → inventory; no package names in src; calls through `calls.py`; `security-gate` when primitives change. No separate PR template | P1 | S |
| G7 | guidance | The release skill doesn't cover stage packages | [OBSERVED] `.agents/skills/raidar-release/SKILL.md:3,11,14` | Add stages, ST checks and `stage-lock` in phase 4 | P1 | S |
| G8 | guidance | The `make stage-new` template (docs-check) will be the strongest behavioural guide | [OBSERVED] design §5.6–5.7 | Make docs-check use every tier 2 field and include `done` and adversarial fixtures | P1 | S |
| G9 | guidance | CHANGELOG entries don't name contract or identity changes | [OBSERVED] `CHANGELOG.md:5-41` | Convention: an entry names `contract@N` or the identity it moves, plus "relocked". No ADR series; contract bumps go in the contract page's change log | P2 | S |

Some proposals cost more than the drift they would prevent. A `Literal` AST ban alone, a PR template, a new ADR series, and a `make import-lint` target are all covered by guidance plus `make quality` [INFERENCE].

## 3. Target layout and import contracts

The structure check allows at most three package levels; this layout fits [OBSERVED `check-structure.py` `MAX_PACKAGE_DEPTH = 3`].

```text
orchestrator/src/raidar/
  contract/            + criteria.py (criteria/1), reply.py (acceptance-reply/1 record), calls.py (call-record and stop/1 schemas)
  subject/
    agent/             public: protocol.py, definition.py, config.py, harness.py
      adapters/        private: harbor_cli, codex_*, claude_*, isolated_*, *_audit, codex_auth, registry
    package/           stage/1 manifest, lab/stages registry + releases stages:, stage-tree/1 digest (pure data, no Harbor)
    primitive/         frame, install, setup, gate, gate_tool (container-shipped, stdlib only), stop, outputs,
                       reply, judge (jev-judge/1), calls, canary; registry.py: {name/N: module}
    workflow/          runner.py (Harbor BaseAgent), definition.py (workflow schema 2), identity.py (primitive_code)
    environment/       unchanged
  design/experiment/   + ST1–ST7 beside RT0–RT6 (sees package + primitive)
lab/stages/[ns/]<name>/  packages (define tool, verify checks, docs-check); CODEOWNERS already covers /lab/
```

Contracts, all checkable with import-linter 2.11 (`layers`, `independence`, `forbidden` and `protected` are available [OBSERVED]):
- **C1 `subject-tiers` (replaces `.importlinter:42-47`).** Layers: `subject.workflow` > `subject.primitive | subject.agent` > `subject.package`. Primitives can't import agents, so agent-specific code can't leak into a primitive.
- **C2 `primitives-only-via-runner`.** `protected`: `raidar.subject.primitive`, with allowed importers `raidar.subject.workflow` and `raidar.design.experiment` (ST2).
- **C3 `agent-adapters-private`.** `protected`: `raidar.subject.agent.adapters`, with allowed importer `raidar.subject.agent`. Today this breaks at `engine/job/credentials.py:17`, `engine/trial/agent_config.py:23`, `cli/agent.py:13-15` and `cli/env_harbor.py:16` [OBSERVED].
- **C4 `health-reads-records-only`.** `forbidden`: `raidar.engine.health` must not import `subject.workflow`, `subject.primitive` or `subject.package`. Today this breaks at `health/definition.py:33-35`.
- **C5 `scoring-boundary` (extends `.importlinter:108-115`).** Add `subject.primitive` and `subject.package` to the forbidden modules.
- **C6 environment independence.** `subject.environment` is independent of `subject.primitive` and `subject.package`.
- **C7 `scoring-tiers` (`.importlinter:33-40`).** Drop `gate_effort` and `process_checks`; add `verification_effort`.

Contracts aren't the whole guard. "Packages can't reach Raidar internals" holds only because packages live outside `raidar` and run in the container. [INFERENCE] Back that with V4, V7 and an AST check in ST1 that refuses `import raidar` in package source.

## 4. Tests and docs to delete or rewrite at cutover

Rewrite these against the new behaviour. Don't re-pin them.
- `test_release_definitions.py:54`: "unknown stage type 'polish'". Rewrite to "a mutable or unreleased package reference is refused" (AT4).
- `test_workflow_define.py:110`, `test_workflow_judged.py:174`, `test_workflow_verify.py:92` and `test_workflow_runner.py`: they drive the `Define`/`Verify` classes through pinned spike releases. Rewrite as per-primitive contract tests plus package fixtures.
- `test_define_*.py` (10 files), `define_support.py`, `define_replay_support.py` and `fixtures/define/`: move into `lab/stages/define/fixtures` and run them through `stage-check`. Delete `test_define_shim.py`, which runs package code on the host.
- `test_health_definition.py` (E1–E3; pins `unit_rules` at `:60`) and the W3–W9 cases in `test_health_evidence.py`: replace with the generic W and S families.
- `test_workflow_judging.py:62` (`question_set="dbv5-aspect/1"`): becomes a `jev-judge/1` contract test that reads `criteria/1`.
- With A8: `test_gate_effort_*.py` (7 files), `gate_effort_support.py`, `test_analysis_gate_effort.py` and `test_pre_fix_snapshot_grading.py`. In phase 6: `test_process_compliance*.py` (5 files).
- Delete `test_overhead_report_gate.py:276-279`, which pins the text of the Makefile `quality` recipe.
- Rename `stage_code` to `primitive_code` in the review fixtures: `tests/review_fixtures/projection/generate.py:359`, `trials.py:98,385`.

Docs:
- `README.md:5,18,50,56,165,228,240-249,256,266`.
- `AGENTS.md`/`CLAUDE.md:3-4,8-9`.
- `src/raidar/AGENTS.md:4,9,14,19`.
- `scoring/AGENTS.md:6` and `lab/environments/AGENTS.md:4`.
- The module docstrings of `stage.py`, `stages.py`, `factory.py` and `definition.py`, which are deleted with their modules.
- `docs/references/{new-harness,new-scenario,orchestration-flow,metrics}.md`, already flagged stale in `codereview.md` D02–D03: delete them, or mark them historical.

## 5. Phase placement (design §9)

| Item | Phase | Note |
|---|---|---|
| G1, G2 (`README:5,18`), G3, V1, V3 (target) | **Precondition** before phase 4; can land during phase 3 | No run or identity change, so no relock. Not a phase of its own |
| A4, A6 (record format), V2, V4, V5, V7, V8, V12, G5, G7, G8, contracts C1, C2, C4, C5, C6 | 4 | Phase 4 already deletes `stage_code`, so one relock covers everything |
| A5 + C3 | 4 | Moving adapter modules changes their paths, and today's identity hashes adapter paths (`stages.py:78-95`). Doing it with phase 4 avoids a separate relock |
| A1, A2, A3, V6, V11, the §4 test rewrites | 5 | AT1 and AT2 become the permanent V6 test |
| A6 (rows), A8 + C7, V9, G4 (`scoring:6`) | 6 | The design puts the gate-effort port here |
| V3 (package attack cases) | 4 and 5 | Required at the exit of both phases |

## 6. Open questions for the owner

1. Should agents get standing consent for `docs/reboot/split-design.md`? Where should the `stage/1` contract page live: `lab/stages/README.md`, outside `docs/`, or under `docs/`? AT1 says "following only the docs", which conflicts with `AGENTS.md:3-4`.
2. Which base ref should the append-only release check use, given this branch is never pushed? Should CI run on `lab-workflow-design`?
3. Should credential preflight and host auth join `NativeAgent`, or stay in engine code as a recorded exemption? Design §4 lists only six protocol members.
4. Does in-tree package tool code stay under ruff, mypy and the 95% coverage gate, or does it rely only on `stage-check` fixtures?
5. Is `make security-gate` a required phase-exit check like `perf-gate`, and on which Docker target?
6. Is the hook broker validity or a reading (D5, decided in phase 4)? This sets whether the G rows invalidate a trial.
7. Should gate-effort, `grading/prefix` and process-compliance be deleted at phase 5 (with verify) or at phase 6, as the design places them?
