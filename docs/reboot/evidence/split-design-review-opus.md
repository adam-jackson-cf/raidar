# Review: `docs/reboot/split-design.md`

## Verdict
**Sound with changes.** The boundary (agent + workflow as the arm, health as the only source of validity, grading in its own sandbox) holds up against both repos. The plan as written does not. If Phase 1 runs literally, it breaks the codex and claude-code adapters and imports used by the reaper, driver and collect, and it removes the gate and provisioning runtime that Phases 3 and 7 must then rebuild. Removing the prompt framing is likely to bring back pilot failure F1 (codex asks a question and writes nothing). Session resume, the liveness monitor, the Harbor phase timeout and where gate records are kept are each unsolved for the verify stage and the define + judge loop. The define-build-verify port also leaves out the parts that make FXL v2 what it was measured as.

## Findings

1. **Blocker.** Phase 1 deletes `subject/treatment/`, but code outside it imports that package. [OBSERVED]
   - `engine/health/reaper.py:34` imports `setup_script`.
   - `engine/trial/driver.py:46` and `materialise.py:35` import `provision`.
   - `engine/trial/agent_config.py:28-32` imports `session_chain` and `HARBOR_AGENT_NAMES`.
   - `engine/trial/collect.py:59-60` imports `integrity`.
   - Phase 3 also needs what Phase 1 deletes: the work/fix loop (`runtime/guardrail_gate.py:226-420`), root-owned `/opt/raidar-tools` provisioning (`catalog/tools.py:73`, `health/tool_tree.py:1-11`) and the inner-agent factory (`session_chain.create_inner`). Phase 7 needs the same provisioning.
   - **Fix:** Phase 1 removes only the catalogue, the component definitions and the schema. Move `provision`, `setup_script`, `session_chain`/`create_inner`, `HARBOR_AGENT_NAMES` and T1 into the workflow-runner package rather than deleting and rebuilding them.

2. **Blocker.** Deleting the "harbor" and "external" adapters deletes base classes. `HarborCliAdapter` is the base of the two adapters being kept. [OBSERVED]
   - Evidence: `adapters/codex_cli.py:10,13`, `claude_code_cli.py:5,8`; `design/experiment/agent_support.py:13-15,76-79` uses both base classes.
   - **Fix:** delete `gemini`, `cursor`, `copilot`, `pi` and `external_cli`; keep `harbor_cli`. Update `TYPECHECK_TARGETS` in `cli/quality.py:19-24`, which names `tests/test_gemini_cli_adapter.py`, or `make quality` fails.

3. **Major.** Removing the framing is a known way to get no work done.
   - The forge experiment records that "without [prompt-wrapper] vanilla codex sees an empty /app, asks a question and writes nothing (pilot F1; observed again…)" (`lab/experiments/smoke/python__forge__mechanics/experiment.yaml:5-7`). [OBSERVED]
   - Admission refuses any smoke where a step left `/app` unchanged (`engine/health/admission.py:6-9`). [OBSERVED]
   - FXL does not use the bare task: its one-shot v1 prompt is "Work the issue below per the repository's own rules.\n\n%%%TASK%%%" (`workflow/one-shot/v1.toml`). FXL also seeds `pyproject.toml` with the problem's dependencies and runs `uv sync` before checkpoint 1 (`evaluation/benchmark/scb.py:385-423`). [OBSERVED]
   - **Fix:** port the FXL one-shot prompt and the workspace seeding as lab-owned trial setup. The Phase 3 exit must also pass admission's `/app`-changed rule.

4. **Major.** Session resume conflicts with the `GuardrailGateAgent` pattern, and no phase proves resume.
   - Harbor's codex resume needs `EnvironmentPaths.agent_dir/sessions` from the previous run (`harbor/agents/installed/codex.py:1356,1415-1421,1457-1464`). [OBSERVED]
   - `GuardrailGateAgent._settle` moves everything under the logs dir into a per-session folder, and `_inner()` builds a new agent per session. Today's fix sessions are fresh, not resumed (`guardrail_gate.py:19-21`). [OBSERVED]
   - Resumed codex rollouts carry the whole history, so per-turn token counts would be cumulative. [INFERENCE]
   - **Fix:** make a resume spike part of the Phase 3 exit: codex and claude-code each run a build turn plus a resumed turn under Harbor, with the same session id and per-turn token deltas recorded.

5. **Major.** "Liveness monitor unchanged" will kill verify and define-build-verify runs.
   - The monitor counts the phase as idle when, for 600 s, there is no output, no CPU from processes owned by the agent user, and no container network traffic. Enforce mode is the default and kills the agent (`engine/health/liveness.py:6-13`). [OBSERVED]
   - Host-side Jev passes make one request per aspect, with a 60 s timeout and 3 retries (FXL `define-build-verify/v2.toml` `[judge]`). Gate work run by root also leaves the agent user's CPU quiet. [INFERENCE]
   - **Fix:** the runner marks judge and gate windows to the monitor, or liveness covers agent turns only; register `judge` and `gate` limits in the L registry.

6. **Major.** The workflow's budget does not fit inside Harbor's per-checkpoint agent timeout.
   - Raidar caps each checkpoint's agent phase (`agent_timeout_sec: 1800` in the forge experiment). All passes run inside that one phase, up to 20 in define-build-verify. [OBSERVED]
   - FXL ran `task = "unbounded"`, and its `fit_rework` reads the time left. Experiment B was measured under those terms. [OBSERVED]
   - A cancel in the middle of a gate run is unspecified. [INFERENCE]
   - **Fix:** the runner reads the phase deadline and passes it to `fit_rework` and the fix-round check. Add a recorded `deadline` ending and its health row. Note that FXL results do not transfer under a capped budget.

7. **Major.** Gate, define and judge state sits inside the agent's container. §6 drops FXL's T4 by citing only the hidden tests.
   - FXL T4 also protects the run's records and the evaluator's environment (`docs/references/run-health.md:92`), and FXL keeps gate, define and judge state outside the workspace (`engine/lifecycle/runner.py:100-108`). [OBSERVED]
   - In Raidar the hook runs as the agent user on the agent's own `git commit`, so `gate-<n>.json` and `hook-runs.jsonl` are files the agent can write. [INFERENCE]
   - **Fix:** treat the engine's root-run seal as authoritative. Copy and hash hook records on the host after each turn. Port FXL's G1–G3 record-agreement checks, and keep a T4-like check over gate and define state paths.

8. **Major.** The health table leaves out FXL's workflow checks and silently reuses Raidar's existing letters.
   - FXL validates verifying runs with E1–E6 (fix turns, endings, structured-reply repairs, definition present), J1–J3 (judge) and G1–G6 (record agreement) (`run-health.md:60-75`). The design's G family covers tools and bypass only. [OBSERVED]
   - Raidar's existing L1/L2 (liveness) and P2/P3 (concurrency and comparison warnings) are given new meanings with no home for the old checks (`engine/health/signals.py:1-5`, `audit.py:15-17`). [OBSERVED]
   - **Fix:** add E and J families with outcome-table rows in Phases 7 and 8. Map or renumber L1/L2/P2/P3 explicitly.

9. **Major.** `define-build-verify@1` as written is not a port of FXL v2.
   - **Skill delivery:** FXL places all four skills in the workspace for the whole run (`engine/lifecycle/workspace.py:215-226`). The design delivers them per stage, which is a different treatment from the one measured. [OBSERVED]
   - **Structured replies:** build and rework turns must return `account`, `ac_tests` and `adrs`, parsed from the final text with repairs (`v2.toml` build and rework prompts; `structured_output = "prompted"` in `config/harness/codex/v2.toml`). The judge depends on `ac_tests`. [OBSERVED]
   - **Missing settings and prompts:** the tokens `%%%DEFINITION%%%`, `%%%PUSHBACK%%%`, `%%%GATE%%%`, `%%%MODE%%%` and `%%%PRIOR%%%`; the `rework` and `summary` prompts; and `max_refinement_rounds`, `closed_at`, `open_at`, `settle_after`, `fit_rework` and `regression_needs_baseline`. [OBSERVED]
   - **Fix:** write a full v2 field and token map, like §5.2 does for verify. Either deliver skills for the whole run or call the result a new version rather than a port.

10. **Major.** Workflow files land in the graded snapshot.
    - Skills (`.agents/skills`), the rules seeded into `AGENTS.md`, `.raidar/decisions.md` and the definition all go into `/app`. `SNAPSHOT_EXCLUDES` drops only caches and `.git` (`contract/snapshot.py:19-30`). [OBSERVED]
    - code-quality and diff metrics then differ by arm, and the gate's `git add -A` commits these files. [INFERENCE]
    - **Fix:** exclude workflow-owned paths from the snapshot, commits and scoring, and record their digests.

11. **Major.** The provider-failure rule contradicts the judge and biases exclusions.
    - "No retry" conflicts with FXL's judge `retries = 3`, which the port carries. [OBSERVED]
    - Exclusion odds grow with the number of calls. Experiment B shows define-build-verify costing about 3× the output tokens of one-shot-verify (FXL `docs/experiments/ledger.md`), so heavier arms will be excluded more often under the 20% cap (`design/experiment/schema.py:198`). [INFERENCE]
    - **Fix:** handle Jev failures separately (an undecided criterion, not an infrastructure failure). Report exclusions per arm and refuse analysis when they differ by arm.

12. **Major.** Phase 5 provides no tools for the stage types.
    - The `define` tool runs on the engine's own interpreter and code (`runner.py:116-135`). The gate needs the pinned pack environment (`capability/verify/packs/python/requirements-tools.lock`). [OBSERVED]
    - Phase 5 keys only a CLI layer, and keys it on the agent definition. [OBSERVED]
    - **Fix:** add a stage-tool layer keyed by stage code digest. Include it in the §5.4 identity and in the G-family tool-tree baseline.

13. **Major.** Admission ignores the workflow.
    - The admission identity is the agent's name, model and effort only (`admission.py:3-5`), so a clean one-shot smoke would admit a define-build-verify benchmark. [OBSERVED]
    - **Fix:** add the workflow release and stage digests to the admission identity in Phase 3.

14. **Major.** Re-imports invalidate pinned errata and the committed experiment locks, which `make quality` checks.
    - The forge errata pin `manifest_sha256` (`lab/problems/registry/scb/forge/38d627ecf668.errata.yaml:6`), and the lock pins the errata file. Phase 2 changes the manifest, and Phase 5 changes it again. [OBSERVED]
    - `experiment-locks-check` is part of `make quality` (`Makefile:438-440,687`). [OBSERVED]
    - **Fix:** Phases 2 and 5 re-pin errata and sensitivities, re-validate, and relock every experiment in the same commit.

15. **Minor.** The Phase 1 deletion reaches further than its list. All of these depend on the deleted scorers or components: [OBSERVED]
    - `evaluation/analysis/` `manipulation.py`, `assessed.py`, `report.py`, `ranking.py`, `markdown.py`, `load.py`
    - `scoring/catalog/checks.py`
    - `evaluation/review/quality/gate_view.py`
    - `grading/prefix/*` and `PRE_FIX_GRADE_SCORERS` (`grading/offline.py:39`)
    - `manipulation_checks` in the experiment schema (`schema.py:211-238`)
    - the `component` resolver entry point (`orchestrator/pyproject.toml:51`)
    - import-linter contracts (`orchestrator/.importlinter:40-52,106-113`)
    - `component_hosts` in `engine/trial/network.py`
    - the `component_probes` validity check (`collect.py:352`)
    - `evaluation/review/projection/conditions.py`
    - the factor guard (`factors: [agent]`).

16. **Minor.** Jev in Raidar is parked and built for offline use.
    - Jev is parked (`Makefile:220-221`). The client caches and replays from the assessment store (`scoring/judge/session.py:4-8`). The pinned model is `typesafe/jev-1.13-20260917` (`protocol.py:23`), while FXL names `typesafe/jev-1.13`. [OBSERVED]
    - **Fix:** add a live mode that records raw responses, and pin one model id.

17. **Minor.** §5.4 does not define what the stage-code digest covers.
    - Pooling already refuses `execution_code` differences, but the refusal can be overridden (`evaluation/analysis/pooling.py:31-41`). [OBSERVED]
    - **Fix:** define the digest's scope (stage code, gate pack, `enumerated.py`, Jev client, adapter) and say whether it can be overridden.

18. **Minor.** Two sources for agent versions during Phases 3–5.
    - Agent pins in `lab/agents/*.yaml` duplicate the `pack.yaml` pins that RT5 enforces (`lab/environments/python/pack.yaml`). [OBSERVED]
    - The value substituted for `entry_command` is unspecified. FXL uses `python {entry}.py` (`scb.py:178-182`); Raidar's pack has only `entry_file_template`. [OBSERVED]

19. **Minor.** Limits move away from FXL's measured settings.
    - FXL's idle limits are workflow-definition settings (`docs/references/time-limits.md:33-43`), with `idle.stage = 180` against Raidar's liveness default of 600. [OBSERVED]
    - `timeout_per_test` is FXL's gate-policy rule `rule.test`, not a stage setting. [OBSERVED]
    - Stating FXL as the source does not make the settings match what was measured.

## Wrong facts
- **Adapter count:** the design says "seven adapters" but names six. Two of them (`harbor`, `external`) are base classes, and `harbor` is the base of the kept adapters (finding 2). [OBSERVED]
- **"`one-shot`'s build prompt is `%%%TASK%%%` alone", presented as following FXL:** FXL's one-shot v1 has a framing line, and FXL also seeds the workspace (finding 3). [OBSERVED]
- **The codex agent "taken from FXL":** both FXL codex v1 and v2 pin `0.162.1` (`config/harness/codex/v1.toml:8`). v2 has five `features_off` entries plus a version-specific `bundled_skills` list, which isolation checks rely on. `0.156.1` is Raidar's pack pin. Raidar's adapter allows only `low` reasoning for `gpt-6-luna` (`adapters/codex_cli.py:24`). [OBSERVED]
- **"T4 is not needed because hidden tests never enter the container":** FXL's T4 also covers records and the evaluator's environment (finding 7). [OBSERVED]
- **The `GuardrailGateAgent` pattern:** its fix sessions are fresh, so it supplies no resume (finding 4). [OBSERVED]
- **Citations:** `sequential.py` is at `evaluation/sequential.py`, not under `evaluation/benchmark/`. The Wilson interval is at `evaluation/analysis/stats.py:35`. [OBSERVED]

## Missing plan items
- **Phase 1 runs experiments as `one-shot` arms before `one-shot` exists (Phase 3).** Either merge Phases 1 and 3, or keep the old condition path running until Phase 3.
- **Session-resume proof** (finding 4) has no phase or exit criterion; the design only says it must happen "before phase 7".
- **The Phase 7 exit ("a refusal produces a fix turn") cannot be guaranteed on a real model.** Add a seeded refusal (a canary workspace with a failing test) and a fake-agent integration test. Prove that bypass detection and tool-tree integrity fire.
- **The Phase 8 exit does not prove the port is faithful.** Add replays against FXL fixtures: unit numbering, unit rules 2, `define` checks, and judge-input identity (FXL has `tests/test_define_compact_replay.py`).
- **Phase 6 ordering:** capture ledger occurrence excerpts before `var/` is cleared. FCR evidence paths point into `var/` (`lab/ledger/faults/FCR-001.md` cites `jobs/pilot-v2/...` and `problems/store/...`); afterwards the runs read `gone`.
- **`lab/releases.yaml` arrives only in Phase 6**, while agents and workflows are released in Phase 3. Edits between those phases go unchecked.
- **Solvable sets:** no plan for generating them in Raidar's grader sandbox (FXL takes them from `benchmark/solvable`), or for how they relate to Raidar's errata exclusions.
- **Health-check port:** no port of FXL's P6 (key and proxy variable names never reach an untrusted environment's records) or P8 (the agent saw only its workflow's skills, using a per-version `bundled_skills` list).
- **`make quality` along the way:** coverage must stay at 95% (`cli/quality.py:26`) and import-linter contracts must be rewritten in each phase that moves code. Neither is budgeted.
