# Stage packages: `stage/1`

A stage package contributes to a checkpoint's one primary agent session. It is not an agent, host plugin, separate step, scorer or judge. Raidar owns execution, limits, evidence, attribution and grading. Authors own their method's text, tools and rules. This page is the public contract; `split-design.md` is the binding design.

The portable implementation is `contract/`, distribution `raidar-contract`, import
`raidar_contract`; it depends only on pydantic and PyYAML. Raidar uses that same
implementation. `make contract-build` builds unpublished wheel/sdist artifacts under
`var/contract-dist/` and prints their SHA-256 hashes. The contract remains prerelease
until the phase-5 exit; runtime primitives and Harbor integration stay in the orchestrator.

## Folder and names

```text
lab/stages/<ns>/<name>/
  stage.yaml
  prompt.md
  instructions.md                 # optional
  skills/<skill>/SKILL.md          # optional; other regular skill files allowed
  tool/pyproject.toml              # optional dependency-only uv project
  tool/uv.lock                    # generated, never hand-edited
  tool/src/...                    # optional own Python source, never built on the host
  fixtures/<case>/context.json
  fixtures/<case>/state/...
  fixtures/<case>/expect.yaml
```

Names are lowercase letters/digits and hyphens, starting with a letter/digit. A package folder is exactly two levels below `lab/stages/`: its namespace `<ns>` and its name `<name>`. A shallower package (no namespace), a deeper one and loose files are refused by `make structure-check` and the loader. The manifest `name` equals the folder's leaf name; the namespace is part of the reference, not the leaf name. `raidar/` is reserved for reviewed upstream packages (`lab/stages/raidar/<name>/`, protected by `CODEOWNERS`); imported third-party packages are committed under their own namespace. No version appears in a file, module, class or directory name. Paths are relative to their owner, without `..`, symlinks or special files.

## Strict manifest

Unknown keys are refused at every level. The top-level keys are exactly `contract`, `name`, `summary`, `requires`, `params`, `tokens`, `inputs`, `tool`, `setup`, `gate`, `on_stop`, `outputs`, `skills`. `contract`, `name` and `summary` are required. `prompt.md` is always required; `instructions.md` is discovered by convention, not a manifest key. Omitted mappings/lists are empty; omitted optional calls/tool are absent.

```yaml
contract: stage/1
name: review-notes
summary: Check and refine a locally written review note.
requires: {setup: setup/1, gate: gate/1, stop: stop/1, outputs: outputs/1}
params: {strict: true}
tokens: [GUIDANCE]
inputs: {}                         # local name -> {from: earlier-package.output, contract: criteria/1}
tool:
  project: tool
  commands: {review-notes: [python, -I, -B, -m, review_notes]}
setup: {run: [review-notes, setup], timeout_s: 60}
gate:
  - name: note-ready
    run: [review-notes, check]
    before: true
    watch: /app/.raidar/review-notes
    timeout_s: 60
on_stop:
  run: [review-notes, inspect]
  watch: /app/.raidar/review-notes
  timeout_s: 60
  max_rounds: 3
outputs:
  note: {path: /app/.raidar/review-notes/note.md}
skills: []
```

`requires` maps local capability labels to supported primitive contract ids. Declaring a trigger requires its corresponding primitive; install and frame are implicit. Unsupported contracts are refused, never imported as plugins. `inputs` names earlier producers and exact contracts; forward references and missing producers are refused. An output's only keys are `path` and optional `contract`; the supported structured output contract is `criteria/1`. Output paths must be below `/app/.raidar/`, never solution files. Skills are leaf names under `skills/` and each needs `SKILL.md`.

`tool.project` is exactly `tool`. Its uv project declares dependencies, not a build backend or editable project. `tool.commands` maps free command names to nonempty argv lists. Own source lives under `tool/src`; dependencies must have hash-pinned Linux Python 3.12 wheels for both supported pack architectures. Source builds, sdists, direct references and install-time downloads are refused. `stage-lock` generates `uv.lock`; ST5 verifies it is current with `uv lock --check --offline`. Root copies verified bytes and installs wheels offline under `/opt/raidar/stages/<ns>/<name>/`; it never runs authored code. Commands are on the agent's `PATH` and the installed tree and wheel inventory are recorded host-side.

### Params, tokens and frame

`params` declares JSON scalar defaults. A workflow can override only declared keys, retaining their JSON type; effective params enter workflow identity. A parameter `test_timeout` supplies `%%%TEST_TIMEOUT%%%`; hyphens become underscores and names uppercase. `tokens` declares additional string tokens returned by setup/stop; names are uppercase letters, digits and underscores. Params and returned tokens cannot collide. Setup must return every declared token. Undeclared tokens and unresolved placeholders are refused. Substitution is one pass: token-looking text inside a value or task is not interpreted again.

A schema-2 workflow declares `build: {prompt: ...}` and ordered `stages: [{use: '<ns>/<name>@N', params: {...}}]`. Build's prompt contains `%%%TASK%%%` exactly once and exactly one slot for every enabled package: leaf name uppercased with hyphens changed to underscores (for example `%%%REVIEW_NOTES%%%`). No absent-package or unknown slots are allowed. Package fragments fill their slots; task text is inserted verbatim. Package slots must not collide, including across namespaces. Instructions are joined in workflow order at the native agent's instruction placement, never over the task's own `AGENTS.md`. Disabled packages leave no sandbox files.

`build.reply: acceptance-reply/1` declares structured acceptance replies. A `judge` requires
that reply, `criteria: <package-leaf>.<output>` declaring `criteria/1`, exactly one pytest-kind
gate, a model, `0 <= open_at < closed_at <= 1`, positive `max_passes`, and a rework frame with
exactly one `%%%PUSHBACK%%%` and `%%%GATE%%%`. Missing producers or reply are ST3 refusals.
`jev-judge/1` owns the question-set id, three retries and 60-second request timeout; packages
and workflow judge configuration cannot override them. Admission is implemented in 5A.
Schema-2 reply/judge execution is refused until the 5B wiring, never silently omitted.

### Setup, gate and stop

Calls use argv lists, never shell text. `run` is nonempty. `timeout_s` defaults to 60 seconds and is positive, at most 300. Setup runs before the agent on lab-owned inputs and prints exactly `{"tokens": {"GUIDANCE": "..."}}` as JSON; logging belongs on stderr. Nonzero exit, timeout, invalid JSON or missing tokens invalidates the trial.

A gate entry accepts exactly `name`, `run`, `kind`, `before`, `watch`, `timeout_s`. Check names are free and unique; no language-specific check-name enumeration exists. Default kind is a command, requiring `run`. The reserved `kind: pytest` records individual test outcomes and requires at least one collected test with no failed cases to pass; it uses the package's pinned pytest. `before: true` checks are prerequisites: they run before ordinary checks and a refusal prevents the latter. `watch` identifies bounded stage state. Every check runs on the agent's commits and at the final record-only seal. The seal never commits, stages, restores or repairs the workspace. Workflow `gate: {max_fix_rounds: N}` bounds fix turns, default zero. A pre-fix solution tree is retained whenever a fix turn starts.

For pytest-kind checks, effective `params.test_timeout`, when present, must be a positive
integer and sets the actual per-test timeout. Without that parameter, the per-test default
is `max(1, timeout_s // 2)`. The whole-check `timeout_s` budget remains separate and is at
most 300 seconds.

`on_stop` accepts `run`, `watch`, `timeout_s`, `max_rounds`; the latter defaults to zero and is in 0–5. It runs after each agent turn once leftover agent processes have been reaped. It prints exactly this strict JSON object:

```json
{"status":"done","message":"","tokens":{},"carry":{}}
```

All four fields are required. `status` is `done`, `resume` or `closed`; `message` is a string; `tokens` maps declared names to strings; `carry` is a JSON object. `resume` needs a nonempty message. Carry is passed unchanged in the next call's context, not automatically inserted into a prompt. `done` requires all declared outputs. `closed` is terminal: a root-owned flag cannot be reopened. A resume beyond the round bound closes instead. One malformed/unusable repair is allowed per package per checkpoint, then closed. The first requested resume in workflow order wins, always resuming the same primary session. Gate fixes, repairs and rework obey the equal checkpoint deadline and the fit rule; a cut records `deadline` and grades what the agent left.

A closed package's `before` checks refuse without invoking its tool, even if a later package resumes the session. Closed flags and canary state belong to one checkpoint, not the entire container.

## Call protocol and evidence

Setup and stop receive a root-owned JSON context path as their final `--context <path>` argv pair. Context contains `task`, effective `params`, `round`, `carry`, `state` (the watched state directory) and declared `outputs`. `RAIDAR_STAGE_CONTEXT` names that path for every call, including gate commands; conventional gate argv is not extended with tool-specific flags. A call receives no inherited host or harness environment. It runs as UID/GID 1000 under `env -i` with a fixed package/pack `PATH`, `LANG=C.UTF-8`, and fresh `HOME` and `TMPDIR` per call. Calls have no network. Package code never runs on the host, including fixture and replay commands. The judge key never enters a container. Hidden grading stays offline in its separate sandbox.

Gate contexts additionally contain the lab's `base` commit, published root-owned (0644)
after workspace setup and before any check. An authored `base` is overwritten; the value
remains the checkpoint's starting commit even after the agent creates later commits.

Every call is recorded host-side with package/primitive identity, argv, user, environment digest/names, timeout, duration, exit/signal, stdout digest and capped stdout, and the last 8 KiB of stderr. Stdout is capped at 1 MiB; overflow is an unusable result. Container exec failure, missing call evidence or changed installed tool bytes is an integrity failure, not an agent score. Outputs are copied and hashed host-side after each call and at close; they are method evidence, never graded solution files. `workflow.json` groups evidence by primitive contract id, not shipped package names.

## Canary attribution and bounds

Before the agent starts, the package's known-good `done` fixture runs inside the same container. Its stdout and output digests must match the passing `stage-check` record for the exact package, pack recipe and primitive identities. A failing or changed canary invalidates the trial. A gate's passing setup check on the starting workspace is its canary.

| Condition | Attribution and action |
|---|---|
| Setup or initial canary fails | Instrument failure; trial invalid |
| Valid result and owed outputs present | Agent outcome as reported |
| Watched state outside Raidar bounds | Agent outcome; one repair then closed; tool not called |
| Junk/schema error, stdout overflow, crash, signal, timeout or missing done output; canary passes and installed tool unchanged | Agent outcome; one repair then closed |
| Failed call and failed canary, resources above recorded floors | Instrument failure; trial invalid |
| Same, but agent-writable disk or memory below its floor | Agent outcome `environment_exhausted`; one repair then closed |
| Changed tool tree, missing call record or failed container exec | Integrity failure; trial invalid |

Before following or opening watched files, Raidar rejects symlinks, FIFOs, devices and other nonregular files. Caps: 64 MiB of regular-file bytes, 10,000 files, depth 32. The bounded state manifest/snapshot is retained for replay. An agent's test that exits 126 or exceeds its timeout after a passing gate canary is an agent outcome, never `gate_error` invalidity.

Any agent-attributed tool failure is reported per arm and package digest and requires a replay disposition before publication: `agent_state` keeps the outcome; `tool_bug` removes that package version from every arm's comparison and requires a new release and full reruns. Individual failed trials must not be dropped or rerun selectively.

### Fixtures

Each fixture has a JSON context, a state tree and `expect.yaml` declaring its expected status and output digests. `done` and `closed` are mandatory for stop packages; `resume` is mandatory when `max_rounds > 0`. Stop fixtures run twice with byte-identical stdout and output digests; the done result becomes the canary. Gate packages supply passing and refusing fixtures, run twice with identical attribution, verdict, check statuses/exit codes and declared output digests. Raw gate diagnostics remain recorded, but are not compared byte-for-byte: conventional tools such as pytest include elapsed time.

Raidar also exercises missing/empty state, zero-byte files, invalid JSON, non-UTF-8 bytes, deeply nested JSON under the cap and unreadable files. They must return valid non-done results within a quarter of the call timeout. Each stop package defines wrong state through its own fixtures: the mandatory `closed` fixture, plus `resume` when `max_rounds > 0`. Bounds fixtures (FIFO, symlink loop and oversized state) prove pre-call rejection without invoking authored code. All cases are offline, UID 1000, clean-environment, pinned-runtime calls. Tool bytes must remain unchanged; writes are confined to declared state and per-call temporary directories.

## `criteria/1`

A criteria output is a strict JSON object with `contract: criteria/1` and `features`, a list of feature objects. Each feature has `id`, `name`, `outcome`, `criteria`; each criterion has `id`, `criterion`, `verification`, `sources`, `kind`, `aspects`, `examples`. Identifiers are nonempty and globally unique; features and their criteria must be nonempty. `sources` is a list of quoted task strings. `kind` is `new` or `regression`. Each aspect and example is `{situation: string, outcome: string}`. New criteria need at least one aspect; regression criteria may use earlier tests. Unknown fields and duplicate ids are refused. Raidar validates this data without importing any package. `acceptance-reply/1` and `jev-judge/1` are Raidar-owned consumers; packages cannot provide new judges or scorers.

## Identity, releases and locking

`stage-tree/1` is `digest_json({scheme: 'stage-tree/1', files: [[path, sha256, exec], ...]})`: sorted package-relative POSIX paths, exact file bytes, executable-bit boolean. Fixtures do not ship and are excluded; `.git`, `__pycache__` and `.DS_Store` are ignored. Symlinks (including in excluded fixture trees), traversal, absolute paths and special files are refused. Dependency lock bytes, prompts, skills, instructions and own tool bytes are included. Transport and checkout location are not identity.

`lab/releases.yaml` has a `stages:` mapping from `<ns>/<name>@N` to its digest. N starts at 1. Releases are append-only against main's history; only the latest release's current bytes resolve. Older bytes live in archive tags. No release writer exists: checks print the next line to add. Stage and workflow references require their namespace; bare references, paths, branches, tags and `latest` do not resolve, with no alias. Bare workflow register entries remain frozen history only. Agent references retain `name@N`. A changed package requires a new package release and dependent workflow release, then relocking.

Workflow identity folds in every package release, digest and effective params. Arm locks carry `packages` and `primitive_code` (`python-ast/1` inventories including transitive primitive imports, Harbor and native adapter code); neither field can be overridden in pooling. Trial evidence records installed-tree digests and wheels; a mismatch invalidates the trial. Prompt, tool-byte and param changes each change identity and stale locks are refused.

The lock pins expected installed-tree digests per architecture. Later checkpoints reuse an existing install only after a fresh root-owned, no-follow measurement matches that expectation; a changed or unmeasurable tree invalidates the trial and is never repaired or reinstalled over.

Lock refusals: **ST1** unsupported/invalid package contract; **ST2** unsupported required primitive; **ST3** missing/forward input producer; **ST4** register/digest mismatch; **ST5** stale lock, sdist or missing pack wheel; **ST6** missing/repeated frame slot; **ST7** colliding commands, skills, output paths or tokens.

## Author commands

Use the repository-root public interface (`env -u AGENT` is required when running from an OMP session):

```sh
env -u AGENT make stage-new NAME=acme/my-check FROM=docs-check  # complete tier-2 command template
env -u AGENT make stage-lock NAME=acme/my-check
env -u AGENT make stage-check NAME=acme/my-check TARGET=mbp
env -u AGENT make stage-check NAME=acme/my-check REPLAY=<trial> TARGET=mbp
env -u AGENT make stage-list
env -u AGENT make stage-info NAME=acme/my-check
env -u AGENT make workflow-new NAME=acme/my-workflow            # FROM=raidar/one-shot-verify by default
env -u AGENT make experiment-lock EXPERIMENT=<smoke-yaml> DRAFT=1
```

Creation refuses overwrites. The `docs-check` template includes a generated pure-wheel lock, a pinned codespell command and six fixtures. Fixtures/replay run offline in throwaway pack containers, never on the host. Draft locking is smoke-only, writes under `var/tmp/drafts/<experiment-id>/`, marks jobs as dev, and never admits a measured comparison. Normal locks require registered immutable references. Generated artifacts belong under `var/` or `RAIDAR_VAR`. See `make help` for the exhaustive command catalogue.

## Trust boundary (C4)

Guaranteed: package code runs only as the agent's user inside a container, cannot reach host secrets, the judge key, hidden verifier bundle or authoritative evidence store; installs are offline and hash-checked; grading is separate. A package cannot define a scorer or host callback.

Not guaranteed: truthful prompts/checks or useful methods. Same-user tools can read the workspace and harness credentials available to the agent, and an agent invoking a tool outside Raidar's isolated call path can use the trial network allowlist. The contract does not promise same-user credential secrecy. Only packages merged through reviewed PRs count for lab decisions. A separate stage-call user, remote transports, signing, multiple concurrent releases, non-Python runtimes and package generators are deferred.

## Upstream packages

`raidar/verify@1` runs lint, format, strict types and pytest with ruff 0.8.6, mypy 2.3.1
and pytest 9.1.1. Its 60-second `test_timeout` default can be overridden through package
parameters. The gate records individual pytest outcomes and refuses empty test collections.
Fixtures cover each failing check, excluded directories, a six-second test passing at the
default and the same test refusing at a two-second override. Whole checks use the maximum
`stage/1` budget of 300 seconds; the spike's 600-second watchdog is not preserved.

`raidar/define@1` owns `define_tool` under its tool source, not under the orchestrator.
Setup, the sealing prerequisite and the stop each have a 60-second timeout; the stop allows
at most three resume turns. The defaults retain unit rules 2 and two accepted clarification
rounds. Ready drafts emit the definition and `criteria/1`, including aspects and examples.
An unusable draft requests one repair, then closes; a corrupt draft cannot turn into an
instrument error merely because its agent-authored bytes do not parse. Trusted-context
errors still fail the call. Fixtures cover done, closed, resume, clarification, repair,
regression criteria and gate acceptance/refusal.

## Contract history

- 2026-10-28: `stage/1`, `stage-tree/1` and `criteria/1` specified before the generic package implementation. Spike stage classes remain until the phase-5 port; they are not the author API.
- 2026-10-29: stage references and folders require a namespace (`<ns>/<name>@N`, `lab/stages/<ns>/<name>/`, exactly two levels); upstream packages live in `lab/stages/raidar/`. `stage-new`, `stage-lock`, `stage-check` and `stage-info` take `NAME=<ns>/<name>`. Agent and workflow references are unchanged; there are no aliases.
- 2026-10-31: remove the generic `wrong-state` stage-check case; semantic wrong state is defined by each stop package's mandatory `closed` fixture and its `resume` fixture when `max_rounds > 0`. No package, primitive, release or lock identity changes.
