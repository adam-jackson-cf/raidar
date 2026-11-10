<div align="center">

<h1>Raidar</h1>

**Controlled experiments on coding agents: does a workflow (a build prompt plus stage packages such as a verify gate or a feature definition, all in one agent session per checkpoint) change what an agent + model delivers on pinned problems, and which agent does best?**

![Status](https://img.shields.io/badge/status-active-brightgreen.svg?style=flat-square)
![Python](https://img.shields.io/badge/python-3.12-blue.svg?style=flat-square)
![Runtime](https://img.shields.io/badge/runtime-uv%20%7C%20docker%20%7C%20harbor-lightgrey.svg?style=flat-square)
![Primary CLI](https://img.shields.io/badge/cli-raidar-orange.svg?style=flat-square)

</div>

Raidar runs an **experiment**: the same pinned **problems** under two or more **arms** (an agent release plus a workflow release), repeated in counterbalanced **attempts**. One execution of a locked experiment is a **job**; every **trial** in it runs through [Harbor](https://pypi.org/project/harbor/) in the runtime pack's Docker image and each checkpoint is graded by a host-only **verifier**. **Scorers** turn stored trials into **Assessments** offline. The analysis reports paired effects against the baseline arm with a verdict of `improved`, `worse`, `inconclusive` or `insufficient`; a benchmark adds a ranking of its agents. The review surface shows the trials and their Quality components.

Raidar uses [Harbor's vocabulary](#concepts) wherever Harbor has a term (job, trial, step result, attempts, verifier, reward, agent name, environment type, metric), and keeps SCB's _problem_ and _checkpoint_ and its own _experiment_, _arm_, _agent release_, _workflow release_, _lock_ and _verdict_.

The binding design is [`split-design.md`](split-design.md): arms, workflows and stage packages, problem onboarding, health, measurement and the implementation plan. Earlier design records live under [`docs/reboot/`](docs/reboot/).

Host setup uses Python **3.13.7**, pinned in `.python-version` and CI, and host
`uv >=0.10.0` as a bootstrap tool only:

```sh
env -u AGENT uv sync --project orchestrator --python 3.13.7 --all-extras --frozen
```

Result-affecting Raidar uv calls use the exact uv dependency locked in
`orchestrator/uv.lock`, not the host uv.

Install the locked maintenance formatter with `npm ci --ignore-scripts`.
`make toolchain-parity-check` checks installer/README/lock agreement and exact
tool pins; `make prettier-check` checks maintained Markdown/YAML with Prettier
3.6.2. Its scope excludes `docs/` because the owner has not consented to reading
or formatting it, I2 byte-frozen release packages and grading payloads, and the
generated ledger index (whose bytes are checked by `make ledger-check`).
Neither gate fixes files. `make dependency-audit-check` audits exported locked
requirements with the pinned dev auditor on dependency-changing commits; CI
always audits (`AUDIT_ARGS=--ci`). `make env-setup` selects all declared extras.

Releases are on-demand PRs, not post-merge writes. Fetch origin, start a release
branch at `origin/main`, then run `env -u AGENT make version-release`. It refuses
histories without a version bump and prints the `chore(release): X.Y.Z` commit
subject. Commit the three generated files and use the normal gated PR flow.
`env -u AGENT make version-check` checks CLI, lock and CHANGELOG consistency as
part of `make quality`; regenerate a release after rebasing onto newer main.
Experiment toolchains record Python, LiteLLM and `raidar_spec`, not the release
version, so a version-only release does not require relocking.

The commit gate runs static checks, all fast tests and affected slow/quality seams.
`scripts/checks/repository-owners.json` declares file ownership, test tiers and seam
owners. New tests must declare their behavior and tier there; new capability data
must declare its exact owner. Unknown changes or stale seam destinations run all
local T2 seams rather than skipping coverage. CI always runs the full slow and
quality tiers.

The grading digest includes the exact host Python version; a different patch version
changes the digest and fails RT7 against the current validation pins. Package compatibility
remains `requires-python = ">=3.12"`; validated grading requires the pinned interpreter.

The public stage-package authoring contract is [`lab/stages/README.md`](lab/stages/README.md):
`stage/1`, calls and canaries, `criteria/1`, immutable references and `stage-tree/1` identity.
The portable schemas and digests have one implementation in [`contract/`](contract/),
distribution `raidar-contract` (import `raidar_contract`), with only pydantic and PyYAML
runtime dependencies. The orchestrator pins `2.0.0` through the same local editable package.
`make contract-build CONTRACT_DIST_DIR=<new-dir>` builds a wheel and sdist, prints SHA-256 hashes
and refuses existing output; it publishes nothing. Private artifacts for frozen `1.0.0`,
portable-call `1.1.0` and policy-split `2.0.0` live under `var/contract/v<version>/`, pinned by
[`contract/releases/`](contract/releases/). Consumers install exact-version/hash-enforced artifacts,
never build from a Git tag. `contract-v1.1.0` and `contract-v2.0.0` identify the releases.
The 63 shared conformance cases ship in both releases:
`python -m raidar_contract.conformance.check` runs them without Raidar or Harbor.
`2.0.0` retains transport-neutral agent schemas and token/audit records; Harbor ids, isolation
and capability policy, credentials/reset shell paths, lab registers and stage locations remain
in the orchestrator. `raidar_contract.CONTRACTS` still maps stage/workflow/criteria/stage-tree/agent
to `1`/`2`/`1`/`1`/`1`; the distribution split changes no package digest or stage input/refusal.
The three shipped workflows now use schema 2 and the generic package runtime. Unmarked arms run
`raidar/one-shot@2` (no stages); `workflow-new` copies `raidar/one-shot-verify`, with verify and
zero fix turns. `raidar/define-build-verify@2` consumes the define package's `criteria/1`
through `acceptance-reply/1` and `jev-judge/1`. Execution is package-only; archived spike
classes and schema-1 workflows are not runtime alternatives.

The normally locked AT1 authoring fixture is
[`python__circuit_eval-cp1__phase5-at1`](lab/experiments/smoke/python__circuit_eval-cp1__phase5-at1/experiment.yaml);
AT2 is the corresponding [`define/build/judge smoke`](lab/experiments/smoke/python__circuit_eval-cp1__phase5-at2/experiment.yaml).
Both select codex `gpt-6-luna` with low reasoning. Their real-model execution requires approval
and `CODEX_AUTH_MODE=chatgpt`; Jev uses its separate API credentials. No-model scripted builders
and a local Jev transport prove mechanics, not model or judge quality.

The upstream [`raidar/verify@1`](lab/stages/raidar/verify/) package runs the spike's four checks
with the same pinned tools: lint, format, strict types and individual pytest outcomes.
Its `test_timeout` parameter controls the actual per-test deadline (default 60 seconds);
whole checks have the `stage/1` ceiling of 300 seconds, rather than the spike's 600 seconds.
Run its offline gate fixtures with `make stage-check NAME=raidar/verify TARGET=local`.
[`raidar/define@1`](lab/stages/raidar/define/) owns the standalone definition tool and emits
the sealed definition plus `criteria/1`. Its stop call has a 60-second timeout and at most
three resume turns. Malformed agent drafts receive one repair opportunity, then close.
[`raidar/docs-check@1`](lab/stages/raidar/docs-check/) runs pinned codespell over repository
text. `make stage-new NAME=acme/my-check` copies that real package by default; `FROM=ns/name`
selects another source. Only the manifest name changes; commands, skills, fixtures and modes stay.

Problem validation scores only repeat-stable reference cases. A checkpoint must add a
previously uncollected test its own reference passes in every repeat, or that checkpoint
alone is excluded as `no_new_scoreable_tests`. Identical reference sources pool positive
and negative repeats; a varying case is removed everywhere it appears. The earlier-reference
comparison is a reading, not an exclusion. Native parity remains required for full validation.

> **History.** The scenario, revision and matrix workflow was retired at the reboot cutover: no code, target or check reads `scenarios/`, `matrices/`, `environments/` or `spikes/` any more, and they have been removed from the tree. Their definitions are preserved at tag `archive/scenario-epoch`, and [`CHANGELOG.md`](CHANGELOG.md) maps each removed target to its replacement. The review surface no longer reads scenario-epoch history.

## Install

Prerequisites:

- Host `uv >=0.10.0` for bootstrap only; Raidar uses its locked uv dependency at runtime
- Docker with `docker compose` 2.40.1 or newer, local or on another host. Each machine names its Docker targets in `~/.config/raidar/targets.toml` (`make target-init` writes a commented template); select one with `TARGET=<name>` or the file's `default`. A remote target is reached through a supervised SSH-forwarded socket (`make target-up TARGET=<name>`, `make target-status`), and hidden tests and agent credentials go only to targets marked `trusted = true`.
- Node 18 or newer, for the review surface
- Agent credentials: file-backed Codex auth (`make codex-auth-setup`) or `OPENAI_API_KEY` for `codex`; `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` or `CLAUDE_CODE_OAUTH_TOKEN` for `claude-code`
- Optional but preferred: the [Infisical CLI](https://infisical.com/docs/cli/overview), logged in once with `infisical login` in a real terminal (it is interactive)

```bash
make env-setup
```

Secrets live in the Infisical project `mac-os-cf` (env `dev`), bound by `orchestrator/.infisical.json`. The repository ships no `orchestrator/.env`. Targets that call a model provider (`experiment-execute`, the smoke targets, `experiment-assess`, `jev-shadow-rubric`, `agent-validate`) run under `infisical run` when the CLI is installed and logged in; each prints which source it used, never a value. Without Infisical they read `orchestrator/.env` if you created one from `orchestrator/.env.example`, and otherwise say `secrets: none` and use only the shell environment and file-backed Codex auth. `SECRETS=infisical` fails instead of falling back, `SECRETS=dotenv` skips Infisical and fails when `orchestrator/.env` is missing, and `INFISICAL_ENV=` picks another environment. Before any trial starts, `experiment-execute` and the smoke targets check that every selected arm's agent has a credential (presence only) and name the agent field that lacks one.

For Codex CLI the default auth policy is `CODEX_AUTH_MODE=auto`: it prefers file-backed ChatGPT login from `~/.codex/auth.json` and falls back to `OPENAI_API_KEY`. A login kept in the OS keyring cannot be passed into Harbor, so switch Codex to file-backed credential storage first. `make help` lists every public target.

## Concepts

| Term                  | What it is                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | Where it lives                                                                                                                                                |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Experiment            | The unit of authoring: a `kind`, one runtime, problems, agents, arms, scorers and the analysis plan. Its id is `<runtime>__<subject>__<intent>`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | `lab/experiments/<kind>/<id>/experiment.yaml`                                                                                                                 |
| Kind                  | `ab-test` (arms are compared, typically the same agent under different workflows), `benchmark` (agents are compared under one workflow: paired verdicts against a reference agent plus a ranking view with mean ± CI, pass@k, cost and tokens) or `smoke` (the same engine; reports are stamped not citable). `research-loop` is reserved. The kind is part of the lock's execution identity                                                                                                                                                                                                                                                                                                                                                                                                                                                    | `kind:`                                                                                                                                                       |
| Lock                  | Content hashes and pinned versions of everything the experiment resolves to, in `execution`, `assessment`, `analysis` and `toolchain` sections. Its `lock_sha256` covers the execution section only, and every job records it. A job refuses a lock that is out of date, and no lock is written for a design its runtime cannot run (`RT0`-`RT6`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | `experiment.lock.json` next to the experiment                                                                                                                 |
| Runtime and pack      | A runtime is a language toolchain and equals a runtime pack id (`python`; `node-ts`, `go`, `rust`, `java`, `dotnet` later). The pack is the agent image (toolchain and pinned agent CLIs, no scorer tools) plus the scorer tool lock                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | `runtime:`; `orchestrator/src/raidar/subject/environment/<runtime>/` (code), `lab/environments/<runtime>/` (recipe: `pack.yaml`, `tools.lock.yaml`, `image/`) |
| Problem (binding)     | A pinned, verifier-validated task (one-shot or multi-checkpoint) imported against one runtime pack: `scb/<problem>` is the python binding, another runtime's binding would be `scb-<runtime>/<problem>`. Its verifier bundle stays on the host and the agent never sees it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | `lab/problems/registry/`, imported into `var/problems/store/`                                                                                                 |
| Stage package         | An author-owned prompt, optional skills/tool, setup/gate/stop calls and declared outputs under the strict `stage/1` contract. Packages contribute to the checkpoint's primary session, never a host plugin or scorer.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | `lab/stages/<ns>/<name>/`; `lab/stages/README.md`                                                                                                             |
| Primitive             | Raidar-owned execution guarantees, named by contract id: `frame/1`, `install/1`, `setup/1`, `gate/1`, `stop/1`, `outputs/1`, plus workflow-selected `acceptance-reply/1` and `jev-judge/1`. Their code identities and package identities cannot be overridden when pooling.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | `subject/primitive/`; lock `primitive_code` and `packages`                                                                                                    |
| Build frame           | The workflow's prompt, with `%%%TASK%%%` and one slot per enabled package, each once.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | Workflow `build.prompt`                                                                                                                                       |
| Primary session       | The checkpoint's one agent conversation; fixes, repairs and rework resume it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Host-written workflow turn/session records                                                                                                                    |
| Agent release         | A pinned agent: an adapter (`codex@3` or `claude-code@2`), provider, tool pin, and the runtimes it covers, defined in `lab/agents/<name>.yaml` and registered in `lab/releases.yaml` as `name@N`. An experiment's `agents:` entry names a release and sets the `model` and `reasoning`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | `lab/agents/<name>.yaml`; `lab/releases.yaml`; `agents:`                                                                                                      |
| Workflow release      | A Harbor workflow runner defined in data: schema 2 combines a build prompt with released `stage/1` packages in one primary session. `raidar/one-shot@2` has no stages; `raidar/one-shot-verify@2` adds verify with no fix turns. `raidar/define-build-verify@2` adds define, an acceptance reply and aspect judging of `criteria/1`. Tools, skills and `.raidar/` state are excluded from graded snapshots and commits. References require `ns/name@N`; bare register entries remain frozen history and never resolve.                                                                                                                                                                                                                                                                                                                          | `lab/workflows/<ns>/<name>/workflow.yaml`; `lab/releases.yaml`; `workflow:`                                                                                   |
| Arm                   | One agent on one workflow release: `{id, agent, workflow}`, where `workflow: <ns>/<name>@N` is optional and defaults to `raidar/one-shot@2`. Arms are paired against the `baseline_arm`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | `arms:`                                                                                                                                                       |
| Attempts              | The replicates of every arm: each attempt runs every arm once per problem, in a seeded, counterbalanced order. Analysis pairs arms by attempt index                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | `analysis.attempts`                                                                                                                                           |
| Job                   | One execution of a locked experiment: its trials, their step results, validity checks and N1 phase timings                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | `var/jobs/<experiment id>/<job id>/` (`job.json`, `job.lock.json`, `trial-records/`)                                                                          |
| Trial and step result | A trial is one arm × attempt × problem through every checkpoint (a Harbor trial, id `bNN-pN-<arm>`, where `bNN` is the attempt index); a step result is one checkpoint of it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | `trials/<trial>/`, `trial-records/<trial>.json`                                                                                                               |
| Verifier and reward   | The hidden, trusted grader: strict all-cases per checkpoint, run host-side; its per-step output is the reward. Harbor's `oracle` agent is something else (it replays reference solutions)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | `orchestrator/src/raidar/grading/`                                                                                                                            |
| Scorer and Assessment | A versioned scorer (`correctness@10`, `code-quality@9`, `resource-usage@8`, `process-compliance@12`, `verification-effort@1`) runs offline over stored trials. Correctness reports the lock-pinned solvable-set outcome alongside the raw SCB outcome; excluded steps leave its denominator, while missing sets are unavailable, never zero. Process compliance reads the work session's trace, not gate fix sessions, and leaves undecidable checks insufficient. Verification effort reads the host-written `gate/1` records (seals, first-pass and step-end cleanliness, failed blocking checks, fix turns, their cost and the fix turns' pre-fix correctness); arms without a `gate/1` package are not applicable. Code quality reads the graded snapshots and retained or offline verifier coverage; its regression breadth excludes at m… |
| Headline metric       | How quality scores combine into the headline S (`scb-canonical@1/@2`, `scb-canonical-oneshot@1/@2`). The lock pins it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | `assessment.metric`                                                                                                                                           |
| Analysis              | Paired deltas, confidence intervals, curves, verdicts, exclusions and a sample-size projection against the baseline arm; a benchmark adds its ranking view                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | the `make experiment-analyze` output directory                                                                                                                |
| Jev                   | Host-side model judgement. `raidar/define-build-verify@2` uses pinned `typesafe/jev-1.13-20260917` through `jev-judge/1`. The offline coverage scorers use the same model, retaining requests, responses and interpreted answers without the OpenRouter key. The older offline policy machinery remains parked with both policies `off`; these capabilities add no headline metric.                                                                                                                                                                                                                                                                                                                                                                                                                                                             | `raidar/judge/` shared transport; `scoring/judge/` offline policy and calibration                                                                             |

Inside each Harbor trial directory the verifier keeps its original evidence names (`raidar-oracle/steps/<step>/record.json`, reward key `oracle_leak`): they are wire names that released scorers and `make assess-regrade` read the same way for every job. For the same reason released scorers keep the observation subject `trajectory`, the evidence input `oracle.cases` and their `@1` descriptions (all hashed into Assessment keys), and the problem store keeps each problem's `oracle/` bundle directory (inside the problem's pinned digest).

The native-agent boundary owns isolation, process facts, accounting, final text, resume storage,
session audits, credential preflight and reset state paths (`subject/agent/protocol.py`).
Harness implementations are private under `subject/agent/adapters/`. Codex contexts contain
cumulative session usage; Claude's stream result contains the current invocation's usage.
The runner records normalized turn deltas/session totals and preserves the native session store.
P8 checks adapter-produced audits, retains developer/system message digests by harness version
and verifies source-record hashes. Missing evidence is `unmeasured`, not `pass`.
Claude `2.1.285` lists skill names without roots, so its release declares partial isolation proof;
native bundled plugins are not host plugins. The three `python__forge__claude-*` smoke
experiments mirror the Codex workflows; they are operational checks, not effectiveness evidence.

`raidar/define-build-verify@2` uses `{account, ac_tests, adrs}` replies and its pinned judge.
Each builder turn gets one syntax and one semantic repair; criterion-to-test mappings merge
across turns. The package lifecycle seals the gate, runs stops, loads copied criteria, checks
the reply, then judges. A gate fix therefore precedes reply repair, unlike the archived spike.
The judge
sees mapped tests plus one hop of test helpers, never production code. Aspects close at
probability ≥0.8, open at ≤0.3, and otherwise remain undecided. Open/undecided criteria
produce pushback in the same session; a rework starts only when its predicted turn,
gate and judge fit the remaining budget. Endings distinguish `verified`, `pass_limit`,
`no_progress`, `budget_exhausted`, `deadline`, `gate_refused` and `gate_error`.
`verified` is a workflow judgement, not a hidden-grader correctness verdict.

The seal always reruns checks as the agent's user and records the checked workspace; it
never commits, restores or stages the agent's work. A refusal with no fix turns ends
`gate_refused`, and the hidden verifier grades the workspace the agent left, including
after a deadline or cancellation. `max_fix_rounds` defaults to zero. Baseline checks
also run as the agent; the separate deterministic setup check executes no task code.
Root installs immutable tools; there is no root hook broker. Hooks run as the agent's
user and append to an agent-writable log. W5's hook-log condition and W11's `hook_log_*` findings
are readings, never validity. Independently measured tool, installed-tree, watched-path,
protected-path and session-log integrity remain validity; no live stage-keyed G/E reader remains.
The seal keeps its protected record channel and call supervisor; it disables Linux
dumpability so checks cannot reopen its record pipes through `/proc`.
Authoritative workflow, setup, baseline, seal and session-audit records are retained
host-side. Health W1–W11 make missing required primitive evidence, setup failure and recorded
integrity violations invalid trials, not merely unmeasured outcomes. An unusable
definition stays unusable for its checkpoint; zero criteria cannot yield `verified`.
Applicability comes from schema-2 host config and locked primitive ids, not stage names.
Digest-checked package manifests supply setup/stop/output obligations in host-only config,
outside package/lock identity; missing or inconsistent declarations fail closed.
W3–W7 read `gate/1`, W8–W9 read package calls/outputs, and W10 reads reply/judge evidence.
Refused canaries, agent-attributed failures and legitimate closed/cut outcomes remain valid
when their required evidence is complete. Generic health families remain phase 6.

The two coverage scorers run only at assessment time over retained define/build artifacts:
`definition-coverage@6` reads each required unit's citing criteria; `test-coverage@6`
reads each criterion's final mapped tests, including regression criteria. Their raw
`readings.json` retains level probabilities, expected levels (0–2), gaps and call evidence.
These are uncalibrated readings, not pass/fail thresholds or headline scores. Arms without
define are `not_applicable`; missing evidence is insufficient, not zero.

## Walkthrough: experiment.yaml to review

The example is an `ab-test` comparing a baseline arm with a treatment arm on the validated one-shot problem the pilot uses. Every step is a public `make` target.

Choose an objective before designing or running an experiment:

- **Explore** (default): check the smallest case first, then at most 4 attempts per arm on one problem with codex/gpt-6-luna/low. Lock and pre-register, stop on process defects, and report exploratory signals and cost rather than evidence of an effect. A verdict below `min_pairs` is `insufficient` by design.
- **Confirm**: use a settled design that completed a clean explore run. Pre-register the primary outcome in `rationale.md`, lock the thresholds, and size attempts from the earlier analysis's J2 sample-size projection. Run all arms unchanged under one lock, with at most one infrastructure exclusion per arm. A Raidar or workflow change starts a new experiment. Report confidence intervals, cost, gate effort and quality; only confirm results are citable.

Objectives are separate from kinds: either an ab-test or a benchmark can explore or confirm. A smoke checks operational health and is never citable.

**1. Provide the problem and the pack.** Import and validate a pinned source with Make targets only; no hand-written per-problem definition is needed. Generated manifests and validation records are committed under `lab/problems/registry/`; materialized content stays under `var/`. `make problem-list SOURCE=scb@<commit>` lists source candidates, and `make problem-info SOURCE=scb@<commit> PROBLEM=<ref>` inspects one before import. Without `SOURCE`, listing shows registered verdicts and per-step solvable-set sizes and nondeterministic counts.

Each task instruction is the pinned SCB `checkpoint_N.md`, including its canary, with only the entry-command and entry-file tokens substituted. `instruction_sha256` is the hash of this rendered checkpoint spec; the pin records the grader's exact entry command. Validation defaults to `REPEATS=5` and records each step's **solvable set**: the cases passed by the reference in every repeat, including regression cases. Records say "no variation observed in N runs" for that reference and environment, not that a case is deterministic; submissions are graded once. Varying cases are excluded; empty sets have step verdict `excluded`. Missing evidence is not a failed case.

**Declared dependencies (`declared-wheels/1`).** Import resolves each reference step's declared
set (`requirements.txt` plus `pyproject`) with uv targeting Python 3.12, PyPI only
(`https://pypi.org/simple`), wheels only (`--only-binary :all:`, so no source build can run),
for both `x86_64-manylinux_2_28` and `aarch64-manylinux_2_28`, with `exclude-newer` set to the
problem's UTC import time. A closure is capped at 200 packages, 1 GiB of wheels and 120 s of
resolution. A source-only package is refused (`no-wheel`); other unsupported requirements are
`after-cutoff`, `unresolvable`, `direct-reference`, `unparseable` or `cap-exceeded`. A set that
does not resolve, or whose architectures diverge, excludes the problem
(`dependency_unresolvable`). The manifest's `dependencies` block is part of the problem pin
and the lock's `dependencies_sha256`, so changing it forces revalidation.

Acquisition is a trusted, networked host step that never sees hidden tests or reference
code. Wheels are verified against recorded hashes and installed wheel-only, offline, with
`--require-hashes --no-deps` into a grader-image layer keyed by base image and lock digest;
`.pth` files and namespace packages are handled. Materialization is immutable and keyed by
dependency digest, so earlier content is retained. The grader stays offline (`--network none`),
non-root and capability-dropped.
Grading reuses the pin's lock when the declared set digest matches, else resolves under the
pin's policy and caps. Regrades reinstall the recorded lock and never re-resolve; a resolver
or PyPI outage is an infrastructure failure, never `unprovided` or a score. Reference
validation uses this same provisioning.

Validation pins the grader's immutable image content ID and implementation digest.
RT7 and the grading runtime refuse a mismatch; missing identity evidence prevents execution
until revalidation. Existing three-repeat records are not retroactively certified.
The implementation digest covers the complete executable grading import closure, package
initializers included, explicit subprocess/plugin roots, exactly shipped runtime bytes (no
`__pycache__`) and host Python/packaging/pydantic versions. Static distribution-version metadata
is outside that closure. The latest revalidation (2026-11-04) ran under digest `a93cc5f7…`:
all nine SCB problems are `validated_partial` and all experiment locks are current.
RT7 refuses relocking before writing when validation is stale; never hand-edit pins/locks to bypass it.
A grading crash or timeout triggers a starter canary in the same sandbox configuration:
completion (not solving the task) passes. A passing canary attributes the failed grade to
the submission; a failing canary remains infrastructure. Each step records the canary status.
Resolver timeouts allow one immediate retry: at most two attempts of 120 s per declared set
and architecture, with identical requirements and pinned policy and recorded attempts.
A second timeout is infrastructure (`dependency_resolver_unavailable`), never `cap-exceeded`
and never an agent score.
Malformed resolver version output or generated lock data is likewise infrastructure, never
a dependency refusal.

The grader's `test_dependencies` venv has its own pin, separate from the agent seed, which
resolves `test_dependencies` only and never the reference's sets. The grader pin covers
`test_dependencies` plus the grader tools. The seeded `pyproject.toml` carries
`[tool.uv] exclude-newer` and `no-build = true`; the agent user's uv and pip configuration
carry the same cutoff and wheels-only rule (hash-pinned pip 26.1.2 tooling upgrade, with no
change to the pack's bytes). Agents keep PyPI access, constrained to this policy.

Health readings D1 (`unprovided`) and D2 (`undeclared_import`) are descriptive and never
invalidate a trial. Analysis prints each arm's affected-step share beside the primary outcome
and warns when arms differ materially; an `undeclared_import` is a heuristic (a module-name
to package alias may be missed).

The default primary outcome is **reference-solvable SCB success** (`solve_rate_solvable`): checkpoints passing every case in their lock-pinned solvable set, divided by the number of non-excluded checkpoints. It is not specification compliance. `correctness@10` always reports raw SCB outcomes beside it. Every analysis shows the spec-determinacy flags and audit coverage (assertion sites audited / total); zero audited is `unmeasured`, never "0 flagged". A step explicitly excluded at validation leaves the denominator; a missing set makes the solvable trajectory unavailable, never zero. Optional **errata** remain pre-registered policy through explicit `analysis.primary_outcome`, never by default; `make problem-errata-draft` drafts their optional record. Optional sensitivities are explanatory notes, not outcome adjustments.

```bash
make problem-list SOURCE=scb@38d627ecf668
make problem-import SOURCE=scb@38d627ecf668 PROBLEMS=dag_execution,log_query
make problem-validate PROBLEMS=dag_execution,log_query REPEATS=5 TARGET=mbp
make problem-list
make pack-build
```

For a multi-step prefix, bind `{ref: scb/dag_execution@38d627ecf668, checkpoints: 2}`.
The prefix is part of the execution lock and runs as one task with a persistent
workspace. Assessment reads the retained trial's bound plan, so its subjects, rates
and analysis solvable-set summaries cover only those checkpoints. The existing
`scb/circuit_eval.checkpoint_1@38d627ecf668` one-checkpoint form still works.
`MAX_CHECKPOINTS` is only a smoke/dev override: later bound checkpoints stay planned
and `not_attempted`, not removed from the task. Other experiment kinds must bind
their checkpoint prefix. Python is the only runtime today; its full
[runtime-pack contract](lab/environments/README.md) defines support for another runtime.

**2. Write `experiment.yaml`.** `make experiment-init EXPERIMENT=lab/experiments/ab-test/python__circuit_eval-cp1__review-trial/experiment.yaml` writes a starter of the shape below: two arms on the current codex and one-shot releases, the id from the directory (or `EXPERIMENT_ID=`) and the problem from `PROBLEM=` (default the one below); the headline metric is left to the lock, which picks it by checkpoint count. It refuses a path that exists. Point the treatment arm at the workflow (or agent) release under test once it is registered in `lab/releases.yaml`; to try another release later, add an arm to the same file, so it stays paired with the baseline arm.

```yaml
schema_version: 3
kind: ab-test # ab-test | benchmark | smoke | research-loop
id: python__circuit_eval-cp1__review-trial # <runtime>__<subject>__<intent>
description: Does the treatment arm's workflow change correctness on circuit_eval?

problems:
  - ref: scb/circuit_eval.checkpoint_1@38d627ecf668

runtime: python # one runtime pack per experiment

agents:
  - id: codex
    release: codex@3 # lab/agents/codex.yaml, registered in lab/releases.yaml
    model: gpt-6-luna
    reasoning: low

arms:
  - id: baseline
    agent: codex # workflow defaults to raidar/one-shot@2
  - id: treatment
    agent: codex
    workflow: raidar/one-shot@2 # the workflow release under test (lab/workflows/<ns>/<name>/)

assessment:
  scorers: [correctness@10, code-quality@9, resource-usage@8]
  metric: scb-canonical-oneshot@1

analysis:
  baseline_arm: baseline
  attempts: 5
  seed: 20260930
  min_pairs: 5 # the lock pins these verdict thresholds
  max_exclusion_fraction: 0.2
  alpha: 0.05
```

Comparative verdicts use the outcome type declared in the lock: exact McNemar for binary
outcomes, exact sign-flip permutation tests otherwise, including aggregate effects.
Intervals describe uncertainty but never decide a verdict. Pre-register
`analysis.max_exclusion_difference = d` (an absolute exclusion-rate difference in 0–1; no default).
An arm's rate is excluded / planned slots across all causes, counting each slot once.
Refuse comparative verdicts when the largest rate minus the smallest exceeds `d`; equality passes.
Without a bound, analysis is descriptive-only and the comparative verdict is `insufficient`.
The independent 20% paired-exclusion cap remains. Exclusions are shown per arm and cause;
a non-significant difference does not establish acceptable attrition.

Aggregate tests retain the existing equal-problem-weight effect and flip its paired deltas;
the interval is not the decision rule. The exact-enumeration state limit is a visible execution
safety bound (`EXACT_STATE_LIMIT`), not an approximation or a scientific threshold.

Resource cost includes native-agent usage priced through LiteLLM's shared table plus recorded
host judge spend. Claude's self-reported dollar total is not authoritative. Missing prices,
missing judged-workflow evidence and sent judge attempts with unknown charges remain unavailable.
The first rework turn uses the recorded `first_build` duration; with the fit rule enabled,
absent that evidence it is not scheduled, with no fallback floor.
Under `halt_on_failure`, a lock-pinned excluded step still executes normally: its workspace
changes and budget are retained, and it is reported `excluded`, neither passed nor failed.
Its separate `halt_gate`/`solvable_excluded` signals permit advancement without a fake solvable
score. Missing validation, grading infrastructure failures and unexplained missing rewards halt.

Validation checks that the design does not contradict its kind (`KIND*`: an ab-test compares arms; a benchmark compares agents under one workflow, warns below 5 attempts and may set `analysis.rank_by`; `research-loop` is reserved) and its name (`NAME1`: a file under `lab/experiments/<kind>/<id>/` sits in the directory of its kind and id; `NAME2`: the id starts with the experiment's runtime).

**3. Validate and lock.** Validation checks the schema, the cross-references, the kind and naming rules, and that every agent and workflow release is the tree's current registered release (`lab/releases.yaml`, at its recorded hash) and offers the agent entry's model and reasoning. The lock pins content hashes and versions, records each arm's full identity (agent release and tool pin, model, reasoning, workflow release and hash, and the stage code that runs it) and runs the runtime-compatibility gate, reporting every issue at once: each problem binding must be validated (`RT0`) and bound to the experiment's runtime (`RT1`; a local `path:` task declares `[metadata] raidar_runtime`), imported against the current pack contract (`RT2`); every scorer and the headline metric must cover the runtime (`RT4`), each agent release's tool pin must be the pack pin (`RT5`), and the runtime must have a pack (`RT6`). The gate sees the effective spec, so `--set` overrides cannot bypass it. `raidar run` refuses to start if relocking would change the lock (naming each stale section), if an arm no longer resolves to its locked identity, or if the pack now builds another image than the locked one (`RT6` drift). A scorer, headline-metric, threshold or toolchain change relocks to the same `lock_sha256`, so existing jobs still match: rescore them (step 5) instead of rerunning. A change to the kind, problems, agents, arms, a release, the stage code, protocol, attempts or seed gives a new `lock_sha256`, which means a new experiment. `make experiment-locks-check`, part of `make quality`, checks every lock under `lab/experiments/`.

```bash
E=lab/experiments/ab-test/python__circuit_eval-cp1__review-trial/experiment.yaml
make experiment-validate EXPERIMENT=$E
make experiment-lock EXPERIMENT=$E
make experiment-plan EXPERIMENT=$E     # dry run: every trial and its hashes
```

**4. Run a job.** Each trial runs through Harbor (the pack image is built first if missing). The job directory `var/jobs/<experiment id>/<JOB_ID>` records step results, validity checks (context isolation, no test leak, progression), N1 phase timings and `job.lock.json`, the lock the job executed under. Agent processes are reaped when each agent phase ends, before its work is collected and graded.

```bash
make experiment-execute EXPERIMENT=$E JOB_ID=first DETACH=1
make experiment-status JOB=var/jobs/python__circuit_eval-cp1__review-trial/first  # monitor until finished before assessing
make perf-check JOB=var/jobs/python__circuit_eval-cp1__review-trial/first     # N1 overhead budgets (report-only)
```

On a real job, `perf-check` is report-only. It lists each phase's p95 against its accepted budget and prints breaches as warnings: over budget, `insufficient` (fewer than `MIN_SAMPLES` samples, default 10), or `missing`. It also prints the job's contention context: the configured concurrency, the peak number of trials running at once, and any other Raidar jobs under the same jobs root that overlapped in time. It exits 0 on breaches and non-zero only on malformed or empty input. `PERF_STRICT=1` makes breaches fail. For a small job, pass `MIN_SAMPLES=<trials>` to judge the budgets on what there is.

The enforced overhead check is `make perf-gate`. It runs the no-LLM fixture experiment (`make perf-fixture`) at concurrency 1 and then 3 and fails on any breach of the accepted budgets. It needs Docker, so it is a release/gate check and is not part of `make quality`.

Budgets are selected from the job's recorded execution target: `mbp` has its own accepted set; missing or unrecognised targets use the N1 reference. The report names the set used, and jobs checked together must share a target. Budget changes require an explicit decision rather than rebaselining to pass a run.

A long job must outlive the shell or agent session that starts it, so launch real-LLM jobs detached and poll them to completion. `DETACH=1` starts the job under a supervisor in its own session (kept awake with `caffeinate -i` on macOS) and returns at once. The supervisor's pid, the job's log and its exit record are in `<job dir>/detach/`. `make experiment-status` reports `state: running`, `finished`, `failed` or `died`, the trials recorded against those selected, and the log's last lines. It reads any job directory and never changes it.

```bash
make experiment-execute EXPERIMENT=$E JOB_ID=second DETACH=1
make experiment-status JOB=var/jobs/python__circuit_eval-cp1__review-trial/second
```

If a job is stopped mid-trial, `make harbor-cleanup JOB=var/jobs/python__circuit_eval-cp1__review-trial/first` removes the trial environments it left behind.

**5. Assess.** Scorers run offline over the stored trials. They write to an Assessment store outside the job and use the assessment settings of the job's lock: `LOCK=` names it, else the experiment's current lock when its `lock_sha256` is the job's (so an assessment-only relock applies); with neither the command refuses, and the job's own `job.lock.json` is never a substitute. Scorers given on the command line must cover the job's runtime, and a `--bundle` must be the verifier bundle of a problem the lock binds (`AS1`). `REPLAY=1` reruns existing Assessments and requires byte-identical output. With `verification-effort@1`, assessment first grades every not-yet-graded `gate/1` retained pre-fix tree into the store with the step's own verifier bundle, on the Docker target; `make assess-regrade ... STORE=` regrades them beside the final snapshots. With `code-quality@9`, assessment first makes the coverage run of every step whose trial deferred it into the store, on the Docker target; `make assess-regrade ... COVERAGE_CHECK=1` makes the runs again and compares them with the trial's own coverage pass or the stored run. `CONCURRENCY=N` makes the pre-fix grades and coverage runs of N trials at once (each trial's steps in order; default 1). `process-compliance@12` (the declared default) never asks a model. Everything under `var/` (jobs, stores, analysis) is git-ignored.

```bash
make experiment-assess JOB=var/jobs/python__circuit_eval-cp1__review-trial/first STORE=var/stores/review-trial \
  SCORERS="correctness@10 code-quality@9 resource-usage@8"
```

**6. Analyze.** The verdict thresholds and the assessment settings come from the lock. The report labels any command-line override. A trial whose inputs cannot be loaded is refused, never read as unassessed; a verifier bundle missing at its recorded root is read from the problem store (`lab/problems` with `var/problems`) by the trial's pinned problem reference, and refused unless its digest is the recorded one. A benchmark's report adds the ranking view; a smoke report says it is not citable.

```bash
make experiment-analyze JOBS="var/jobs/python__circuit_eval-cp1__review-trial/first" STORE=var/stores/review-trial ANALYSIS_OUT=var/analysis/review-trial
```

**7. Review.** Project the Quality element from the jobs and the store, then serve the surface with the same jobs; serve builds the UI first if `review-surface/dist` is missing (`make review-surface-build` rebuilds it). Each job's lock is the one given with `REVIEW_LOCK=<lock>` (also used by the Quality projection, or `QUALITY_LOCK=`) or the one beside the job's experiment file when its `lock_sha256` is the job's; a smoke lock under `var/tmp/smoke/` must be passed explicitly. A job whose lock cannot be found is refused, never projected from guesses. `make review-surface-test` rewrites `review-surface/data` with its fixtures plus the jobs you pass, so run the projection again before serving.

```bash
make review-surface-quality-data QUALITY_JOBS=var/jobs/python__circuit_eval-cp1__review-trial QUALITY_STORE=var/stores/review-trial
make review-surface-serve REVIEW_JOBS_ROOT=var/jobs/python__circuit_eval-cp1__review-trial REVIEW_STORE=var/stores/review-trial
```

## Smoke checks

The smoke targets run real agent trials through the same experiment path: `lab/experiments/smoke/python__circuit_eval-cp1__agent-health/` for a single agent and `lab/experiments/smoke/python__circuit_eval-cp1__pack-agent-health/` for one arm per released agent (today `codex@3` only). Both have kind `smoke`. The separate `lab/experiments/benchmark/python__circuit_eval-cp1__agents/` compares agents; it is not an operational smoke. Each smoke target locks its experiment under `var/tmp/smoke/` with its own overrides and writes jobs under `var/jobs/`.

```bash
make orchestrator-smoke                                   # codex + gpt-6-luna (low), one trial
make agent-smoke RELEASE=codex@3 MODEL=gpt-6-luna         # the same smoke with its agent release replaced
make smoke-agents                                         # one trial per released agent (arm codex)
make smoke-agents SMOKE_ARM=codex                         # one arm only
```

Before a non-smoke job runs, `make experiment-execute` requires clean smoke evidence on the same execution code, pack recipe and target, covering every selected arm's identity: agent release and tool pin, model, reasoning, workflow release and hash, and stage code. Separate clean smokes may jointly provide that coverage; only executed arms count. Grader recipes are recorded and compared by analysis pooling, not a separate smoke-admission recipe match. The command refuses missing coverage (`ALLOW_UNSMOKED=1` overrides; recorded in `job.json`). Clean includes that the agent changed `/app` in every executed step of every trial (checkpoint 1 against the seeded workspace), so a smoke where the agent wrote nothing does not count. The forge mechanics experiment is one historical multi-checkpoint example, not the default problem; `python__onboarding__need-one` prepares dag_execution and log_query for the same path.

Never use `ALLOW_UNSMOKED=1` for a confirm job. An empty override such as `REASONING_EFFORT=` unsets the field; `''` sets an empty string. An agent release's tool pin is the runtime pack's pin (`make releases-check`).

Before checkpoint 1 every trial seeds `/app` with a `pyproject.toml` naming the problem's declared test dependencies and runs `uv sync` as the agent user (a lab provision at environment start, recorded per step under `agent/raidar-provision/`); a failed or missing seed makes the step an infra error.

The historical forge mechanics smoke uses `scb/forge@38d627ecf668` through all 8 checkpoints, one attempt, and the one-shot workflow; it is spike evidence, not an onboarding default or a source of budgets for other problems. Forge's timing and UUID tie-break sensitivities remain in `lab/problems/registry/scb/forge/38d627ecf668.sensitivities.yaml`. The one-shot agent-health smoke assesses correctness only; the agent benchmark has one attempt per released agent, so it warns that it is below five attempts.

## Run health, pooling and the fault ledger

Every trial writes `health.json`: verdict, record, limit, liveness, contention, isolation and tamper checks, each `pass`, `violation` or `unmeasured`. A verdict, record/limit, isolation or tamper violation invalidates the trial; `make health JOB=<job dir>` recomputes it, and violations land as provisional lines in `<job>/fault-candidates.jsonl`. A host-side liveness monitor (`RAIDAR_LIVENESS=enforce` by default; `observe` and `off` opt out) watches each agent phase for output, CPU or network: an agent quiet for 600 s is killed and the step is verified and graded on what exists, like an agent timeout, so the trial stays valid and the kill is a provisional fault candidate (calibration: ADR-002 A.9).

Health identifiers group verifier checks (`V1`–`V3`; V3 compares the ledger digest with the stored snapshot digest, not a live workspace), records/limits (`H1`, `H3`–`H6`; H5 reads the recorded agent setup limit), liveness (`L1`–`L2`), contention (`P2`–`P3`), isolation (`P8`: instructions must match their recorded origin and content permitted before the turn; incompletely parsed transcripts are `unmeasured`), integrity (`T1` tool tree, `T2` records manifest, `T3` reaper residuals) and gate readings (`G1`, `G2`) plus tool validity (`G3`). Hook-log evidence never invalidates a trial. `T2` is unmeasured in-run and verified by `make health`. Liveness samples every 15 s by default (`RAIDAR_LIVENESS_INTERVAL_S`); `RAIDAR_LIVENESS_IDLE_S` sets the idle window. Findings distinguish `idle_killed` from observe-only `idle_observed`. Harness and workflow time limits take precedence; Raidar does not retry trials automatically.

Defining workflows also get `E1` (every accepted seal has a sealed, passing definition),
`E2` (call records agree with the final draft and inputs), and `E3` (an unusable definition
is recorded and never silently gate-passed). Missing evidence is `unmeasured`; E violations
invalidate the trial.

Judged workflows get `J1` (every call has retained evidence), `J2` (answers agree with
aspect and criterion closure) and `J3` (undecided aspects are reported and cannot yield
`verified`). Missing evidence is `unmeasured`; J violations invalidate the trial.
Host-side judge windows use the registered `request.judge` limit and do not consume
the agent-idle window. Agent environment names are retained in
`<trial>/raidar-secret-isolation.json`; each grader and coverage sandbox retains
`<trial>/raidar-oracle/steps/<step>/{grading,coverage}-secret-isolation.json`.
These names-only records prove key exclusion without persisting environment values.

`job.json` records the runner commit and toolchain hash, pack and grader recipe digests, effective limits and the execution target. `make experiment-analyze` pools several jobs only when these match (`POOL_OVERRIDE=<field>[,<field>]` lifts a named refusal, recorded in the analysis; a records-manifest mismatch or a stage-code difference never lifts).

Pooling also checks the mechanics epoch, dirty-runner state and subject; timing and cost cannot pool across target hardware. Jobs predating the mechanics epoch are labelled `unmeasured`. Whatever the number of jobs, every arm must declare all nine agent capabilities (`capabilities` in `lab/agents/<name>.yaml`, recorded in the lock and `plan.json`) and the declarations must be equal, unless the experiment declares `varies: [harness]`; such a harness comparison must share one canonical model id and reasoning unless it also lists `model` and/or `reasoning` in `varies`. Every valid trial of a problem must carry the same parity record (`parity` in its trial record: image id, seeded workspace digest, limits, agent network policy, the agent process's user, `PATH` and `TMPDIR`); none of this lifts with `--pool-override`, and the analysis records both comparisons. Smoke admission applies the same rule to the arms of a run. Each trial record also keeps `harness_isolation`, the flags the agent harness applied to its turns.

`lab/ledger/` tracks fault categories (`faults/FCR-NNN.md`) and remedies (`remedies/M-NNN.md`); `make ledger-index` regenerates `lab/ledger/README.md` and `make ledger-check` (part of `make quality`) validates the records.

Make variables follow the vocabulary: `JOB`, `JOBS`, `JOB_ID`, `JOBS_DIR`, `AGENT`, `RELEASE`, `ATTEMPT`, `SMOKE_ATTEMPTS`, `VERIFIER_BUNDLE`, `REVIEW_JOBS_ROOT`, `QUALITY_JOBS`, `ALLOW_DEV_JOBS`. A variable under its old name (`RUN=`, `HARNESS=`, `BLOCK=`, ...) fails with its new name.

## Repository layout

- `orchestrator/`: the `raidar` CLI and library, grouped into tiers (`orchestrator/src/raidar/`), highest first; a group imports only groups below it, and import-linter enforces it: `cli` (commands), `evaluation` (`review/` with `projection/` and `quality/` above `analysis/`, and `ledger/`), `engine` (running a locked experiment: `job/` above `trial/` above `health/` above `perf/` above `target/`), `design` (`experiment/`: schema, lock, compatibility gate, arm resolution; `problem/`: registry, import, store), `scoring` (`catalog/` above one package per scorer and the offline `judge/`, above `framework/`; it never imports subject execution or package definitions), `grading` (the hidden verifier), `subject` (`workflow/` above independent `primitive/` and `agent/`, above pure `package/` data; `environment/` runtime packs independent of all four), `judge` (shared host-side transport, protocol, recording and secret isolation) and `contract` (shared records and mechanics: job layout, Harbor result reading, trial records, canonical hashing, registry references, repository paths). Health reads records, not workflow, primitive or package code. Folder names are singular unless the folder is a catalogue of same-kind items.
- `lab/`: everything tracked that defines experiments. `lab/experiments/<kind>/<id>/` holds experiment definitions with their locks (the smoke experiments and the agent benchmark), `lab/problems/` the pinned problem registry (`sources.yaml`, `registry/`, `fixtures/`), and `lab/ledger/` the fault and remedy ledger. The data the code loads at run time sits beside them, each tree at one explicit path from `raidar/contract/paths.py`: `lab/agents/<name>.yaml` (agent releases), `lab/workflows/<name>/workflow.yaml` (workflow releases), `lab/stages/<ns>/<name>/` (stage packages), `lab/releases.yaml` (the registry of `name@N` releases), `lab/environments/<runtime>/` (the runtime pack recipe: `pack.yaml`, `tools.lock.yaml`, `image/`) and `lab/judge/` (judge policies a…
- `var/`: generated data, ignored by git; `RAIDAR_VAR=<dir>` relocates it. `var/jobs/`, `var/stores/` and `var/analysis/` hold job directories, Assessment stores and analysis output, `var/problems/store/` and `var/problems/cache/` the imported problem content and source checkouts, `var/tmp/` scratch (smoke locks, review fixtures), `var/cache/tools/` the host-provisioned scorer tools (`RAIDAR_TOOL_CACHE=<dir>` overrides), and `var/archive/` local data from before the restructure.
- `review-surface/`: the review UI, its frozen projection contract and its Playwright suite.
- `docs/reboot/`: the requirements, plan, ADR, overhead baseline, design memo, the Phase 1 gate packet, and the tracked Phase 0 spike evidence (`evidence/`).

No version appears in any file, module, class or folder name: a scorer, agent or workflow is edited in place, its version is the `name@N` registered in `lab/releases.yaml` (scorers: the `@N` in its manifest), and `make release-archive REF=<name>@<N>` tags the release being replaced as `archive/<name>@<N>` before the edit lands. Older experiments and jobs are not carried forward (tag `archive/pre-structure-reset` preserves them). Local jobs, stores and analysis from before the structure reset remain under `var/archive/pre-v1/`.

The gate dispatcher strips Git repository-location environment variables for both entrypoints, so checks' scratch repositories cannot inherit the committing repository or index.

`make gate-commit` checks whole-tree path policy, then runs `make quality`: structure, smoke command shapes, release registration, experiment locks, ledger, ruff, import contracts, lizard, mypy, fan-out, and the fast test tier with unchanged line and branch coverage floors. `make gate-ci` adds the slow and quality tiers, in that order, on every push and pull request. Both entrypoints are declared in `.omp/gate.json`; the check-only pre-commit hook runs the commit entrypoint without fixing or staging files. Install it in each clone:

```bash
env -u AGENT uv run --project orchestrator --extra dev pre-commit install --hook-type pre-commit
```

Full Ruff lint checks all maintained Python roots, including tests and scripts, against
`.omp/baselines/ruff.json`. `make ruff-baseline` explicitly records current findings; gates
never refresh it and reject new or stale findings. Existing debt is finding-specific and
may only shrink after adoption. New suppression comments and non-catalogue per-file
exceptions fail `make ruff-suppressions-check`. Lint targets Python 3.12 with preview rules;
formatting remains stable, line length stays 100, and descriptive summaries retain D401's
approved exception. Fix targets preserve released stage packages and raw grading payloads.

`make test-hygiene-check` is part of both gates. It requires bounded subprocess
waits in Python tests, valid skill frontmatter and local support links, and
content-addressed stage evidence in `lab/stage-checks/`. Current stage releases
need installed-tree evidence; packages with `on_stop` also need a canary matching
their current runtime primitives and declared outputs. Historical registered
digests remain valid history; `var/` and wall-clock age are not evidence.
Repair stage evidence with `make stage-check NAME=<namespace/name>`.

Python policy is target-owned in `scripts/checks/check-python-quality.py`: unknown
Python roots fail closed, commands require approved kebab-case names, tests map to
canonical groups, and generic module/directory names require exact approval.
Skills use kebab-case. Production and command subprocess calls stay in mapped
seam functions (test process safety is checked separately); dynamic imports stay
in explicit adapters, and collaborator contracts require named callable aliases
and concrete types. Production and command loop/comprehension depth is capped at
two. Existing identity-bound findings are ratcheted individually in
`.omp/baselines/python-policy.json`; new findings or baseline growth fail.

Both gates also run `make test-evidence-check` against `origin/main`
(`TEST_EVIDENCE_BASE` overrides the comparison ref). Each added test file,
function/method, and expanded parameter case needs its own selector in the tracked
`.omp/test-behavior-map.json`: `{"schemaVersion": 1, "tests": {"path::test_name":
{"source": "changed/source.py", "behavior": "Describe the changed behavior"}}}`.
The source must change in the same PR. The checker rejects new cases with the
same normalized called targets, bound inputs and assertions as an existing or
other added case; map missing evidence or merge/remove duplicate tests.
Parameter rows and IDs must be statically collectable; changed files with dynamic
collection fail closed rather than executing test code during this static check.

Both gates run `make rule-coverage`: all 104 catalogue IDs in
`.omp/guardrail-rules.json` need executed evidence in `.omp/rule-coverage.json`.
Each local rule runs its real checker on passing and violating fixtures; hooks
prove installation and commit-failure propagation in disposable repositories.
PYQ210/PYQ211 retain the approved architectural exclusions above. Configuration
deviations and finding-level debt remain explicit implemented enforcement, not
claims of a debt-free tree.

`make record-host-readback` records a live GitHub query for default-branch required
`gate`, auto-merge and rebase-merge settings. Refresh `.omp/host-readback.json`
within 30 days; missing, malformed or stale records fail coverage. The upstream
validator is vendored unchanged under `scripts/vendor/setup-repository-guardrails/`;
its provenance records the source and update procedure. The maintained launcher
checks its recorded SHA-256 and installed skill bytes before full execution.

Docker-backed tests (`make test-docker`), the N1 overhead gate (`make perf-gate`) and `make security-gate` remain separate checks on a ready Docker target.

`make analyzer-scope-check` enforces filesystem (PYS251), environment/process
(PYS252), and applicable `CODEOWNERS` ownership (PYS253) across scoring, evaluation
analysis, and engine health. Only the five adopted I/O boundaries are approved;
the 68 existing direct operations are individually ratcheted in
`.omp/baselines/analyzer-scope.json` per the adopted decision, never exempted by file.
Production code and execution identities are preserved. PYQ210/PYQ211 are
not applicable to the structural Scorer Protocol and inherited coverage entrypoint:
the [adopted decision](docs/reboot/evidence/decide-analyzer-sol.md) records why no
nominal analyzer hierarchy is imposed. This check is part of `make quality`.

Tests are tiered (fast, slow, quality, docker, security, perf) with their own targets (`make test-fast`, `test-slow`, `test-quality`, `test-docker`); see the [repo test profile](.omp/test-profile.md) and global `test-guidance` for generic rules.

The fast run writes a unique, complete phase-level budget report under `var/`;
both gates consume it without rerunning pytest. Invalid measurements always
fail; timing is advisory locally and enforced only for the matching pinned
`ubuntu-24.04` CI profile. Until five successful baseline measurements exist,
the checker prints `no limits (0/5)` and still enforces report validity.
Dispatch `.github/workflows/test-budget-measure.yml` at a fixed revision, then
`make test-budget-record RUNS="<GitHub run ids>"` imports verified artifacts
into `.omp/test-budgets/`. `make test-budget-check` consumes only the current
invocation's report. Baselines and formulas determine limits; owner approval
for increases is verified using GitHub reviews bound to the policy diff and
commit, never a committed boolean. Unit 6b records the first five baselines
after the tooling merges; the 30-second goal remains separate.

Strict mypy checks every maintained Python root, including tests and scripts. Its
finding-specific debt is tracked in `.omp/baselines/mypy.json`: new findings,
stale entries and baseline growth against `origin/main` fail the gate. Fixes
remove their baseline entries in the same change. `make mypy-check` is check-only;
`make mypy-baseline` explicitly records a scan for initial adoption, not a way to
approve new debt. The inventory check requires new stage tool roots to join the
configured target list. Fixture programs are inputs, not typing targets; released
sources are checked without rewriting their bytes.
Missing third-party stubs and decorators made `Any` by `follow_imports=skip`
have evidence-commented, named module overrides, not global exceptions. Test
modules retain strict annotation checks while accepting pytest decorators.
