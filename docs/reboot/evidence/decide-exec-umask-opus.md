**Activity Summary:** This message replaces my last answer. Only the regression-test part changed: it now extends existing tests instead of adding a new slow test, which follows the repository's regression-coverage rule. I edited no file. The only container I ran was a throwaway one on `raidar-mbp`, now removed. Inside it, `docker exec` reported umask `0000` for root and for uid 1000.

**Fix: (d), confidence high.** Option (c) alone would refuse the new target and leave the code unfixed.
1. Make `os.umask(0o022)` the first line of `payload/main.py:main()`. This is the single entry for every root payload; `gate_tool.main` already does the same.
2. In `install_tree.install`, chmod 0755 every directory it creates below `/opt/raidar/stages`, and run `_normalise_modes` after `_wrap`, so `bin/` is covered.
3. Prefix every `calls.trusted_exec` command with `umask 022`, and delete the duplicate prefix in `gate.py:_run`. Add the same prefix to the root commands in `engine/trial/provision.py`, which calls `environment.exec` directly.

**Payload audit:**
- **Depends on umask:** `install_tree` leaves `<ns>` and `bin` at 0777; I reproduced this on `mbp`. `calls.put_root_file`, `upload_state` and `make_state_dir` run `mkdir -p -m 0755`, which leaves missing parent directories at 0777. That hits `/var/lib/raidar-calls/canary/<scope>-<dir>`; the same command gave `/app/.raidar/gate` 0777 on `mbp`. `provision.py` likely leaves `/opt/raidar-tools` at 0777 (inference: the image has no such directory, so `mkdir -p` creates it).
- **Not dependent:** `_install_payload` (explicit `-m`, then `chmod -R go-w`), `run_payload` operation directories, the `walk.snapshot` tar (0600), supervisor call directories, `gate.py` and `gate_tool`.
- **Correction to the brief:** `/opt/raidar/stages` is 0755 in the real call order, because `ensure_trusted` creates it first.

**Identity cascade:**
- All 9 `primitive_code` digests change: `frame/1`, `install/1`, `setup/1`, `gate/1`, `stop/1`, `outputs/1`, `runner/1`, `acceptance-reply/1` and `jev-judge/1`. `identity._files` adds every `payload/*.py` to every inventory; `primitive_inventory` confirmed this.
- Delete `lab/stage-checks/2dc004c3….json` (the `raidar/define` record with its `primitives` map) on purpose, then rerun `make stage-check`. The tool refuses to publish different bytes over an existing record.
- The three arm64 `.installed.json` digests should stay the same, because the local tree is already 0755 (expected, not run). After the fix, `mbp` should produce `d28105d3`.
- Relock all 12 experiment locks with `make experiment-lock`; `experiment-locks-check` is part of `make quality`.
- `lab/releases.yaml` needs no new `@N`, because it names no primitive or payload code.
- The grading closure imports no `raidar.subject` or `raidar.engine` module, so grader IDs and the 9 problem validations stay valid.
- `primitive_code` is a pooling key, so jobs run before the fix will not pool with jobs run after it.

**Sequencing:** land this first as its own reviewed identity commit and PR, with before and after digests. Both PRs rewrite all 12 locks, so rebase `agent/mbp-ready` onto it and relock once. Do not declare `mbp` ready before this lands. Every package-arm trial there is invalid at install, because its tree `36e1be4f` differs from the locked `d28105d3`. `security-gate` also fails 16 of 36 cases.

**Severity: high but latent, and contained today.**
- On `mbp`, uid 1000 (`agent`) moved `bin/probe` aside, wrote and ran a fake wrapper (it printed "forged"), then put the original back. The tree digest read `661847abd548` before and after. The same user also renamed `<ns>/define` and created a replacement.
- The per-call `verify()` hashes content, mode and owner, not inode or change time, so it misses a swap that is put back. The T1 health check `W11` only reads integrity findings already recorded.
- It is contained because `package_trial.installed_mismatch` stops every package trial before the agent starts. It becomes exploitable only if someone deletes the arm64 record and reruns `stage-check` on `mbp`.
- It does not give the agent root: `/opt/raidar` and `/opt/raidar/trusted` are 0755.

**Regression coverage:** the broken rule is that trusted root output and installed-tree digests must not depend on the container's exec umask.
- **Existing coverage did not protect it.** Only `security-gate` caught it, on `mbp`. The fast mode test passed because it inherits umask 022, and no test sets a hostile exec umask.
- **Extend `test_an_install_copies_bytes_normalises_modes_and_wraps_commands_safely`:** run it through `main.main(["install", …])` under `os.umask(0)`, with the root nested at `<stages>/ns/name`. Assert that `ns`, the root and `bin` are 0755.
- **Extend `test_fresh_installs_match_despite_different_uploaded_wheel_timestamps`:** make its second install use an exec that starts with `umask 000;`. Assert the same tree digest as the first install. This keeps the existing rule that fresh installs match despite incidental environment differences.
- I am adding no new test, because the existing scenarios already cover this rule.
