# Overhead baseline and proposed N1 budgets (plan task 0.4)

**Status: budgets PROPOSED, not yet accepted.** Requirements N1 and the Phase 0 exit criteria need the user to accept (or amend) §5 before they become enforced numbers for `raidar perf check` (plan D4).

- Date: 2026-09-29.
- Harness: `spikes/overhead/` (`bench.py`, `summarize.py`), built on the A′ spike harness in `spikes/a-prime/` (task 0.3), which it reuses without editing.
- Raw evidence: `spikes/overhead/baseline-summary.json` (tracked) and per-rep JSON under `spikes/overhead/_work/results/<cell>/` (gitignored).

## 1. What was measured

**Trial shape.** One Harbor 0.23.0 `[[steps]]` trial (the A′ mechanism). SCB `recli`, checkpoints 1–2, continue mode. Per trial there is one environment. Per step:
- Harbor uploads the step workdir and runs the Raidar `setup.sh` reset;
- the agent runs;
- Harbor collects `/app` to the host;
- the custom host-side verifier snapshots `/app` and grades it in a separate oracle container.

**Agent.** The spike's no-LLM `ReferenceSolutionAgent` (subclassed as `OverheadReferenceAgent`) installs the checkpoint's reference payload. The plan suggested Harbor's built-in `oracle`/`nop` agents. The spike agent was used instead because it also has no LLM, and it keeps reference solutions host-side, as the A′ design requires; Harbor's `oracle` agent needs `solution/` inside the task. No real-LLM trial was run for this task. Agent execution and oracle execution are recorded but **excluded from budgets** (N1).

**Cells.** 10 reps each, plus one discarded harness-smoke rep 0 per cell. All 40 counted reps passed every checkpoint (`strict_pass_rate=1.0`), with no exceptions.

| Cell | Definition |
|---|---|
| `cold` | The env image is absent: `delete=True` removes it after each rep. The BuildKit layer cache is busted by a unique `RUN` directly after `FROM`, so every layer rebuilds, including apt and the npm codex install. The oracle image is removed and rebuilt with `--no-cache`. Base images (`ghcr.io/astral-sh/uv:python3.12-trixie-slim`) stay local, because a registry pull is network cost, not framework cost. |
| `warm` | Layers are cached, and the verifier downloads the `/app` snapshot from the env. This is the current A′ default. |
| `warm-artifact` | As `warm`, but the verifier grades Harbor's own host copy of `/app` (`snapshot_source=artifact`) instead of downloading it again. |
| `warm-init` | As `warm`, plus a task `environment/docker-compose.yaml` override with `services.main.init: true` (see §4, finding 1). |

**Raidar phases that do not exist yet are stubbed** with the closest spike equivalent. Each stub is labelled `[STUB]` in the table.

| Raidar phase | What the stub measures | Location |
|---|---|---|
| Component apply | Uploads a representative component bundle and installs it as root into the harness baseline dir that the per-step reset restores from. The bundle is `spikes/overhead/components/fixture`: AGENTS.md, config.toml and 3 skills, 11 files, 47 KB. It runs in `agent.setup`, so it is inside Harbor's `agent_setup` phase. **This install-into-a-baseline-dir model is the Phase 0 stub only; the WS-C implementation does not work this way (see §8)** | `overhead_spike/components.py` |
| Per-step reset | Real. This is the A′ `setup.sh`, plus Harbor's workdir upload and healthcheck around it | a-prime |
| Snapshot download | Real. The verifier's `/app` download, or the artifact copy | a-prime verifier |
| Verifier plumbing | Real. Verifier wall time minus oracle run minus snapshot transfer: Harbor verifier dispatch, test staging and `reward.json` handling. The spike-only leak scan is disabled by `OverheadVerifier`; in 0.3 it cost about 1–10 s | a-prime verifier |
| Scorer orchestration | Per step: one no-op `docker run --rm --network none` container on the step's artifact copy, plus a host-side digest and a score-record write. This is the mechanical cost of one containerised scorer doing no scoring work | `bench.py` |
| Result write | Harbor teardown plus `result.json` (real), and Raidar's rep-JSON write (stub) | a-prime report / `bench.py` |

**Environment.**
- Apple M1 Max (10 cores, 32 GB), macOS 15.7.1.
- **OrbStack 2.2.3 only** (Docker 29.4.0, Compose v5.1.2; the VM has 10 CPU and 15.7 GiB), with its data dir on the UGreen external drive (`external_available=True`).
- Harbor 0.23.0 via the Python API, on orchestrator venv Python 3.13.7.

## 2. Results (seconds; p50 / p95, n = samples)

Percentiles use linear interpolation between order statistics. With n=10 the p95 is close to the maximum. Per-step phases pool both checkpoints, so n=20.

| Phase | Scope | cold | warm | warm-artifact | warm-init |
|---|---|---|---|---|---|
| Environment start (Harbor `environment_setup`: build or cache check, then `up`) | trial | 239.98 / 339.10 (10) | 11.15 / 15.99 (10) | 11.15 / 28.49 (10) | 8.07 / 12.17 (10) |
| Oracle image build (spike-managed; cold only) | trial | 90.60 / 140.40 (10) | — | — | — |
| Component apply [STUB] | trial | 1.04 / 1.24 (10) | 1.02 / 1.11 (10) | 1.03 / 2.11 (10) | 1.01 / 1.18 (10) |
| Per-step reset (workdir upload + `setup.sh` + healthcheck) | step | 1.27 / 1.31 (20) | 1.26 / 1.36 (20) | 1.27 / 1.73 (20) | 1.27 / 1.33 (20) |
|   of which the `setup.sh` reset itself | step | 0.23 / 0.25 (20) | 0.23 / 0.24 (20) | 0.23 / 0.26 (20) | 0.23 / 0.24 (20) |
| Artifact collection (`/app` → host, Harbor) | step | 2.65 / 2.91 (20) | 2.55 / 2.71 (20) | 2.64 / 2.87 (20) | 2.50 / 2.70 (20) |
| Snapshot transfer (verifier) | step | 1.01 / 1.10 (20) | 1.00 / 1.36 (20) | **0.00 / 0.00** (20) | 1.00 / 1.02 (20) |
| Verifier plumbing | step | 0.70 / 0.73 (20) | 0.70 / 0.72 (20) | 0.70 / 1.10 (20) | 0.68 / 0.70 (20) |
| Step archive gap (step outputs → `steps/<k>/`) | step | 1.27 / 1.33 (10) | 1.26 / 1.33 (10) | 1.28 / 1.93 (10) | 1.28 / 1.47 (10) |
| Scorer orchestration [STUB] | step | 1.64 / 4.44 (20) | 1.46 / 5.40 (20) | 2.35 / 8.71 (20) | 1.54 / 3.45 (20) |
| Teardown + Harbor `result.json` (`compose down`) | trial | 12.60 / 13.27 (10) | 12.70 / 13.63 (10) | 13.03 / 13.70 (10) | **2.10 / 2.84** (10) |
| Raidar result write [STUB] | trial | 0.00 / 0.00 (10) | 0.00 / 0.00 (10) | 0.00 / 0.00 (10) | 0.00 / 0.00 (10) |
| **Framework overhead per 2-step trial** (total − agent − oracle + scorer stub) | trial | 268.64 / 369.69 (10) | 40.21 / 48.50 (10) | 39.33 / 70.69 (10) | 26.87 / 29.45 (10) |
| (excluded) agent execution, reference agent | step | 1.71 / 1.91 (20) | 1.69 / 1.74 (20) | 1.71 / 2.62 (20) | 1.69 / 2.11 (20) |
| (excluded) oracle run, SCB tests | step | 13.64 / 16.67 (20) | 15.67 / 21.30 (20) | 15.14 / 20.20 (20) | 11.62 / 16.22 (20) |

**Derived fixed costs (p50, warm).** These are arithmetic on the rows above.
- **Per step:** reset 1.26 + collection 2.55 + snapshot 1.00 + plumbing 0.70 + archive 1.26 ≈ **6.8 s**.
  - About 5.8 s with artifact-source snapshots.
  - Plus about 1.5 s per containerised scorer.
- **Per trial:** env start 11.2 + component apply 1.0 + teardown 12.7 ≈ **24.9 s**.
  - About 11.2 s with `init: true`.

## 3. Deviations and caveats

1. **No Docker Desktop cell.** The plan asks for Docker Desktop and OrbStack. The user directive is OrbStack only, with data on the UGreen drive, so only OrbStack was measured. The budgets below are OrbStack budgets. If Docker Desktop is ever used, it needs its own baseline.
2. **Concurrent load.** Other Phase 0 agents shared the OrbStack VM during the runs:
   - task 0.5's `slop-code-exec-*` containers came and went;
   - signoz ran throughout;
   - 4–7 containers were running at each rep start, and the host load average was about 6–8.

   The p95 tails are therefore conservative, meaning they are inflated by contention. The outliers are warm-artifact env start at 28.5 s and the scorer stub at 8.7 s, both in reps with 7 running containers. The lower warm-init env start (8.1 vs 11.2 s) coincides with lower load (4 containers). This is an inference: `init` should not affect start time.
3. **Small `/app`.** The recli payload is 1 file of 20–40 KB, so the transfer phases (collection, snapshot, archive) are latency-bound.
   - 0.3's real Codex steps showed artifact collection of 2.3–6.0 s.
   - These budgets need a size term, re-baselined on real-agent `/app` trees before `raidar perf check` gates on them (§6).
4. **Cold is mostly the network, not the framework.**
   - Cold env start (p50 240 s) is dominated by the Dockerfile's apt and `npm install -g @openai/codex` layers.
   - The cold oracle-image build (p50 91 s) is dominated by apt and uv downloads.
   - Both vary with network conditions: rep 1 took 346 s and rep 10 took 194 s.
5. **Stubbed phases** are labelled. Their budgets are provisional until the real components exist (WS-C component apply, the scorer workstream).
6. **Excluded from budgets:** agent execution and oracle run (N1). Base-image registry pulls, which are network.

## 4. Findings that change the budgets

1. **About 10 s of every teardown is a Harbor default, and it is avoidable.**
   - Harbor's compose file runs the main service as `sh -c "sleep infinity"` as PID 1. That ignores SIGTERM, so `docker compose down` waits the full 10 s stop grace before SIGKILL.
   - A task-level `environment/docker-compose.yaml` with `services: {main: {init: true}}` cuts teardown from **12.7 → 2.1 s p50** (13.6 → 2.8 p95). Framework overhead per 2-step trial falls from 40.2 → 26.9 s p50.
   - **Recommendation:** the B1 importer or pack emits this override. `stop_grace_period: 1s` is an alternative. This needs no Harbor change.
2. **Artifact-source snapshots remove a transfer.** Grading Harbor's host artifact copy instead of re-downloading `/app` saves about 1.0 s per step and gives identical rewards.
   - It also removes a second read of the agent env.
   - **Recommendation:** the WS-D verifier defaults to `snapshot_source=artifact`, and still records the digest.
   - It relies on Harbor's artifact exclude list matching the verifier's snapshot excludes. Keep them in one place.
3. **The per-step reset is cheap.** `setup.sh` takes 0.23 s. Almost all of the 1.27 s "reset" phase is Harbor's workdir upload and healthcheck.
4. **A′ vs B (one trial per checkpoint).** This is an inference from the p50s above, for 8 checkpoints, warm:
   - **A′:** ≈ one trial fixed cost plus 8 per-step costs. That is about 25 + 8×6.8 ≈ **79 s**, or about **66 s** with `init: true`.
   - **B:** pays the trial fixed cost at every checkpoint. It loses the per-step reset but keeps collection, snapshot and plumbing, so ≈ 8×(24.9 + 5.5) ≈ **243 s**, or ≈ 8×(11.2 + 5.5) ≈ **134 s** with `init`.
   - A′ is roughly 2–3× cheaper. This supports keeping A′ (ADR-001 option table).
   - Against 0.3's real gpt-6-luna-low agent steps (81–118 s), A′ adds about 6–8 % per step. That is under the design memo's 15 % container-overhead trigger for the dev-only fast loop. B would add about 15–30 %.

## 5. N1 budgets (accepted by the user, 2026-09-29)

**Rule.** For each phase:
1. Take the worst warm-cell p95, excluding the contention-flagged outliers noted in §3.2.
2. Multiply by about 1.3–1.5.
3. Round up to 0.5 s, with at least 0.5 s of absolute headroom.

A check fails when the p95 over ≥10 samples exceeds the budget. Budgets apply to the **warm** path, meaning the pack image is present. The **cold** path is recorded, and gets only a tripwire until the WS-I prebuilt pack changes what "cold" means.

| N1 phase | Proposed budget (p95) | Scope | Basis (worst warm p95) | Condition / note |
|---|---|---|---|---|
| Environment start, warm | **20 s** | trial | 16.0 (12.2 warm-init; 28.5 outlier under load) | Image layers cached |
| Environment start, cold | **record-only; tripwire 420 s** | trial | 339 | Network-dominated. Re-baseline after WS-I I1, when cold becomes a prebuilt-image load or pull |
| Grader (oracle) image, warm | **1 s** (presence check) | trial | ≈0 (cached tag) | Cold build is record-only; tripwire 180 s (p95 140) |
| Component apply [provisional] | **2.5 s** | trial | 2.1 | For bundles ≤ 1 MB. Phase 1 measurement (§8): about 250 ms for the all-8-built-ins `setup.sh`, which runs per step inside per-step reset; `raidar run`'s `component_apply` is host-side only. Budget unchanged |
| Per-step reset | **2.0 s** | step | 1.73 | Includes Harbor workdir upload and healthcheck |
| Artifact collection | **3.5 s** | step | 2.91 | Small `/app`. A size term is needed (§6) |
| Snapshot transfer | **0.5 s** (artifact source) / 2.0 s (download) | step | 0.00 / 1.36 | Depends on adopting finding 2 |
| Verifier plumbing | **1.5 s** | step | 1.10 | Excludes the oracle run and snapshot |
| Step archive gap | **2.5 s** | step | 1.93 | Harbor's step-output move |
| Scorer orchestration [provisional] | **6 s per containerised scorer** | step | 5.4 (8.7 outlier) | Noisy under contention. Re-baseline with real scorers |
| Teardown + result write | **4 s** (with `init: true`) / 15 s (without) | trial | 2.84 / 13.70 | Depends on adopting finding 1 |
| Raidar result write | **0.5 s** | trial | ≈0.001 | Stub |
| **Aggregate: per-step framework overhead** | **10 s** | step | ≈7.4 p95 sum (download) | Excludes the agent, oracle and scorers |
| **Aggregate: per-trial fixed overhead, warm** | **27 s** (with init) / 38 s (without) | trial | 16.2 / 30.7 (sum of phase p95s) | Sum of the env start, component apply and teardown budgets |

**Decisions (all accepted by the user, 2026-09-29):**
1. Accept or amend the budgets.
2. Adopt finding 1 (`init: true` override), which sets the 4 s teardown and 27 s per-trial budgets.
3. Adopt finding 2 (artifact-source snapshots), which sets the 0.5 s snapshot budget.
4. Accept that cold is record-only plus a tripwire until WS-I I1.

## 6. Gaps to close before `raidar perf check` gates on these (plan D4)

- **Size scaling.** Re-measure collection, snapshot and archive with a realistic `/app`: a real gpt-6-luna-low agent tree of 1–50 MB with `.venv` excluded. Then express those budgets as `a + b·MB`.
- **Stubbed phases.** Component apply is now measured on the WS-C implementation (§8). Scorer orchestration still needs a re-baseline with real scorers; it is not produced by `raidar run`.
- **Quiet-machine rerun.** The p95s here include cross-agent contention. A rerun with the VM otherwise idle would give tighter budgets. D4's CI job should fix its own concurrency.
- **Longer trials.** Per-step costs were flat across checkpoints 1–2 here, and 0.3's 8-checkpoint runs showed similar per-step numbers. An 8-step warm cell would confirm there is no drift, for example from growing artifact dirs.

## 7. Reproduce

```bash
cd spikes/overhead
export DOCKER_CONTEXT=orbstack             # OrbStack only; check orbstack-data-status first
PY=../../orchestrator/.venv/bin/python     # needs spikes/a-prime/_work (build_task.py) present
$PY bench.py --cell warm --reps 10         # also: warm-artifact, warm-init, cold (about 8 min/rep)
$PY summarize.py                           # writes baseline-summary.json and prints the table
./cleanup.sh                               # removes the per-trial ovh-* env images
```

## 8. Phase 1 measurements on the production code (WS-C, WS-D; 2026-09-30)

These are measurements, not budget changes. The §5 budgets stay as accepted.

**How components are actually applied.** Phase 0 stubbed component apply as a bundle installed as root into a harness baseline directory (for example `/opt/raidar/harness-baseline`) that the reset restores from. The WS-C implementation does not do this, and nothing is written to `/opt/raidar/harness-baseline`:
- On the host, the arm's condition is compiled (`raidar.components.compile_arm`) and written into the trial's Harbor task. Every step gets a generated `steps/<step>/workdir/setup.sh`, and the workspace overlay (for example `AGENTS.md` or lint configs) goes into the first step's `workdir/`.
- Harbor uploads each step's `workdir/` and runs `setup.sh` as the agent user. The script runs the context reset, then materialises component support files under `/tmp/raidar-components` (never inside the workspace), records the overlay digests for the discovery probes, and deletes itself.

**What `raidar run` records as N1 phases** (`raidar.execution.phases`, per trajectory record):
- `component_apply` is the host-side compile plus writing the condition into the trial task.
- The in-container `setup.sh` runs inside Harbor's step preparation, so it falls under `per_step_reset`. The reset's own duration (from `raidar-reset.json`) is also recorded as the record-only sub-phase `setup_sh`; the support-file materialisation after the reset (about 20 ms) is not timed separately.
- **Consequence for the budgets.** `component_apply` no longer measures the quantity its 2.5 s budget was set from (the Phase 0 in-container install, p95 2.1 s). Against the host-side compile it is only a regression tripwire, far from binding. The in-container component work is judged by the 2.0 s `per_step_reset` budget, which Phase 0 set from a reset without components; the fixture's lint-config arm keeps `per_step_reset` at p95 1.49 s, inside it. No budget in §5 was changed; re-baselining `component_apply` (or folding it into `per_step_reset`) is a decision for the budget owner.
- The pre-agent leak scan (WS-E guard) runs in the same gap before the agent starts. It is recorded as `pre_agent_leak_check` and subtracted from `per_step_reset` and `step_archive_gap`, because Phase 0 had no scan there.
- Phase 0's definitions are otherwise unchanged. In particular, `step_archive_gap` still overlaps the next step's `per_step_reset`, exactly as in §2.

**Measured values.**

| Measurement | Result | Source |
|---|---|---|
| All-8-built-ins `setup.sh`, exec'd as uid 1000 in `raidar/pack-python:8c98ccdfd980e2a4` | 250–256 ms over 5 runs. The reset is 229–234 ms of that, mostly its fixed 0.2 s settle sleep, so support-file materialisation is about 20 ms. This excludes docker exec and Harbor's workdir upload | WS-C |
| Host-side `component_apply` (compile + write) | p50 0.01 s / p95 0.01 s (n=10) | WS-D fixture |
| `setup_sh` inside real trials (baseline and lint-config arms) | p50 0.23 s, max 0.25 s (n=20) | WS-D fixture |

**WS-D no-LLM fixture** (`make perf-fixture`). This is a pack-bound 2-checkpoint problem (the WS-B importer run over the fake SCB dataset) × {baseline, lint-config} × 5 blocks, using Harbor's `oracle` agent at concurrency 1 on OrbStack. All 10 trajectories were valid and every checkpoint was solved. `raidar perf check` passed every budget:

| Phase | p50 / p95 (s) | n |
|---|---|---|
| environment_start (warm) | 3.14 / 4.58 | 10 |
| grader_image (warm) | 0.22 / 0.27 | 20 |
| per_step_reset | 1.27 / 1.49 | 20 |
| pre_agent_leak_check (record-only) | 0.57 / max 0.81 | 20 |
| artifact_collection | 2.66 / 2.77 | 20 |
| snapshot_transfer (artifact) | 0.00 / 0.00 | 20 |
| verifier_plumbing | 0.00 / 0.01 | 20 |
| step_archive_gap | 1.28 / 1.58 | 10 |
| teardown_result_write (init) | 2.27 / 2.61 | 10 |
| per_step_overhead | 4.79 / 5.53 | 20 |
| per_trial_fixed_overhead (init) | 5.29 / 6.78 | 10 |

**Real-agent trials** (codex-cli `gpt-6-luna` low: forge checkpoints 1–2, and the one-shot `circuit_eval/checkpoint_1`). There are too few samples to judge a p95, but two observations matter for the gaps in §6:
- `artifact_collection` took 4.7–5.2 s on 2 of the 3 real steps, above the 3.5 s budget. One of those steps had an empty `/app`, so the time is not explained by tree size alone.
- Harbor's `agent_setup` took about 36 s in both real trials, because Harbor reinstalled codex (`apt-get`, `nvm`, `npm install -g @openai/codex@0.156.1`) even though the pack image bakes in 0.156.1. The cause is that Harbor merges stderr into stdout for `codex --version`. The pack image's `HOME=/tmp/agent_home` makes codex print a `WARNING ... under temporary dir "/tmp"` line on stderr, so Harbor parses that line as the version, sees a mismatch, and reinstalls. `agent_setup` has no N1 budget, so `raidar perf check` does not catch this. It is a WS-I pack fix: move `HOME`/`CODEX_HOME` out of `/tmp`.
