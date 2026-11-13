# Raidar handoff: continuing on a new machine (2026-11-05)

Give this file to the agent that picks up the work. It is self-contained; read it top to bottom before acting.

## Prompt

You are continuing Raidar's redesign into a neutral, open-source evaluation lab. An **arm** is an agent plus a workflow. A workflow is Raidar's fixed build frame plus user-authored **stage packages**, written to the `stage/1` contract over Raidar-owned **primitives**.

Before changing anything, read:
1. `AGENTS.md` and `CLAUDE.md` (repository rules);
2. `split-design.md`, the binding design: the status entries at the top, §5.8 Contract, and §9 Implementation plan (the phase table is the progress record);
3. `.omp/test-profile.md` and global `test-guidance` (binding for every test).

Run `make help` to see the public interface.

### Where things stand
- **Phases 3, 4, 4b and 5 are complete.**
  - Shipped defaults are real packages: `lab/stages/raidar/{verify,define,docs-check}`.
  - Workflows are schema 2: `raidar/one-shot@2`, `raidar/one-shot-verify@2` and `raidar/define-build-verify@2`.
  - `stage-import` and `workflow-import` exist, and the spike code is deleted.
  - The AT1 and AT2 real-model smokes passed (codex `gpt-6-luna`, low effort, ChatGPT login).
- **The contract is extracted as `contract/` (`raidar-contract`).**
  - Tags: `contract-v1.0.0` (the frozen `stage/1`), `contract-v1.1.0` (call-protocol models and 63 conformance cases) and `contract-v2.0.0` (lab policy moved to the orchestrator).
  - Hash pins are in `contract/releases/`. The built artefacts were under `var/contract/` and did not travel: run `git fetch origin --tags` before `env -u AGENT make contract-build`, and check the hashes against the pins.
- **The test suite follows `.omp/test-profile.md` tiers** (fast, slow, quality, docker, security, perf) and global `test-guidance`. The fast-tier coverage floors are 99.43% line and 98.72% branch, compared as integer counts. Never lower them.
  Budget measurement and approval operations are owned by `scripts/checks/` and the profile's Fast-tier budget section: dispatch `test-budget-measure.yml` at a fixed revision, import successful artifacts with `env -u AGENT make test-budget-record RUNS="<GitHub run ids>"`, and obtain an owner GitHub review bound to the exact policy diff and commit before policy increases.
- **All nine SCB problems are `validated_partial`**, revalidated with 5 repeats on the **`mbp`** target under grading digest `4cf6e9b2…` (2026-11-05, after the locked-uv change). Verdicts, check statuses and solvable-set sizes match the earlier runs. mocked_http's one varying hidden test changes which test is in its solvable sets from run to run (checkpoints 3–6 and 8); the vote accepted this as `mbp` evidence, see `CHANGELOG.md`. The grader pins are `mbp`'s image IDs (see below). All experiment locks are current.
- **The new machine uses the remote `mbp` Docker target.** The full no-model check sequence passed there on 2026-11-04, and again on 2026-11-05 after the locked-uv change (see [Set up the `mbp` Docker target](#set-up-the-mbp-docker-target)). The first run found and fixed a security defect. `mbp`'s `docker exec` runs with umask 0000, which left the installed tool tree world-writable. Now the root payload entry sets umask 022, every trusted exec and provision command is prefixed with `umask 022;`, and installs chmod the directories they create (PR #7, an identity change to all nine primitives).
- **Stage-check evidence travels with the checkout** in tracked `lab/stage-checks/`, outside
  package folders so released package digests stay unchanged. Installed-tree and stop-canary
  records use this canonical store, with no `var/` fallback; execution logs remain under `var/`.
  Installed-tree records are keyed by the **Docker target's container architecture**, not the
  host's. The committed records cover **arm64 only**, which is `mbp`'s architecture, so the new
  machine needs no new evidence whatever its own CPU. A **non-arm64 Docker target** needs
  `make stage-check` plus a relock before running those arms.

### How decisions are made (user rule since 2026-10-28)
- The orchestrating agent only orchestrates, verifies and reports.
- Every design or remediation decision goes to two independent read-only voters: Claude Opus 5.5 (`anthropic/claude-opus-5-5`) and GPT-6 Astra (`openai-codex/gpt-6-astra`). Adopt the majority. On a split, GPT-6.1 Sol (`openai-codex/gpt-6.1-sol`) chooses one voter's full answer. Report every vote.
- Implementers run headless, as GPT-6.1 Sol at high effort. Afterwards, run `make quality` yourself and spot-check.
- File the vote records under `docs/reboot/evidence/decide-<topic>-<voter>.md`.

### Rules that bit us
- **Released `@N` versions are edited in place** (owner decision 2026-11-12): update the registered digest in `lab/releases.yaml`, relock and rerun affected experiments; a registered version is never removed. Never hand-edit `*.lock.json`; relock only with `make experiment-lock EXPERIMENT=<path to experiment.yaml>`.
- **Identity-bearing bytes:** the grading digest closure (`grading_execution_digest()` in `orchestrator/src/raidar/grading/records.py`), `primitive_code`, and released package bytes.
  - Changing them invalidates the problem validations (RT7), and a revalidation takes about 6 h for 9 problems × 5 repeats.
  - Test-only work must never change them. Any identity change gets its own reviewed commit, with before/after values and a scheduled revalidation.
  - The grading digest includes the exact host Python version. `.python-version` and `orchestrator/.python-version` pin **3.13.7**, and CI installs it. Without the project-local pin, `uv run --project orchestrator` picked the newest installed Python (3.14.10 on the old machine), which changes the digest and fails RT7. `requires-python = ">=3.12"` remains the package compatibility range, not the validated grading interpreter.
  - **Raidar runs its own locked uv, never the host's, wherever uv can affect results.** `uv==0.10.0` is an orchestrator dependency, matching the problems' `resolver: uv@0.10.0` pins. The grading dependency resolver, native validation, stage locks, the ST5 package check and Python tool provisioning call it through `uv.find_uv_bin()` (`raidar.contract.uv`). The host uv only bootstraps the environment: any uv `>=0.10.0` works, including Homebrew's, and nobody needs to change their global uv. Moving the resolver to a newer uv is a deliberate change: update the pin, lockfile and problem records, then revalidate.
  - `env -u AGENT make toolchain-pin-check` (the first step of `make quality`) checks the Python pins and the running interpreter. It also checks that the declared `uv==X.Y.Z`, the `uv` version in `orchestrator/uv.lock` and every problem's resolver pin agree.
- **Prefix `make`, `uv` and `pytest` with `env -u AGENT`.**
- **Releases use on-demand PRs:** fetch origin, run `env -u AGENT make version-release` at `origin/main`, commit as `chore(release): X.Y.Z`, and use the normal gate; `make version-check` runs in quality and requires regeneration after rebase drift. Release versions no longer stale experiment locks.
- **Real-model trials need the user's approval.** Agents sign in with the local ChatGPT subscription (`CODEX_AUTH_MODE=chatgpt`), never the API key. The API keys are for Jev only, the judge model reached through OpenRouter. Secrets come from Infisical; never print them.
- **Report format:** an Activity Summary, then key points K1..Kn. Plain language; separate what was observed from what is inferred. State measured claims with sample size and an interval, or label them provisional.

### Set up this machine
1. Clone `main` and run `env -u AGENT make help`.
2. Install and log in to Infisical (project `ae2a56a4-28d9-429f-bb7c-7d45e26b9ffa`, env `dev`), and run `codex login`, which uses the ChatGPT subscription.
3. Install the toolchain: any uv `>=0.10.0` (Homebrew's is fine), then `uv python install 3.13.7`. Run `env -u AGENT uv sync --project orchestrator --all-extras --frozen`. uv reads `orchestrator/.python-version` and installs Raidar's locked uv into the project environment. Check with `env -u AGENT make toolchain-pin-check`.
4. Docker: this machine uses the remote **`mbp`** target; see [Set up the `mbp` Docker target](#set-up-the-mbp-docker-target) below. Mark a target `trusted = true` only with the user's decision. Install the client Compose plugin with `brew install docker-compose`, and add `"cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"]` to `~/.docker/config.json` (preserve existing keys). Confirm `docker compose version`.
5. Restore all nine registry problems: `var/` does not travel. Run `env -u AGENT make problem-import PROBLEM="scb/<name>@38d627ecf668"` for each of `circuit_eval.checkpoint_1`, `code_search`, `dag_execution`, `forge`, `log_query`, `metric_transform_lang`, `datagate`, `execution_server`, and `mocked_http`.
6. Install the check-only commit hook: `env -u AGENT uv run --project orchestrator --extra dev pre-commit install --hook-type pre-commit`. It runs `make gate-commit` (whole-tree paths then `make quality`). `make gate-ci` adds slow and quality tiers and runs on every push and pull request.
7. Run `env -u AGENT make gate-commit`. Fast-tier coverage floors are unchanged.
8. Run `git fetch origin --tags` before `env -u AGENT make contract-build` if you need the wheels.

### Set up the `mbp` Docker target
`mbp` is the user's spare Apple-silicon Mac: aarch64, OrbStack, Docker 29.8.2, 10 CPUs, about 25 GB memory. It is **shared** with other projects. Never touch `native-*` containers, and never stop, remove or prune anything that is not Raidar's.

1. **Tailscale:** join the same tailnet. `tailscale status` must list `adams-macbook-pro`, and OrbStack must be running on `mbp`.
2. **SSH:** this machine's public key must be in `mbp`'s `~adamjackson/.ssh/authorized_keys` (the user adds it). The tunnel runs non-interactively, so load a passphrase-protected key into the agent first: `ssh-add --apple-use-keychain ~/.ssh/<key>`. Check with `ssh -o BatchMode=yes adamjackson@adams-macbook-pro.tailf3b31b.ts.net true`; `Permission denied (publickey)` with an accepted key means the key isn't loaded.
3. **Targets file:** run `env -u AGENT make target-init`, then make `~/.config/raidar/targets.toml`:
   ```toml
   default = "mbp"

   [targets.mbp]
   kind = "docker"
   trusted = true          # user decision 2026-10-23 (ADR-002 §2.3)
   max_concurrency = 3

   [targets.mbp.transport]
   kind = "ssh-socket"
   host = "adamjackson@adams-macbook-pro.tailf3b31b.ts.net"
   identity = "~/.ssh/<key>"
   remote_socket = "/Users/adamjackson/.orbstack/run/docker.sock"
   ```
4. **Tunnel:** run `env -u AGENT make target-up TARGET=mbp`, then `env -u AGENT make target-status TARGET=mbp`. Expect `"healthy": true` and `"arch": "aarch64"`. Raidar owns the Docker context `raidar-mbp`, and the tunnel log is `~/.raidar/run/mbp.log`. If the tunnel goes stale, run `make target-down TARGET=mbp`, then `target-up`.
5. **Pinned images on `mbp`: load, never rebuild, never prune.** Validations and locks pin each grader's image ID, and the verifier refuses any other ID (`refuse_grader_mismatch`). Builds are not reproducible, and a missing tag triggers a rebuild with a new ID, which is then refused. The IDs also depend on the daemon's image store: `mbp`'s classic store reports config digests, while a containerd store (OrbStack's default elsewhere) reports index digests. So graded trials run on `mbp`.

   | Image | ID on `mbp` (`docker image inspect --format '{{.Id}}'`) | Problems |
   |---|---|---|
   | `raidar-oracle-grader:963c14e63ff5e15d` | `sha256:e0044b07679a…` | circuit_eval.checkpoint_1, code_search, dag_execution, forge, log_query, metric_transform_lang |
   | `raidar-oracle-grader:81cc253926b5099e` | `sha256:1e1af4686cbf…` | datagate |
   | `raidar-oracle-grader:4d9f312562ca60cb` | `sha256:4154e005ff32…` | execution_server |
   | `raidar-oracle-grader:2cf4f359dd32f0b6` | `sha256:9a697be7c29f…` | mocked_http |
   | `raidar/pack-python:2b91a52663e099f2` | built 2026-10-31; its tree matches the committed installed-tree records | all package arms |

   The full grader IDs are `environment.grader_environment_id` in `lab/problems/registry/scb/*/38d627ecf668.validation.json`; compare them before grading. The pack tag is a recipe hash, not a byte identity: `mbp`'s own 2026-09-30 build differs, and it is kept as `raidar/pack-python:2b91a52663e099f2-mbp-20260930`.

   If the images are ever lost, reload them from `~/Documents/raidar-grader-images/` on this machine, outside the git checkout. The adjacent `.sha256` files contain old absolute paths: hash the tarballs directly with `shasum -a 256 ~/Documents/raidar-grader-images/*.tar` and compare the digests. `oracle-graders-2026-11-04.tar` must hash to `689db9c15dfb7a02181a711f0b900040cf9892b014022d87e6ee967dae6053ef`; `pack-python-2b91a52663e099f2-2026-10-31.tar` must hash to `8d71ef0b7cf6ebd3dc6591d259858fc7bd4eb2aa2434e685880e5b05d3d80e4e`. Then run `docker --context raidar-mbp load -i ~/Documents/raidar-grader-images/oracle-graders-2026-11-04.tar` and the same for the pack tarball. Loading into `mbp`'s classic store reproduces the pinned IDs. Without the archives, revalidate all nine problems on newly built graders before graded trials.
6. **First test (no model):**
   1. `env -u AGENT make toolchain-pin-check`, then `env -u AGENT make quality`.
   2. `env -u AGENT make test-docker TARGET=mbp`.
   3. `git fetch origin --tags`, then `env -u AGENT make security-gate TARGET=mbp`. Expect 36 passed.
   4. `env -u AGENT make perf-gate TARGET=mbp`.
   5. A graded oracle trial, which checks the grader pins end to end: `env -u AGENT make experiment-execute EXPERIMENT=lab/experiments/smoke/python__circuit_eval-cp1__phase5-at1/experiment.yaml AGENT_OVERRIDE=oracle TARGET=mbp`. Smoke jobs are never citable.
   6. `env -u AGENT make experiment-locks-check`.

   Don't use `make stage-check` as a readiness check. It is for new or changed packages. `raidar/define`'s canary record holds resource floors measured on one host, so a re-run always refuses with "different bytes"; gate-only packages re-run as no-ops on the same pack image.

   The old machine last ran this sequence on 2026-11-05, with the `mbp` tunnel up, and saw:
   - `make quality`: 5,154 fast tests passed;
   - `make test-docker`: 10 passed;
   - `make security-gate`: 36 passed;
   - `make perf-gate`: every `mbp` budget passed, with warm environment start p95 at 4.62 s and 4.70 s against 5.5 s;
   - the oracle trial: `valid=True reached=1 [solved]`;
   - `make experiment-locks-check`: all locks current;
   - all nine grader IDs on `mbp` equal the validation records.

### What did not travel
- `var/`: jobs, assessment stores, Docker logs and execution evidence. Installed-tree and
  stop-canary records do travel in tracked `lab/stage-checks/`; the decisions are committed
  in `docs/reboot/evidence/`. The untracked run outputs aren't needed to continue.
- The old machine's memory notes and OrbStack data.

### Next work: the user chooses
- **Phase 6 (health):** health becomes the sole validity producer, with an explicit `not applicable` label; see §9.
- **Or FXL's runtime work:** the Factory X Loops project builds its `stage/1` runtime against `raidar-contract` 2.0.0. Its blockers R1, R2 and R5 are cleared, and R3's `raidar/test-evidence` package is phase 10.

### Open items
- The `mbp` check sequence passed on 2026-11-05. The AT1 and AT2 real-model smokes have not been re-run on `mbp` since the primitive identity changes; run them with the user's approval.
- `claude-code` isolation is `partial`; measured Claude arms need `full`.
- Claude Code credentials: `claude setup-token` into Infisical as `CLAUDE_CODE_OAUTH_TOKEN`.
- The fast and slow tiers miss their prospective 30 s and 300 s budgets (about 137 s and 390 s).
- mocked_http has one varying hidden test in every validation run. The checkpoints affected per run, oldest first: 7, 5, 7, 6, then 6 (the last two on `mbp`, 2026-11-04 and 2026-11-05). The varying test differs between runs. The cause is unknown; the classification as upstream test nondeterminism is provisional. Investigate it before any measured experiment binds mocked_http or datagate.
- Nothing checks the `umask 022;` prefixes in `orchestrator/src/raidar/engine/trial/provision.py`. The test fakes strip the prefix, and no Docker or security test checks the modes of provisioned files.
- If the project environment has no `uv` binary, `uv.find_uv_bin()` falls back to `~/.local/bin/uv` (and the base-prefix scripts folder), not to PATH. Only grading then refuses a different version, through the resolver identity check in `grading/provision.py`. Native validation, stage locks, ST5 and tool provisioning would run the fallback unchecked. Re-running `uv sync` restores the locked binary.
