Two separate product bugs explain all five reported uid/gate failures. A third, unrelated bug breaks the seed test. I edited no files and ran no containers or make targets; everything below comes from the logs, the gate records under `var/tmp/local-gates/` and the code at `2df25b5`. None of the three failures is caused by OrbStack.

**Failure 1: `stage-check` fails with "the gate runs as uid 0"** (gate-check, note-state, docs-check)
- **Root cause:** `stage-check` runs the gate in its own throwaway container, `PackContainer`. Its `exec` passes no `--user` when `user=None` (`stage_container.py:103-105`), and the pack image sets no `USER` (`lab/environments/python/image/Dockerfile`), so those commands run as root. `GateRun._agent` and the setup check depend on `user=None` meaning the agent's user (`gate.py:472`, `gate.py:683-685`). In Harbor trials that holds, because Harbor sets `default_user` to the agent user; `PackContainer` has nothing equivalent. The check in `gate_tool.py:1062-1063` then correctly refuses. Introduced in phase 4 by `bcd6f30` (`stage_check.py:177-183`). Group F (`26cb23e`, the broker removal) did not touch this path. Product defect; any Docker would behave the same.
- **Contradiction with the brief:** the replay run did not fail on uid. Its log says `at3-rounds_exhausted holds no captured state for acme/note-state`. Group F moved the fixtures under `acme/`, but `at3_support.py:122` still installs with `reference=package.name`, i.e. `note-state`. So the trial saved its state under `stages/note-state/`, while `captured_states` (`stage_check.py:529-533`) looks for the `acme/note-state` key. That is a test defect from `26cb23e`.
- **Remediation:**
  - Give `PackContainer` a default user that applies when `user is None`, the same way Harbor's `_resolve_user` works. Set it to uid 1000 in `_gate_once`, matching its existing `chown -R 1000:1000`. Keep `_check_not_root`: it is what caught this.
  - Change `at3_support.py:122` to `reference=f"{FIXTURES.name}/{package.name}"`.
  - This keeps the design rules: the gate runs as the agent's user with no broker, and root only installs.
  - Even with the uid fixed, all three packages would still fail, because each gate command check starts with `python` (inference): the failure 2a/2b defect.
- **Confidence:** high.

**Failures 2a and 2b: planted exit 126 gives `refused`, and the planted timeout records no `timeout` error**
- **Root cause:** this is not a uid problem. In AT3 both `setup-check.json` and `gate-1.json` show uid 1000 and passed. The real defect is in `check_argv` (`gate.py:174-180`). It replaces `gate-check` with the package's raw manifest argv, `[python, -I, -B, -m, gate_check]`. That `python` is never pointed at the package's venv.
  - The check's PATH is `<root>/bin:BASE_PATH` (`install.py:101-103`). `<root>/bin` holds only the generated wrappers (`install_tree.py:104-133`), so `python` resolves to `/usr/local/bin/python`.
  - The setup-check row for this tool passed anyway, because it only looks for that interpreter (`gate_tool.py:1076-1082`).
  - Every seal and the canary record `/usr/local/bin/python: No module named gate_check`, exit 1. The no-marker check runs first (`before: true`) and its refusal stops the rest, so the planted `conftest.py` never runs. That gives `refused` with no `timeout` cause.
  - The canary also returned `refused`, which the test accepts (`test_package_at3_docker.py:111`), so the broken tool counted as a decision.
- **Introduced:** phase 4, `bcd6f30`. Product defect. In real trials, every gate command check that goes through a tool command would always refuse, and that refusal would count against the agent.
- **Remediation:** make `check_argv` return `run` unmodified. `gate-check` then resolves through PATH to the root-owned wrapper `<root>/bin/gate-check`, which runs the venv's python. Setup and stop already work this way (AT3 `healthy` passes). This is what the stage/1 contract in `lab/stages/README.md` describes (commands on PATH). The wrapper sits inside the digest-checked tool tree, so the agent still can't forge validity. The canary will then actually run the tool. Add a regression test that a tool-command check resolves to `<installed.bin>/<name>`.
- **Confidence:** high.

**Failure 2c: the seed's `uv` gets Permission denied on `/tmp/uv-cache/sdists-v9/.git`**
- **Root cause:** the seed script (`workspace.py:123-130`) runs as root.
  - The image's pip is 25.0.1, which lacks `--uploaded-prior-to`. So root first runs `uv pip install --system …`, which uses the image's shared cache `UV_CACHE_DIR=/tmp/uv-cache` (mode 1777, Dockerfile lines 31 and 58-61).
  - That leaves root-owned cache files behind. The next step, `runuser -u agent -- uv venv`, then can't open `sdists-v9/.git`.
- **Introduced:** `712bf3d`. Product defect, not specific to this machine: the base image is pinned by digest (`pack.yaml:16`), so the root branch runs on every target. Inference: in real trials with declared test dependencies, the seed install would fail and the trial would be marked infrastructure-invalid.
- **Remediation:** add `--no-cache` to the root `uv pip install` at `workspace.py:124`. Root then never writes to, or reads from, the cache the agent can write.
- **Confidence:** high on the cause. Medium that real trials fail, because no real trial was observed.

**Releases and locks:** none of the fixes changes a stage package tree, a workflow or agent definition, `lab/releases.yaml`, an experiment lock, the problem registry, the pack lock or the `gate_tool.py` payload digest. No `@N` release or lock is affected; each fix needs a CHANGELOG entry.

Shared cause: not one uid bug. Failures 1 and 2a/2b come from two separate `bcd6f30` defects: the `PackContainer` default user (1) and the unresolved `python` in `check_argv` (2a/2b, which also hides behind 1). The replay failure is a Group F test-reference defect, and 2c is an unrelated uv-cache ownership defect from `712bf3d`.
