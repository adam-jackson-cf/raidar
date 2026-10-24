# Mechanics v1, step 0 spike: copy-only data path and host-side liveness (2026-10-23)

- **Branch:** `spike/copy-path` (worktree `../raidar-spike`), at commits `23c8b04` (liveness) and `e335fce` (copy mode, runner) on top of `7db0890`. Not merged, not pushed.
- **Design:** `docs/reboot/mechanics-v1-design.md` r2 §5 step 0. **ADR:** `docs/reboot/adr-002.md`.
- **Raw output:** `../raidar-spike/.tmp/spike/{A,B,C}/summary.json` and `mounts.jsonl`; the fixture logs are beside them. Scripts: `spikes/copy-path/{run_arm.sh,collect.py,compare.py,watch_mounts.py}`.

## Arms

All three arms run the no-LLM perf fixture (`tests/test_overhead_fixture_run.py`): 2 checkpoints × {baseline, guided} × 5 attempts with Harbor's `oracle` agent, graded by the Docker grader, then `raidar perf check --strict`. Each arm runs at concurrency 1 and again at 3.

| Arm | Where Raidar runs | Docker | Data path |
|---|---|---|---|
| **A** | mbp (rsync checkout) | mbp OrbStack, local socket | today's mounted path (`RAIDAR_COPY_MODE` unset) |
| **B** | mbp | mbp OrbStack, local socket | copy mode (`RAIDAR_COPY_MODE=1`) |
| **C** | work Mac | mbp OrbStack via forwarded unix socket (context `mbp-fwd`) | copy mode |

mbp's OrbStack is shared with other projects' `native-*` containers (load average 1.8–3.8 during the runs).

## Results

**Parity.** `compare.py` found 0 differing fields against A, across 40 step results per arm (20 at each concurrency). Every `/app` snapshot digest and grading ledger is identical in A, B and C, and so are the leak scans and the presence of `raidar-reset.json` in every step.

**Bind mounts** (`docker inspect` of every Raidar container seen during the run):

| Arm | Containers | Bind mounts |
|---|---|---|
| A | 116 | 444 |
| B | 116 | 0 |
| C | 116 | 0 |

**Perf p95 (s)** against the N1 budgets (`overhead-baseline.md` §5). Every phase passes in every arm.

| Phase | Budget | A c1 | B c1 | C c1 | A c3 | B c3 | C c3 |
|---|---|---|---|---|---|---|---|
| environment_start[warm] | 20.0 | 3.82 | 1.40 | 2.15 | 1.67 | 1.64 | 3.26 |
| grader_image[warm] | 1.0 | 0.06 | 0.05 | 0.18 | 0.06 | 0.06 | 0.40 |
| per_step_reset | 2.0 | 0.47 | 0.48 | 1.24 | 0.47 | 0.45 | 1.56 |
| artifact_collection | 3.5 | 0.19 | 0.30 | 1.34 | 0.21 | 0.31 | 1.60 |
| step_archive_gap | 2.5 | 0.48 | 0.45 | 1.27 | 0.46 | 0.44 | 1.02 |
| teardown_result_write[init] | 4.0 | 0.43 | 0.37 | 0.78 | 0.54 | 0.51 | 1.42 |
| per_step_overhead | 10.0 | 1.08 | 1.19 | 3.00 | 1.04 | 1.16 | 4.08 |
| per_trial_fixed_overhead[init] | 27.0 | 4.25 | 1.75 | 2.88 | 2.17 | 2.12 | 4.04 |
| verifier_plumbing | 1.5 | 0.01 | 0.01 | 0.01 | 0.01 | 0.01 | 0.01 |

- Copy mode costs little on the same host: B is within about 0.1 s of A per step.
- Driving Docker from the work Mac (C) adds about 0.8–1.2 s per step at p95. Each `docker exec`/`cp` round-trips over the SSH socket.

**Grader copy seconds, p50 (n = 20 per role):**

| Arm | grading copy-in | grading run | grading copy-out | coverage copy-in | coverage run | coverage copy-out |
|---|---|---|---|---|---|---|
| B | 0.04 | 0.66 | 0.05 | 0.04 | 2.01 | 0.06 |
| C | 0.16 | 0.83 | 0.08 | 0.14 | 2.14 | 0.09 |

The fixture's `/app` is small; how copy time scales with a real forge `/app` is not measured.

**Negative control.** With today's mounted code pointed at the remote daemon (flag off, `mbp-fwd`), problem validation's grader mounted empty directories on mbp. The run returned `sandbox_error` → `infra_failure`, and the lock then refused with `RT0 ... has no passing verifier validation`, so no trials ran. Docker also created empty phantom host directories on mbp under `~/Projects/complete/raidar-spike`; they have been removed.

## Liveness monitor (observe/enforce prototype)

- **Code:** `execution/liveness.py`, armed by `RAIDAR_LIVENESS=observe|enforce`.
- **Hooks:** `AGENT_START` starts monitoring and `AGENT_END` stops it. Signal 1 comes from Harbor's agent-phase log callback; signals 2–3 come from a root `/proc` poll for the agent uid's CPU and the container's network bytes.
- **Output:** the record goes to `<trial dir>/raidar-liveness.json` on the host.
- **Test conditions:** interval 2 s, idle threshold 20 s, copy mode on `mbp-fwd`.

| Case | Mode | Decision |
|---|---|---|
| Hung agent (`sleep`) | observe | `idle_observed` at 21.8 s |
| Busy, silent agent (CPU loop) | observe | `active` (CPU > 100 ticks per sample) |
| Chatty agent (echo every 1 s) | observe | `active` |
| Hung agent plus a `setsid`-detached sleeper | enforce | `idle_killed` at 23.9 s; the detached process is in `killed`; `residual: []` |
| Container stopped mid-monitoring | — | the failed poll is recorded `unmeasured`; the decision stays `active` |

**Threshold headroom.** Across the main-03 and main-04 jobs there are 196 codex rollouts with 15,458 gaps between consecutive rollout events. Those gaps are p99 12.4 s, p99.9 29.8 s and max 64.3 s, against the proposed default `idle_timeout_sec` of 600 s.

## Falsifiers

| Falsifier (design r2 §5 step 0) | Result |
|---|---|
| A/B/C identical ledgers; parity fixtures | **Pass** for digests and ledgers. The mode, case and symlink fixtures and the 5× regrade on A were not built. |
| No bind sources in B/C | **Pass** |
| `raidar-reset.json` in every step; leak scans clean | **Pass**, all arms |
| A hung agent flagged idle; a busy one not | **Pass** (fixture agents) |
| A `setsid` survivor is reaped | **Pass** (enforce mode, uid kill) |
| Killing the tunnel mid-trial leaves no running remote agent after reconnect | **Fail, as expected: this is the requirement for step 1.** The trial was classified `agent_infra` (`step_exception: RuntimeError`), which is correct. But the trial container and its compose network stayed running on mbp (`sleep infinity`, no agent process), with nothing to reap them on reconnect. Both were removed by hand (Raidar's own objects only). |
| Image IDs differ across daemons while recipe digests match | **Not tested:** only one daemon (mbp) is available while the work Mac's OrbStack is off |
| Blackholed model endpoint is flagged idle per harness | **Not tested:** it needs a real harness install; deferred to step 5 calibration |
| Transport option (b), `ssh://` with ControlMaster | **Not measured.** Option (a), the forwarded socket, works. |

## Findings carried into step 1 onward

1. **Reap on reconnect.** After a transport loss, the target supervisor must remove the job's labelled containers and networks once the target is reachable again (spike evidence above). Today the cleanup runs only through the lost socket.
2. **The perf fixture has been broken since the spec-determinacy audit landed** (`140603d`, 2026-10-02) [INFERENCE from the error; not bisected]. The fake dataset's `entry_file` (`adder.py`) doesn't match its reference file (`app.py`), so the audit's variant shim raises `FileNotFoundError`, with or without copy mode. The spike set `audit=False` in `tests/execution_support.py::imported_pack_fixture`. The proper fix (step 2) is to make the fake dataset's names agree.
3. **The validator mounts too.** `problems/validator.py` builds its own `DockerSandbox`; the spike routes it through `grading_sandbox()`. Step 3 must cover the validator, regrade, prefix-grade and offline-coverage paths, not just the trial verifier.
4. **Harbor non-mounted mode works through `capabilities.model_copy(update={"mounted": False})`, an empty mounts compose file and a no-op `prepare_logs_for_host`.** Harbor creates the in-container log directories itself. The stale-log-name workaround is unnecessary in copy mode.
5. **Remote control cost** is about 1 s per step at p95 over the forwarded socket. That is well inside N1, but it is the reason to keep budgets per target.
6. **The mbp hardware baseline is far below N1** (environment start p95 1.4–3.8 s against a 20 s budget). mbp budgets proposed from B/C would therefore be much tighter than N1; that is your decision (ADR-002 §2.5).
7. **Liveness risk to calibrate in step 5:** work that runs as another uid, or a long quiet root-owned process during the agent phase, reads as idle, because only agent-uid CPU counts. [INFERENCE] A component that runs its checks as root inside the agent phase (for example a gate) would be one such case; this was not tested.
