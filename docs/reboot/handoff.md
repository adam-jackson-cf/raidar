# Raidar handoff: continuing on a new machine (2026-11-03)

Give this file to the agent that picks up the work. It is self-contained; read it top to bottom before acting.

## Prompt

You are continuing Raidar's redesign into a neutral, open-source evaluation lab. An **arm** is an agent plus a workflow. A workflow is Raidar's fixed build frame plus user-authored **stage packages**, written to the `stage/1` contract over Raidar-owned **primitives**.

Before changing anything, read:
1. `AGENTS.md` and `CLAUDE.md` (repository rules);
2. `split-design.md`, the binding design: the status entries at the top, §5.8 Contract, and §9 Implementation plan (the phase table is the progress record);
3. `.agents/skills/test-patterns/SKILL.md` and its `references/test-tiers.md` (binding for every test).

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
- **The test suite follows the test-patterns tiers** (fast, slow, quality, docker, security, perf). The fast-tier coverage floors are 99.43% line and 98.72% branch, compared as integer counts. Never lower them.
- **All nine SCB problems are `validated_partial`**, revalidated with 5 repeats under grading digest `a93cc5f7…` (2026-11-04, after the `lab/stage-checks/` cutover). All experiment locks are current.
- **Every Docker check ran on the old machine's local OrbStack**, standing in for the remote `mbp` target (user decision 2026-10-31). A re-run on a trusted remote target is still pending, though not blocking.
- **Stage-check evidence travels with the checkout** in tracked `lab/stage-checks/`, outside
  package folders so released package digests stay unchanged. Installed-tree and stop-canary
  records use this canonical store, with no `var/` fallback; execution logs remain under `var/`.
  The committed installed-tree records cover **arm64 only**. An arm64 host can check the
  existing locks without regenerating evidence; an **amd64 host needs `make stage-check`
  plus a relock** before running those arms (see setup below).

### How decisions are made (user rule since 2026-10-28)
- The orchestrating agent only orchestrates, verifies and reports.
- Every design or remediation decision goes to two independent read-only voters: Claude Opus 5.5 (`anthropic/claude-opus-5-5`) and GPT-6 Astra (`openai-codex/gpt-6-astra`). Adopt the majority. On a split, GPT-6.1 Sol (`openai-codex/gpt-6.1-sol`) chooses one voter's full answer. Report every vote.
- Implementers run headless, as GPT-6.1 Sol at high effort. Afterwards, run `make quality` yourself and spot-check.
- File the vote records under `docs/reboot/evidence/decide-<topic>-<voter>.md`.

### Rules that bit us
- **Released `@N` versions are frozen,** and `lab/releases.yaml` is append-only. Never hand-edit `*.lock.json`; relock only with `make experiment-lock EXPERIMENT=<path to experiment.yaml>`.
- **Identity-bearing bytes:** the grading digest closure (`grading_execution_digest()` in `orchestrator/src/raidar/grading/records.py`), `primitive_code`, and released package bytes.
  - Changing them invalidates the problem validations (RT7), and a revalidation takes about 6 h for 9 problems × 5 repeats.
  - Test-only work must never change them. Any identity change gets its own reviewed commit, with before/after values and a scheduled revalidation.
  - The grading digest includes the exact host Python version. `.python-version` and CI pin **3.13.7**; a different patch version changes the digest and fails RT7 against the current validation pins. `requires-python = ">=3.12"` remains the package compatibility range, not the validated grading interpreter.
- **Prefix `make`, `uv` and `pytest` with `env -u AGENT`.**
- **Real-model trials need the user's approval.** Agents sign in with the local ChatGPT subscription (`CODEX_AUTH_MODE=chatgpt`), never the API key. The API keys are for Jev only, the judge model reached through OpenRouter. Secrets come from Infisical; never print them.
- **Report format:** an Activity Summary, then key points K1..Kn. Plain language; separate what was observed from what is inferred. State measured claims with sample size and an interval, or label them provisional.

### Set up this machine
1. Clone, check out the working branch (or `main` once the PR has merged), and run `env -u AGENT make help`.
2. Install and log in to Infisical (project `ae2a56a4-28d9-429f-bb7c-7d45e26b9ffa`, env `dev`), and run `codex login`, which uses the ChatGPT subscription.
3. Sync the host environment with `env -u AGENT uv sync --project orchestrator --python 3.13.7 --all-extras --frozen`, replacing any incompatible environment with the pinned interpreter.
4. Docker: create `~/.config/raidar/targets.toml` (`env -u AGENT make target-init`), and mark a target `trusted = true` only with the user's decision. The old machine used a work-only file, `var/tmp/targets-local.toml`, that marked OrbStack trusted; it did not travel.
   On amd64, deliberately delete the affected installed-tree records in `lab/stage-checks/`,
   then run `env -u AGENT make stage-check NAME=<ns/name> TARGET=<trusted-target>` for the
   needed packages and `env -u AGENT make experiment-lock EXPERIMENT=<path to experiment.yaml>`
   for dependent experiments. Stage-check refuses different bytes under the same identity;
   identical bytes are a no-op. Delete a canary record only when intentionally replacing
   its evidence, then regenerate and relock too. Do not silently replace observed resource
   floors. `make releases-check` requires installed-tree evidence for each architecture
   pinned by committed locks and refuses records naming unregistered package digests.
5. Run `env -u AGENT make quality`. Expect it to pass, with the fast tier at about 5,090 tests.
6. Run `git fetch origin --tags` before `env -u AGENT make contract-build` if you need the wheels.

### What did not travel
- `var/`: jobs, assessment stores, Docker logs and execution evidence. Installed-tree and
  stop-canary records do travel in tracked `lab/stage-checks/`; the decisions are committed
  in `docs/reboot/evidence/`. The untracked run outputs aren't needed to continue.
- The old machine's memory notes and OrbStack data.

### Next work: the user chooses
- **Phase 6 (health):** health becomes the sole validity producer, with an explicit `not applicable` label; see §9.
- **Or FXL's runtime work:** the Factory X Loops project builds its `stage/1` runtime against `raidar-contract` 2.0.0. Its blockers R1, R2 and R5 are cleared, and R3's `raidar/test-evidence` package is phase 10.

### Open items
- Re-run the Docker checks (`make test-docker`, `make security-gate`, `make perf-gate`, the stage-checks) on a trusted remote target. Run `git fetch origin --tags` before `env -u AGENT make security-gate` so the archived negative-control source is available.
- `claude-code` isolation is `partial`; measured Claude arms need `full`.
- Claude Code credentials: `claude setup-token` into Infisical as `CLAUDE_CODE_OAUTH_TOKEN`.
- The fast and slow tiers miss their prospective 30 s and 300 s budgets (about 137 s and 390 s).
- mocked_http's varying hidden tests: 7 in one validation run, 5 in the next. The cause is unknown; the classification as upstream test nondeterminism is provisional.
