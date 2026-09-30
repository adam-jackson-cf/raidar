# A′ spike results (plan task 0.3)

> **Provenance.** Copied from `spikes/a-prime/RESULTS.md` at the Phase 1 cutover. The spike code it names (`build_task.py`, `run_trial.py`, `raidar_spike/`) is retired from the tree; read it at commit `2b66568:spikes/a-prime/` (the last commit with `spikes/`). Paths such as `_work/` are relative to that spike directory; raw `_work/` evidence was never tracked.

**Verdict: A′ passes all four checks.** One Harbor 0.23.0 `[[steps]]` trial holds one environment for all checkpoints. A fresh agent process runs per step, and a custom host-side `BaseVerifier` grades each step on a downloaded copy of `/app`, in a separate container. Fallbacks E (in-agent loop) and B (one trial per checkpoint) were not needed, so they were not evaluated.

- Date: 2026-09-29.
- Harbor: 0.23.0, from the orchestrator venv, driven through the Python API (`Trial.create`).
- Container runtime: OrbStack (`DOCKER_CONTEXT=orbstack`, with the data dir on the external drive: `external_available=True`).

## Substitution vs. the plan

The plan names the Claude Code agent. Under the user's model policy, **every real-LLM trial used `codex-cli` with `gpt-6-luna` at reasoning `low`**. The Codex session rollout confirms this: `"model":"gpt-6-luna"`, `"effort":"low"`, cli `0.156.1`.

Because Claude Code is the Phase 1 harness:
- the Claude state wipe (`CLAUDE_CONFIG_DIR`, `~/.claude`, `~/.claude.json`) is implemented in `setup.sh`;
- it was **unit-checked only**. No Claude trial was run.

The no-LLM checks (halt/continue, the negative control, the 8-checkpoint runs) used a spike reference agent that installs SCB reference payloads from the host-only bundle.

## Problem

- **Problem:** SCB `recli`, 8 checkpoints, converted with `scb-problems/scripts/scb_to_harbor.py --org scb`.
- **Converter behaviour on Harbor 0.23:** generation succeeded, but the converter's post-generation build smoke calls the removed `harbor task check` command (0.23 wants `harbor check`).
- **Build step:** `build_task.py` turns the converted task into two parts:
  - `_work/tasks/<name>/`: what Harbor sees.
    - Instructions and `environment/Dockerfile`, with codex 0.156.1 and an empty harness baseline in `/opt/raidar/harness-baseline` added.
    - A per-step `workdir/setup.sh`.
    - A **stub** `tests/test.sh`: Harbor 0.23's `Task._validate_tests` requires a test script for shared-mode steps even when a custom verifier is used. The stub is never uploaded.
    - No tests and no solutions.
  - `_work/oracle/<name>/`: the host-only hidden bundle.
    - SCB tests, per-step `test.sh` and reference payloads.
    - The oracle Dockerfile.
    - `instruction_index.json` and `leak_tokens.json` (24 distinctive test names).
- **Static assertion:** the builder asserts that no oracle file or token exists in the task dir. It was clean on every build.

## Mechanism

```text
Harbor MultiStepTrial (1 container, agent user uid 1000)
  per step: upload steps/<k>/workdir → /app ; bash /app/setup.sh   (Raidar reset: kill uid-1000 leftovers,
            reset CODEX_HOME/~/.codex/CLAUDE_CONFIG_DIR/~/.claude/~/.claude.json to baseline, self-delete)
          → agent run (fresh `codex exec` process; probe first)      → step artifacts: /app → steps/<k>/artifacts/app
          → RaidarOracleVerifier (host process, verifier.import_path):
               leak scan of agent env (root) → download_dir_with_exclusions(/app) → copy + hidden tests
               → `docker --context orbstack run --rm <oracle image> bash /tests/test.sh` → reward.json
          → min_reward check (halt) or continue
```

## Evidence

All trial directories are under `_work/trials/<trial>`. Per-run JSON (timing plus checks) is under `_work/results/<trial>.json`.

| Trial | Agent | Steps run | strict_pass_rate per step | C1 | C2 | C3 |
|---|---|---|---|---|---|---|
| `codex-gpt6luna-low-3step` | codex-cli gpt-6-luna low (real) | 3/3 | 0.971, 0.944, 0.829 | ✅ | ✅ | ✅ |
| `ref-8-continue-skip3` | reference, skip ckpt 3 | 8/8 | 1, 1, **0.8**, 1, 1, 1, 1, 1 | ✅ | ✅ | ✅ |
| `ref-8-halt-skip3` (`min_reward={strict_pass_rate=1.0}`) | reference, skip ckpt 3 | **3/8 (halted)** | 1, 1, **0.8** | ✅ | ✅ | ✅ |
| `negative-control-leak` | reference, leaks 1 hidden test file | 1/1 | 0 (forced), `oracle_leak=1` | ❌ as designed | n/a | ✅ |
| `recli-2-continue-…180727`, `post-format-regression` | reference | 2/2 | 1, 1 | ✅ | ✅ | ✅ |

**Check 1: host-side verifier, and no tests in the agent environment.**
- Every step's `verifier/raidar_verifier.json` records:
  - a separate `raidar-oracle-*` container, run with `--rm` on a copy of the snapshot;
  - a root-level leak scan of the agent env that was `clean: true`. The scan checks test filenames, `/tests` contents, and the 24 test-name tokens.
- The negative control proves the scan is not vacuous:
  - A planted `/tmp/negative-control/test_checkpoint_1.py` was detected by both filename and token.
  - The verifier forced the reward to 0 and set `oracle_leak=1`.
- Harbor never uploads tests in this mode: the custom verifier is the only code that touches tests, and it only reads them host-side.

**Check 2: context isolation.**
- **Real Codex session state existed and was wiped.** After step 1 of the codex trial, `CODEX_HOME` (`$HOME/.codex`, outside Harbor's `/logs/agent` management, run without `--ephemeral`) held:
  - `sessions/…/rollout-…-01a0ee6b-….jsonl`;
  - `memories_1.sqlite` and `logs_2.sqlite` (copies are under `steps/checkpoint_1/agent/codex-home-snapshot`, with auth.json removed).
- **Step 2 could not find step-1 history.** The step-2 probe runs as the agent user after `setup.sh` and greps the whole filesystem, excluding system dirs. It searched for:
  - the step-1 Codex **thread id** (the id of the real transcript and session);
  - a planted sentinel written into `CODEX_HOME` sessions, history and memories, `CLAUDE_CONFIG_DIR/projects`, `~/.claude/projects` and `~/.claude.json`.

  Both returned **0 hits**, and the harness state dirs were empty.
- **The planted process was killed.** Step 1 ended by planting `setsid` `raidar-planted-sentinel-checkpoint_1`, which was alive at the end of step 1 (`raidar-plant.json`).
  - At step 2, `raidar-reset.json` lists it under `killed`, and `residual` is `[]`.
  - The probe shows it absent.
  - The same result holds in every multi-step trial.
- **Unit checks** (`tests/test_step_setup.py`, 2 passed):
  - Wipe path on the host, with Codex and Claude dirs restored to the baseline contents exactly and `~/.claude.json` removed.
  - Kill path in a container. Agent-uid leftovers, including an orphaned `setsid` process, are killed; a root process survives; `residual` is empty.

**Check 3: per-step `/app` artifacts land on the host.** `steps/<k>/artifacts/app` exists for every executed step. Its content digest equals the digest of the snapshot the oracle graded (`digest_matches_graded_snapshot: true`).

**Check 4: both progression modes.**
- **Halt:** with `min_reward={strict_pass_rate=1.0}`, the trial stopped after failing checkpoint 3, and steps 4–8 were never run.
- **Continue:** with no `min_reward`, all 8 steps ran, checkpoint 3 failed, and checkpoints 4–8 passed.
- **Strict is derivable from continue:** the halt run's executed prefix matches the continue run's first 3 steps (1, 1, 0.8).

## Timing (OrbStack, M-series macOS, seconds)

Numbers come from `run_trial.py`'s JSON (Harbor `TimingInfo` plus Raidar phases).

**Trial-level phases:**

| Phase | Cold (first build) | Warm |
|---|---|---|
| `environment_setup`: Harbor env build and start | 208.7 | 9.2–33.2 (median ≈ 14) |
| Oracle image build (spike-managed) | 4.8 | 0 (cached) |
| Trial teardown and `result.json` write | 12.9 | 12.2–13.5 |

**Per-step phases, warm, across all steps of all runs:**

| Phase | Range |
|---|---|
| Step prepare: workdir upload, `setup.sh`, healthcheck | 1.3–2.7 |
| `setup.sh` reset itself | 0.23–0.26 |
| Artifact collection: `/app` → host | 2.3–6.0 |
| Snapshot download in the verifier | 1.0–1.8 |
| Verifier plumbing, excluding the oracle run and the spike-only leak scan | 1.7–2.9 |
| Leak scan (spike assertion, not a production phase) | 1.1–1.8 |
| Step archive gap | ≈1.3 |

**Problem-dependent phases:**
- Oracle run: from 7–26 s (checkpoints 1–3) up to 186 s (checkpoint 8). It is cumulative because the prior checkpoints' tests re-run each step, plus `scb-check` via `uvx`.
- Real agent (gpt-6-luna low): 81, 118 and 112 s per step.

**Framework overhead per step:**
- About 7–9 s warm, excluding agent and oracle time.
- That breaks down as prepare ≈1.3, artifact collection ≈2.4, snapshot ≈1.0, plumbing ≈1.7 and archive ≈1.3.

**For task 0.4:** `run_trial.py` is the reusable harness. Each run writes `_work/results/<trial>.json` containing:
- `harbor_timing_info`: trial, `environment_setup`, `agent_setup`, and per-step `agent_execution`/`verifier`;
- `raidar_phases`: the per-step breakdown above, plus teardown;
- `hook_events`: Harbor `TrialEvent` timestamps;
- `checks`.

`report.timings()` can re-derive the timings for any finished trial. Useful options for 0.4:
- `--snapshot-source artifact` grades the Harbor artifact copy instead of re-downloading `/app`. It is implemented but not yet measured, and would remove one transfer per step.
- `--delete` gives cold-ish runs.

## Findings for Phase 1 / ADR

1. **Harbor requires a test script for shared-mode steps**, even with `verifier.import_path`. A stub `tests/test.sh` is needed; the B1 importer must emit it.
2. **`verifier.import_path` and its kwargs are trial-level config** (`TrialConfig.verifier`), not `task.toml`. The execution layer (WS-D) must inject them, which means the verifier is a Raidar runtime component, not a task property.
3. **Per-step `setup.sh` lives in `steps/<k>/workdir/`, is uploaded into `/app`, and runs as the step's agent user.** It must self-delete, and snapshots must exclude it (done here).
4. **Claude vs Codex state:**
   - Harbor's docker env is "mounted": after each step, Harbor *moves* `/logs/agent` contents to the host. So a harness state dir under `/logs/agent` (Raidar's current `CLAUDE_CONFIG_DIR=/logs/agent/sessions`, and `CODEX_HOME=/logs/agent/codex-home`) is effectively wiped by Harbor.
   - `$HOME/.codex`, `~/.claude` and `~/.claude.json` are not managed by Harbor. The `setup.sh` reset is required for them, and was proven with real Codex state.
5. **The secret upload breaks under a non-root agent user.** `docker cp` creates root-owned files that keep the source mode (0600), so `CodexCliHarborAgent`'s `_upload_secret_file` → `chmod 600` produces a file the uid-1000 agent cannot read.
   - The spike's `SpikeCodexAgent` stages `auth.json` as root and does `install -o agent -m 600`.
   - The Phase 1 C3/WS-C port must do the same.
6. **Leftover processes survive between steps**, as predicted (Harbor PR #3454 is still open). A `setsid` orphan reparented to PID 1 survived until `setup.sh` killed it.
   - The kill must protect this script's own ancestors and descendants, not "anything whose parent is PID 1". The first spike iteration made that mistake and missed the orphan; this was fixed and unit-tested.
7. **The oracle container currently uses `--network bridge`**, plus a shared uv cache volume, because the SCB `test.sh` resolves pytest and plugins with `uv run --with` and `uvx scb-check`.
   - N2 wants network-less grading.
   - The Python pack (WS-I) should prebuild the oracle image with those tools so the run can use `--network none`.
8. **Harbor names images per trial** (`<trial_name>__env-main`), with a distinct image ID per trial but shared layers.
   - `delete=False` keeps them, so they accumulate. Cleanup is needed in the execution layer.
   - Warm env setup was still about 9–33 s. The 208 s cold figure is dominated by the apt and npm codex install layer, which prebuilt pack images would remove.
9. **The spike's leak scan passes test names on a root grep command line** (briefly visible in `/proc`) while no agent process is running. A production leak assertion, if kept, should scan host-side or use hashed tokens.
10. **Converter `min_reward` defaults to `{core_pass_rate=0, strict_pass_rate=0}`**, which is effectively continue. SCB strict halt is `strict_pass_rate=1.0`.

## Reproduce

```bash
cd spikes/a-prime; PY=../../orchestrator/.venv/bin/python; export DOCKER_CONTEXT=orbstack
(cd /path/to/scb-problems && uv run scripts/scb_to_harbor.py --org scb --out $PWD/_work/converted recli)  # generation; build-smoke step fails on 0.23 (see above)
$PY build_task.py --converted _work/converted/scb/recli --name recli-8-continue
$PY build_task.py --converted _work/converted/scb/recli --name recli-8-halt --progression halt
$PY build_task.py --converted _work/converted/scb/recli --name recli-3-codex --steps 3 --agent-timeout 600
$PY run_trial.py --task recli-8-halt     --agent reference --skip-steps checkpoint_3
$PY run_trial.py --task recli-8-continue --agent reference --skip-steps checkpoint_3
$PY run_trial.py --task recli-3-codex    --agent codex --model openai/gpt-6-luna --reasoning low
$PY -m pytest tests -q
```

Codex auth comes from the host's existing Codex OAuth/session `auth.json`, resolved via `raidar.codex_auth` and passed by path only. A check found no `auth.json` or token material in `_work/`.

## Files

- `build_task.py`: builds the task and the hidden oracle bundle, with the static isolation assertion.
- `run_trial.py`: runs one trial through the Harbor API and writes the timing and checks JSON. This is the reusable harness for 0.4 and 0.5.
- `raidar_spike/`:
  - `verifier.py`: `RaidarOracleVerifier`.
  - `agents.py`: `SpikeCodexAgent` and `ReferenceSolutionAgent`, with probe and plant.
  - `step_setup.sh`: the per-step reset.
  - `report.py`: timings and checks.
- `tests/test_step_setup.py`: the wipe and kill unit checks.
- `_work/`: generated tasks, trials and results (git-ignored).
