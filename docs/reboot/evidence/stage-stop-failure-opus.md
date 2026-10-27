# Decision: a stage's malformed stop-call result

**Decision.** Don't sort failures by what the failed call looks like. The rule is attribution by control run. The trial is invalid only when the package tool fails on input the lab owns: a setup call, or a known-good fixture run as a canary in the same container. When the tool fails on the agent's state and the canary passes, the failure is an agent outcome. That covers junk, crash, signal or OOM, timeout and schema-invalid output. The stage gets one repair turn, then closes, and the raw output is recorded. Raidar rejects out-of-bounds state before calling the tool. A tool bug found later invalidates the whole package version for every arm. It never invalidates single trials. [INFERENCE]

The orchestrator's split is rejected in one place. It treats "non-zero exit without a result", "timeout" and "crash" as instrument failures. Those are exactly the channels the agent's state can trigger. The split also includes "crash before reading agent state", which Raidar cannot observe from outside the tool. [INFERENCE]

## Classification table (on-stop and setup calls)

| # | Condition | Class | Recorded host-side |
|---|---|---|---|
| 1 | Setup call: non-zero exit, timeout, invalid JSON or missing tokens. It runs before the agent, on lab inputs only. | Instrument: invalid | Call record (argv, user, env digest, timeout, wall time, exit or signal, raw stdout up to the cap and its full digest, last 8 KiB of stderr) |
| 2 | Prepare canary: the package's `done` fixture, copied lab-owned into the container and run before the agent starts. Fails, or differs from the result digest `stage-check` recorded. | Instrument: invalid | Call record, plus expected and actual digest |
| 3 | On-stop result is valid and `done`, and every declared output exists | Agent: done | Call record; outputs copied and hashed straight after the call |
| 4 | Valid `resume`, within `max_rounds` | Agent: resume turn | Call record, message, carry |
| 5 | Valid `closed`, or `resume` past `max_rounds` | Agent: closed (root flag) | Call record; terminal ending |
| 6 | Pre-call bounds check fails on the watched state: symlink, FIFO or device; or bytes, file count or depth over the cap. The tool is not called. | Agent: unusable | Bounds finding and the state manifest (name, type, size) |
| 7 | No valid result, the canary passes and the tool tree is unchanged. "No valid result" means any of: non-JSON; schema-invalid; stdout over the cap; non-zero exit or signal without a valid result; timeout; `done` with a declared output missing. | Agent: unusable | Call record, cause, canary record, capped snapshot of the watched state (for replay) |
| 8 | No valid result, canary also fails, and the agent-writable filesystem and memory are above their floors | Instrument: invalid | Call and canary records, resource readings |
| 9 | As row 8, but free disk or memory is below its floor | Agent: unusable (`environment_exhausted`) | Same, plus resource readings |
| 10 | Tool-tree digest changed, a call record is missing, or the container exec itself failed | Instrument / integrity: invalid | The existing W1 and W11 style findings |

"Unusable" means: one repair turn per stage per checkpoint, then `closed`. This is define's existing single repair. [OBSERVED define.py:233-236]

## Bounds (Raidar-owned, not overridable by packages) [INFERENCE: values proposed]
- Per-call timeout: the manifest's `timeout_s`, default 60 s, hard cap 300 s. The canary uses the same timeout. Before each call, Raidar kills processes the agent left running, as the gate already does. [OBSERVED verify.py:569]
- Calls run as the agent's user under `env -i` with a fixed PATH. HOME and TMPDIR point to a fresh directory Raidar creates for that call. Python runs with `-I`. There is no network. The tool reads its state location only from Raidar's context or env, like `RAIDAR_DEFINE_STATE` today. [OBSERVED define.py:56] That is what makes the canary possible.
- Stdout cap 1 MiB; the last 8 KiB of stderr is kept. Watched state is capped at 64 MiB, 10,000 files and depth 32, with regular files and directories only. The snapshot is limited by the same caps.
- One repair per stage per checkpoint. `max_rounds` ≤ 5. The checkpoint deadline and budget still take precedence.

## Health and admission rules
- **New validity rows (W family; violations make the trial invalid):**
  - every setup and on-stop call has a call record;
  - the prepare canary passed;
  - every row-7/8/9 classification carries a canary record.

  Today W violations already make a trial invalid. [OBSERVED collect.py:369-378]
- **Warning, never a validity change:** a count per arm and per package digest of tool failures attributed to the agent, split by cause (rows 6, 7 and 9). The report shows it beside each arm's infra-error count. [OBSERVED report.py:163-166]
- **Any non-zero count needs a disposition** before the comparison is published. `make stage-check REPLAY=<trial>` reruns the same tool on the recorded state snapshot.
  - `agent_state`: the state is outside the documented contract. The result stands.
  - `tool_bug`: the tool fails on state the contract allows. The package version is at fault. Every trial of every arm that uses it leaves the comparison. The fix gets a new version and a new identity, and the affected arms are rerun in full.
  - Dropping or rerunning only the affected trials is forbidden.
- **Admission:** a passing `stage-check` record must exist for the exact package digest, pack recipe digest and protocol-primitive digest. Smoke and measured runs both require it, and there is no override: it is deterministic and cheap. This is stricter than `ALLOW_UNSMOKED`. [OBSERVED admission.py:14-16]

## `make stage-check` fixture requirements
1. **Author fixtures:** at least one for each status the tool can return. `done` and `closed` are always required; `resume` is required when `max_rounds` > 0. Each fixture is `context.json` plus a state tree plus the expected status and outputs.
2. **Determinism:** each fixture runs twice and must give byte-identical stdout and identical output digests. The `done` fixture and its digest become the in-trial canary (rows 2 and 7).
3. **Raidar-supplied adversarial fixtures, all within the bounds:** a missing state directory, an empty directory, a 0-byte file, invalid JSON, non-UTF-8 bytes, deeply nested JSON under the byte cap, a schema-valid but semantically wrong file, and an unreadable file. Each must return a valid result with a status other than `done` within a quarter of the timeout.
4. **Bounds fixtures** (FIFO, symlink loop, oversized file) check Raidar's own pre-call rejection; the tool never sees them.
5. **Same conditions as a trial:** non-root user, `env -i`, no network, pinned runtime. The tool-tree digest must be unchanged afterwards, and the tool must write nowhere except its state directory and the per-call temp directory.

## Attack on the proposed split

**Can anyone reliably tell "crashed" from "rejected"? No.**
- From a single call, an exception thrown while parsing agent JSON looks the same as a bug in the tool's rule code. [INFERENCE]
- Authors who catch every exception and emit `closed` move the error the other way: their tool bugs become silent agent outcomes. [INFERENCE]
- Define today has no instrument class at all. Any non-JSON inspector output becomes `error`, then a repair, then `definition_unusable`. [OBSERVED define.py:202-209, 233-236] That ending excuses the define evidence and the judge pass. [OBSERVED evidence.py:24-26, 44-46] So a broken define tool would score every trial as an agent failure, and no health row would catch it. [INFERENCE]
- Valid JSON that lacks `state` raises a `KeyError` in the lab's own code: that case has no classification at all. [OBSERVED define.py:210]
- Only a differential run tells them apart: the same tool, on known-good input, in the same container. A replay of the captured state then attributes the individual case. [INFERENCE]

**Can an agent make the tool crash on purpose? Yes, and the existing gate already shows it.**
- Ways in: a FIFO at the state path (`open` blocks, then times out); a symlink loop; a file bigger than memory (OOM); deeply nested JSON (RecursionError). [INFERENCE]
- The gate turns a killed check, exit 126/127 or the 600 s check timeout into ERROR. [OBSERVED verify_gate.py:39-41, 331-337]
- ERROR becomes `gate_error`. [OBSERVED verify.py:578] That is a W4 violation [OBSERVED evidence.py:38], and W4 makes the trial invalid. [OBSERVED collect.py:369-378]
- The seal runs the agent's own pytest and conftest. [OBSERVED verify.py:572] So a conftest calling `os._exit(126)`, or a suite running past 600 s, invalidates the trial. [INFERENCE: the code path is observed; the exploit has not been run]
- Under the orchestrator's split, every bundle inherits this hole. Rows 6 and 7 close it.

**What do timeouts caused by the agent's state count as?** An agent outcome, gated by the canary (row 7). There is precedent: an idle kill during enforcement is already classified as an agent timeout, not a validity failure. [OBSERVED collect.py:363-365]

**How does each rule bias the comparison between arms?**
- *All invalid (Astra):* invalid trials are dropped from analysis [OBSERVED report.py:376] and a later valid run can replace them. [OBSERVED sources.py:6-8] Agent-induced invalidation is therefore missingness that tracks failure. It biases stage arms upwards, and only stage arms can use it. [INFERENCE]
- *All agent outcome (Opus):* a real tool bug counts against the stage arm. The bias is downward and conservative, the evidence is visible, and arms without the stage are unaffected. But a tool that is broken every time looks like a method that never works, and nothing flags it. [INFERENCE]
- *Orchestrator's split:* it brings back Astra's upward bias through the cheapest channels to trigger (crash and timeout). [INFERENCE]
- *Final rule:* the trial is invalid only when the tool fails on lab-owned input, which does not depend on the agent's trajectory. Replacing invalid trials is then honest. What remains is a bias against the stage from input-specific bugs, and replay can audit it. [INFERENCE]

**Does the per-arm warning bring the bias back?**
- Only if someone acts on it per trial. Dropping or rerunning the flagged trials is Astra's rule applied after the fact, with the analyst choosing after seeing outcomes. [INFERENCE]
- A difference in rates between arms cannot diagnose a package bug either. It mixes treatment effect with agent quality, and an arm without the stage always has a rate of zero. [INFERENCE]
- So the warning only requires a replay disposition, and the remedy covers the whole package version or nothing.

## Residual risk
- A bug triggered by unusual but legitimate state passes the canary and counts against the stage arm until a replay finds it. It is bounded, recorded and replayable, but not removed. [INFERENCE]
- A single `done` canary misses bugs that depend on size or timing; the adversarial fixtures narrow this but don't remove it. [INFERENCE]
- The floors for exhaustion (rows 8 and 9) can blame the agent when the lab's container is simply too small. Setup should record free resources so this can be checked. [INFERENCE]
- **The gate is a Raidar-owned primitive with the same hole** (see the attack above). Unless its ERROR path follows the same canary rule, an agent can still invalidate trials through the gate checks. [INFERENCE]
- Define's inspector call sets no timeout, so a hang today runs until the checkpoint deadline cuts it. [OBSERVED define.py:202]

The work was read-only and edited no repository files. Nothing was run; `make quality` was not run.
