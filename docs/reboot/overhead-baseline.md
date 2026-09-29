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
| Component apply | Uploads a representative component bundle and installs it as root into the harness baseline dir that the per-step reset restores from. The bundle is `spikes/overhead/components/fixture`: AGENTS.md, config.toml and 3 skills, 11 files, 47 KB. It runs in `agent.setup`, so it is inside Harbor's `agent_setup` phase | `overhead_spike/components.py` |
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
| Component apply [provisional] | **2.5 s** | trial | 2.1 | For bundles ≤ 1 MB. Re-baseline when WS-C lands |
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
- **Stubbed phases.** Re-baseline component apply (WS-C) and scorer orchestration (real scorers) once they exist, using the same harness.
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
