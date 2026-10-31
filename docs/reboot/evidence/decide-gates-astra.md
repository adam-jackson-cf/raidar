**1 — `stage-check`: UID 0**
- **Root cause:** product defect introduced by phase 4, `bcd6f30`, not broker removal. `orchestrator/src/raidar/subject/workflow/stage_container.py:94-110` omits Docker `--user` when `user=None`; the pack defaults to root. Gate setup inherits that default (`subject/primitive/gate.py:470-472`, under the same source root).
- **Remediation:** make `PackContainer.exec` translate `None` to UID 1000, preserving explicitly requested root. Keep the root-refusal check, broker-free execution, supervisor and seal record protection (§5.1, §6); never bypass the check.
- **Identity:** no definition `@N`, dependency lock or arm-lock change from this adapter-only fix; execution provenance changes.
- **Confidence: high.** Ordinary fixture logs establish this failure; it is not OrbStack-specific.

**1, replay — different failure than the brief reports**
- **Evidence:** `var/tmp/local-gates/stage-check-note-state-replay.log` says **“holds no captured state for acme/note-state”**, not UID 0.
- **Root cause:** test-driver namespace migration omission in `26cb23e` (Group F). `orchestrator/tests/at3_support.py:119-125,141-143` still uses leaf `package.name` as the reference. Actual captures are under `stages/note-state/`; `orchestrator/src/raidar/subject/workflow/stage_check.py:530-534` searches `stages/acme_note-state/`.
- **Remediation:** use `acme/<name>` consistently for installation and `PackageSpec.reference` in the driver; regenerate evidence and replay it. Keep exact namespace matching—no compatibility alias or fabricated context.
- **Identity/confidence:** test defect; no `@N` or lock changes; **high**. Not environmental and unrelated to broker execution.

**2a — planted exit 126**
- **Root cause:** product defect in `bcd6f30`: `orchestrator/src/raidar/subject/primitive/gate.py:174-180` expands the declared command into raw `python -I -B -m gate_check`, bypassing its installed wrapper and venv.
- **Evidence:** persisted `planted_exit_1260/.../agent/gate/{canary,gate-1}.json` records UID **1000**, `/usr/local/bin/python: No module named gate_check`, and prerequisite exit 1. `gate_tool.py:574-578` correctly stops before pytest; the planted conftest never executes.
- **Remediation:** resolve declared commands to `[f"{package.installed.bin}/{run[0]}", *run[1:]]`. Existing wrappers select the package venv (`payload/install_tree.py:104-108,127-130`). Preserve confinement, tool digests and authoritative seal evidence; strengthen this test’s known-good canary assertion to `passed`.
- **Identity:** no package/definition `@N` or `uv.lock` change; refresh gate check records and regenerate affected experiment locks—the runner’s transitive primitive identity includes `gate.py`.
- **Confidence: high.** Not a UID problem, test-expectation defect or OrbStack peculiarity.

**2b — planted timeout**
- **Root cause/evidence:** the same `bcd6f30` command-expansion defect. `planted_timeout0/.../agent/gate/gate-1.json` likewise records UID 1000, missing `gate_check`, prerequisite refusal and no pytest execution.
- **Remediation:** the same wrapper fix; retain the timeout assertion and prove the pytest check actually runs. Under §5.3, timeout after a healthy canary with unchanged tools remains an **agent outcome**, not invalidity; do not weaken attribution to make the test pass.
- **Identity:** same primitive/check-record/experiment-lock refresh as 2a; no additional release or dependency-lock change.
- **Confidence: high.** Product defect, not environmental.

**2c — seed cache permission failure**
- **Root cause:** product defect introduced by `712bf3d`, before phase 4. `orchestrator/src/raidar/engine/trial/workspace.py:123-130` upgrades pip as root, then invokes uv as the agent, sharing `UV_CACHE_DIR=/tmp/uv-cache` (`lab/environments/python/image/Dockerfile:32`). The log shows the upgrade succeeding before the agent hits the root-created cache entry.
- **Remediation:** add `--no-cache` to the root-only `uv pip install` upgrade command. Retain hash-pinned, wheels-only installation and agent-user venv creation; do not broadly chmod/chown the shared cache or run agent work as root.
- **Identity:** no `@N`, pack recipe or dependency/experiment-lock change from this script-only fix; execution provenance changes and requires fresh smoke evidence.
- **Confidence: high.** Triggered by an older image pip requiring upgrade, not specifically OrbStack/macOS.

Shared cause: 2a/2b share command resolution; ordinary stage checks have a separate UID-default defect, replay a namespace mismatch, and seeding a cross-user cache defect. No files edited, containers started or checks rerun.
