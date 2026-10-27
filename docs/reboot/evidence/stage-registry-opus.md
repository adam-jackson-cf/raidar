# Decision: a shared stage-package registry for Raidar

Prefixes: `R/` = `orchestrator/src/raidar/`, `FXL/` = `factory-x-loops/factory-x-loops/src/factory_x_loops/`. Only `make releases-check` was run (`releases: ok`). No repository file was edited, and none of the proposed surface exists yet.

## 1. Recommendation

- **Store packages in this repository and register them the same way as agents and workflows.** [INFERENCE]
  - Each package is a folder `lab/stages/<name>/`.
  - Its releases are a new `stages:` section of `lab/releases.yaml`: `name@N` mapped to a sha256 of the package's bytes.
  - A workflow refers to `define@1`. The workflow's resolved digest folds in the package's digest.
  - The experiment lock records each package's release and digest, plus the digest of each Raidar primitive's code. Pooling never accepts an override for either.
- **Identity does not depend on where the bytes came from, and references are namespaced from the start.** Remote sharing (`acme/docs-check@1`, imported from git) can then be added later without migration. [INFERENCE]
- **Why this fits current conventions:**
  - Only the latest version of a name resolves. An edited definition with no new release is refused, and the refusal names the next release. [OBSERVED `R/subject/releases.py:83-93,186-190`]
  - Versions are data, never part of a path. [OBSERVED `scripts/checks/check-structure.py:6-7,91`]
  - A workflow's hash already covers its resolved definition and every skill file. [OBSERVED `R/subject/workflow/definition.py:34-40,356-358`]
  - The lock records each arm's identity as the agent release and pin, the workflow release, the workflow's content hash and `stage_code`. [OBSERVED `R/design/experiment/arm.py:104-125`; `lab/experiments/smoke/python__forge__define-spike/experiment.lock.json:1048-1056`]

## 2. Facts that shape the design

- **Two version forms already exist.**
  - `name@N` is a registered release. [OBSERVED `R/contract/releases.py:28`]
  - `name/N` is a contract version that Raidar's code implements, e.g. `question_set: Literal["dbv5-aspect/1"]`. [OBSERVED `R/subject/workflow/definition.py:172`]
  - So I propose writing the brief's `criteria@1` as `criteria/1`. [INFERENCE]
- **External, pinned data already has a precedent.**
  - `lab/problems/sources.yaml` maps the namespace `scb` to a URL and commit. [OBSERVED `lab/problems/sources.yaml:3-10`]
  - Problem references are namespaced, e.g. `scb/forge@38d627ecf668`. [OBSERVED `lab/problems/registry/scb/forge/38d627ecf668.json:4`]
  - The reference grammar allows `/` in names, but the release grammar does not. [OBSERVED `R/contract/references.py:24-26` vs `R/contract/releases.py:28`]
- **Today's tool closure is open.**
  - The verify gate runs `uv pip install` on its top-level pins at trial time, with no hashes and no exclude-newer. [OBSERVED `R/subject/workflow/verify.py:269-270`]
  - The resolved set is only recorded afterwards, by `uv pip freeze`. [OBSERVED `R/subject/workflow/verify.py:41-42,412-416`]
  - The trial network allows the runtime's package hosts. [OBSERVED `lab/agents/codex.yaml:37`]
  - So two trials under one lock could run different transitive versions. [INFERENCE]
  - The pack's own measurement tools already pin `exclude_newer`. [OBSERVED `lab/environments/python/tools.lock.yaml:14-15`; `R/subject/environment/python/provisioner.py:229`]
- **Method code runs on the host today.** Define imports `define_tool` host-side. [OBSERVED `R/subject/workflow/define.py:164-167`] A third-party package must never do this. [INFERENCE]
- **Pooling and admission rules:**
  - Pooling accepts an override for `subject` (agent and workflow hashes) but never for `stage_code`. [OBSERVED `R/evaluation/analysis/pooling.py:7-8,47-57,179`]
  - Admission compares each arm's full identity. [OBSERVED `R/engine/health/admission.py:3-7`]
- **How stage code is hashed today.** The stage code digest is a `python-ast/1` digest over listed modules, the Harbor version and the adapter modules. [OBSERVED `R/subject/workflow/stages.py:110-117`; `R/contract/source_digest.py:23`] The canonical rule is to hash "exactly the bytes a lab definition ships". [OBSERVED `R/contract/canonical.py:10-13`]
- **FXL hashes components by content, but puts the version in the path** (`name/v{N}`). Raidar forbids that. [OBSERVED `FXL/engine/workflow/components.py:97,212-221`; `scripts/checks/check-structure.py:91`]

## 3. Options: where packages live and how they are referenced

| Option | Reference form | Publish and consume | Forks and private teams | Identity | Verdict |
|---|---|---|---|---|---|
| **A. In-repo `lab/stages/` plus a `stages:` register** | `define@1` (local); `ns/name@N` reserved | A PR adds the folder and a register line. Consumers vendor the package. | Forks and private repos work as plain git. | Whole-folder sha256 in the register, folded into the workflow and the lock | **Adopt.** Same rules as agents and workflows. PR review doubles as package review. |
| B. Separate registry repo (`raidar-stages`) | `ns/name@N` | A PR to the shared repo; consumers fetch | A team runs its own registry repo | Same digest | **Defer.** This is A's format in another repo. Add it when a second publisher exists. |
| C. Git ref per package | `git+URL@commit#path` | Push and cite a commit | Any git host and credentials | A commit is not content identity. History can be rewritten. | **Steelman (§9). Adopt later as a transport only.** |
| D. OCI artifacts | `oci://host/ns/name@sha256:…` | Push with `oras`; content-addressed | Existing private registries, signing | Strong, but the bytes are opaque in a PR | Defer as a transport. It needs registry auth, and review moves out of PRs. |
| E. PyPI wheels | `pkg==X` + hash | `uv publish` | Private indexes | Author-chosen semver; installs can run build backends | Reject. Prompts and skills are not Python, and stages must stay language-agnostic. |

[INFERENCE throughout the table.] A, B, C and D share one digest scheme (§6). They differ only in how bytes are fetched, so choosing A now locks nothing in.

## 4. What a package is: a worked example

```text
lab/stages/verify/                  # folder name = manifest name; no version in any path
  stage.yaml                        # manifest (stage/1)
  prompt.md                         # fragment; %%%TEST_TIMEOUT%%% filled from params
  skills/test-per-aspect/SKILL.md
  skills/pytest-templates/SKILL.md
  tool/pyproject.toml               # uv project, `[tool.uv] package = false`: deps only, never built
  tool/uv.lock                      # generated by `make stage-lock`, never hand-edited; every wheel sha256
  tool/src/…                        # own code (define has define_tool here); put on the venv path, not built
  fixtures/refused-commit/          # `make stage-check` cases: workspace/, task.md, expect.yaml
```

```yaml
# lab/stages/verify/stage.yaml
contract: stage/1                   # package-contract schema; exactly one supported version
name: verify                        # must equal the folder name (FXL precedent: frontmatter name = folder)
summary: Python lint, format, type and test gate on commit; record-only seal on stop.
requires: { gate: gate/1 }          # Raidar primitives this package uses
tool: { project: tool }             # dependency closure = tool/uv.lock; runs on the pack's system Python
prompt: prompt.md
skills: [test-per-aspect, pytest-templates]
params: { test_timeout: 60 }        # defaults; a workflow may override any key, and no other
gate:
  - { name: lint,   run: ruff check }
  - { name: format, run: ruff format --check }
  - { name: types,  run: mypy --strict }
  - { name: tests,  kind: pytest }  # gate/1's reserved kind: per-test outcomes
```

Define's manifest adds `requires: {stop: stop/1}` and `outputs: {criteria: {path: .raidar/define/criteria.json, contract: criteria/1}}`. A judging package adds `requires: {judge: jev-judge/1, inputs: [criteria/1]}`. [INFERENCE]

```yaml
# lab/releases.yaml (new section; same format as agents/workflows)
stages:
  define@1: <sha256 stage-tree/1 of lab/stages/define/>
  verify@1: <sha256 stage-tree/1 of lab/stages/verify/>
workflows:
  define-build-verify@4: <sha256 of the resolved workflow, which includes define@1 and verify@1 digests>
```

```yaml
# lab/workflows/define-build-verify/workflow.yaml
schema: 2
name: define-build-verify
stages:                             # order = setup, gate and stop order; one primary session
  - { use: define@1 }
  - { use: verify@1, params: { test_timeout: 90 } }
build: { prompt: "… %%%DEFINE%%% … %%%VERIFY%%% … %%%TASK%%%", reply: acceptance }  # Raidar frame
```

**Dependencies are pinned and closed** [INFERENCE]:
- `tool/uv.lock` is the only dependency truth.
- `make stage-lock` refuses:
  - sdists, so nothing is built and no build backend code runs;
  - a missing wheel for the pack's Python or for linux amd64/arm64;
  - a `requires-python` that excludes the pack's Python 3.12. [OBSERVED `lab/environments/python/pack.yaml:6`]
- Wheels are downloaded on the host into `var/` (a cache keyed by sha256) and checked against `uv.lock`.
- They are copied into the container. Root then installs them offline with `--no-index`, hashes required, into `/opt/raidar/stages/<name>/`, on the pack's interpreter. This replaces today's install from the network at trial time.

## 5. Versions and authoring

- **Frozen releases.** A registered `name@N` never changes, which is the repo rule. Any byte change in the folder (tool, prompt, skill, fixture, or `uv.lock` after `make stage-lock`) produces a new digest. The chain is then [INFERENCE, extending `R/subject/releases.py:186-190`]:
  1. `releases-check` names `define@2: <sha>`.
  2. Each dependent workflow resolves to a new digest, so `releases-check` names its next release.
  3. `experiment-locks-check` names the stale locks.
  4. `make experiment-lock` relocks them.
- **Pinning by digest** lives in the register, not in `workflow.yaml`. The workflow's resolved digest and the lock carry it, so a fork's different `define@1` cannot be confused with upstream's. [INFERENCE]
- **Mutable references are always refused, at parse time.** The grammar is `[ns/]name@N` with N ≥ 1. A bare name, `@latest`, a branch, a tag or a path is rejected. [INFERENCE; grammar after `R/contract/releases.py:28`]
- **Drafts are a lock option, not a reference form.**
  - `make experiment-lock … DRAFT=1` is allowed only for `smoke`-kind experiments, with the lock written under `var/tmp/`.
  - It resolves unregistered or changed packages and workflows from the working tree, and records `draft: true` with their digests.
  - Draft jobs are dev jobs: they are refused by analysis without `ALLOW_DEV_JOBS` [OBSERVED `R/evaluation/analysis/sources.py:181-207`] and never admit a measured run.
  - The runner's dirty flag also labels them. [OBSERVED `R/engine/job/provenance.py:26,49-52`]
  - Released numbers are therefore spent only on bytes that passed a smoke. [INFERENCE]
- **Only the latest version resolves**, as for agents and workflows. Older bytes live in `archive/<name>@N` tags. [OBSERVED `R/subject/releases.py:83-84`; `Makefile:641-648`] To A/B a method change inside one experiment, give the variant its own name (e.g. `define-strict`). [INFERENCE]

## 6. Identity and hash spec

- **`package_sha256`** = `digest_json({"scheme": "stage-tree/1", "files": [[path, sha256(bytes), exec], …]})`. [INFERENCE] The parts:
  - `digest_json` is sha256 of compact, key-sorted UTF-8 JSON. [OBSERVED `R/contract/canonical.py:16-20,34-35`]
  - `files` covers every regular file under the folder, as sorted POSIX paths, minus `IGNORED_TREE_NAMES`. [OBSERVED `R/contract/canonical.py:13`]
  - `exec` is `mode & 0o111 != 0`.
  - Symlinks, `..`, absolute paths and paths outside `[A-Za-z0-9._/-]` are refused.
  - Bytes are hashed, not ASTs, because the payload ships verbatim. The scheme is tagged so a later normalisation change is explicit (precedent `R/contract/source_digest.py:23`).
- **Resolved workflow digest** = today's resolved definition [OBSERVED `R/subject/workflow/definition.py:341-358`] plus `packages: {"define@1": sha, …}` and each package's resolved `params` (manifest defaults overlaid by workflow overrides). [INFERENCE]
- **Lock arm identity** replaces `stage_code` [OBSERVED `R/design/experiment/arm.py:121-123`] with two fields, and the arm `digest` covers them [INFERENCE]:
  - `packages: {define: {release: define@1, sha256}, …}`;
  - `primitive_code: {frame, gate/1, stop/1, jev-judge/1, …}`. Each entry is built like `stage_code_digest`: `python-ast/1` over the primitive's modules, the Harbor version and the adapter modules (`R/subject/workflow/stages.py:110-117`), and covers only the primitives the arm uses.
- **Pooling and admission.**
  - `packages` and `primitive_code` join `NEVER_OVERRIDABLE` [OBSERVED `R/evaluation/analysis/pooling.py:179`]. They include tool code, which is today's `stage_code` class. [INFERENCE]
  - Workflow text and params stay in `subject`, which accepts an override as today. [OBSERVED `R/evaluation/analysis/pooling.py:54,146`]
  - Admission gains both fields automatically, since it compares the full arm entry. [OBSERVED `R/engine/health/admission.py:51-63`]
- **Trial evidence** (host-written): the digest of the installed `/opt/raidar/stages/<name>/` tree plus the list of installed wheels. A mismatch with the lock is an instrument failure (precedent: the verify `setup_check`, `R/subject/workflow/verify.py:51-56`). [INFERENCE]
- **Clean cutover.** `STAGE_MODULES`, `STAGE_PAYLOADS`, `StageType` and `R/subject/workflow/define_tool/` leave the code. [OBSERVED that they exist: `R/subject/workflow/stages.py:22-67`; `R/subject/workflow/definition.py:58`]

## 7. Compatibility

- **Contracts.** Each contract has one version that Raidar supports: `stage/1` (manifest and call protocol), `gate/1`, `stop/1`, `criteria/1`, `jev-judge/1`, `frame/1`. They are listed in one Raidar module, and that module is included in `primitive_code`. [INFERENCE]
- **Refused at lock time** (new `ST` checks, beside RT0–RT6 [OBSERVED `R/design/experiment/lock.py:25-28`]; every check below is [INFERENCE]):

| Check | Refuses when |
|---|---|
| ST1 | The manifest `contract` is unsupported. |
| ST2 | A `requires` entry is unsupported. |
| ST3 | A required input (e.g. `criteria/1`) is not produced by an earlier enabled package's `outputs`. |
| ST4 | A package digest does not match the register (without `DRAFT`). |
| ST5 | `uv.lock` is stale against `pyproject.toml`, contains an sdist, or lacks a wheel for the pack. |
| ST6 | An enabled package's frame token does not appear exactly once. |
| ST7 | Two packages claim the same command, skill, output path or token. |

- **A contract bump is one change:**
  - migrate every in-tree package;
  - re-release the packages and their dependent workflows;
  - relock.
- External packages on the old contract are refused at lock time, with the needed version named. There is no dual support and no shim. [INFERENCE]

## 8. Trust model

**What Raidar guarantees mechanically** [INFERENCE unless cited]:
- Package code never runs as root. Root only copies bytes and installs wheels, and installing a wheel runs no code.
- Package code never runs on the host. This includes `make stage-check`, which runs fixtures in a throwaway pack container with no network, as the agent's user. This retires the precedent in `define.py:164-167`.
- No host secret enters the container. Only the host-side judge holds the Jev key.
- Evidence is written host-side: call argv, exit status and stdout digest, plus copies of outputs labelled as reported by the package.
- Packages cannot score. Payloads live outside `/app`, and outputs live in excluded `.raidar/` paths. [decisions.md "Proposed resolution"]
- Install is offline from hash-checked wheels. The installed tree's digest is checked against the lock.

**What it cannot guarantee** [INFERENCE]:
- **Misleading the agent.** A package can mislead through its prompt or tool output, or pass a gate that should fail.
- **Reaching the agent's own data.** It can read and modify the workspace, burn the budget, and read anything the agent's user can, including the harness credential: codex runs with a `CODEX_HOME` holding `auth.json` [OBSERVED `lab/agents/codex.yaml:40`].
- **Using the trial's network allowlist.** It can reach the provider and package hosts [OBSERVED `lab/agents/codex.yaml:37`].
- **Proving usefulness.** Evidence proves what ran, not that the method is useful or honest.

**Policy.** For lab and factory-x decisions, only packages merged into `lab/stages/` through a reviewed PR count, with `CODEOWNERS` covering `lab/stages/`. Imported packages are vendored, so they go through the same review. Unmerged packages run only as drafts. [INFERENCE]

## 9. Steelman of the rejected alternative: git-pinned references as identity (option C)

**The case for it:**
- No register and no publishing step: cite `git+https://github.com/acme/stages@<commit>#docs-check`.
- Forks, private hosts and teams work through ordinary git credentials.
- A commit cannot be changed in place.
- It matches the problem-source precedent (`lab/problems/sources.yaml:9-10`).
- It removes the cascade of re-releasing dependent workflows after a package change.

**Why it is rejected as identity** [INFERENCE]:
- A commit hashes a whole repository, not the package. The same bytes at two commits look like two packages and fail to pool.
- Force-pushes and repository deletion lose bytes.
- Reports would cite hashes instead of `define@2`.
- Every lock would need network access.
- Branch refs would still need a refusal rule.

It is kept as the deferred fetch transport behind `stage-import`, verified against `stage-tree/1`.

## 10. Make targets

All of these are [INFERENCE]: proposed targets that do not exist yet.

| Target | Does |
|---|---|
| `make stage-new NAME=x [FROM=docs-check]` | Copies an example package. Never overwrites (precedent: `experiment-init`). |
| `make stage-lock NAME=x` | Runs `uv lock` for `tool/`. Refuses sdists and wheels missing for the pack. |
| `make stage-check NAME=x` | Checks schema, contracts, digest and closure; fetches wheels; runs fixtures in the pack container offline. Prints the register line. Needs the Docker target. |
| `make stage-list` | Name, latest release, digest, register state, contract, using workflows. |
| `make stage-info NAME=x` | Expanded manifest: commands, triggers, outputs, resolved params, closure. |
| `make releases-check` (extended) | Adds stages and dependent workflows. Already part of `make quality`. |
| `make structure-check` (extended) | Allows `lab/stages` [OBSERVED allowlist `scripts/checks/check-structure.py:57-59`]. |
| `make experiment-lock … DRAFT=1` | The authoring loop (§5). |

- **No `stage-release` writer.** A release is the line `releases-check` prints, the same as for agents and workflows today. [OBSERVED `lab/releases.yaml:2-4`]
- **Worked examples.** `define` (own tool code plus outputs), `verify` (third-party wheels plus the gate) and `docs-check` (the smallest case, and the `stage-new` template) ship in `lab/stages/` with fixtures. README Concepts gains "stage package". [INFERENCE]

## 11. First version, and what is deferred

**Build now** [INFERENCE]:
1. `lab/stages/` with a `stages:` register and its resolver. Reference grammar `[ns/]name@N`, with only bare (local) names resolving.
2. `stage-tree/1` digests, workflow folding, the lock fields `packages` and `primitive_code`, never-overridable pooling, the installed-tree evidence row.
3. `contract`/`requires` and ST1–ST7.
4. `uv.lock` closure, wheels only, offline install.
5. The five targets and `DRAFT=1`.
6. Port `verify`, then `define`, then add `docs-check`. Delete the per-type code.

**Must be right in v1 so remote sharing needs no migration:**
- Digests are independent of transport.
- The namespaced grammar is already reserved.
- Packages are self-contained and relocatable: no paths outside the folder, no host code, no Raidar imports.
- The dependency closure lives inside the package.
- Contract fields are present.
- Folder name equals manifest name.

**Defer:**
- `stage-import` with git transport and a namespace alias file `lab/stages/sources.yaml` (after the problems precedent);
- OCI and a separate registry repo;
- signing;
- resolving several versions at once;
- non-Python runtimes;
- the generator.

## 12. Risks

All of these are [INFERENCE]:

| Risk | Consequence |
|---|---|
| Release cascade | One package change re-releases every dependent workflow and makes locks stale. This is honest, but causes churn. |
| Wheels-only | Excludes dependencies without linux wheels for both architectures. |
| `stage-check` needs a Docker target | Onboarding cost for external authors. |
| Whole-folder digest | A fixture-only edit forces a release and splits pooling. |
| Porting define and verify | Moves their digests, so spike jobs stop pooling. |
| Credential exposure | A package can read the harness credential (§8). |
| Local namespace aliases (later) | The same package can carry different names in different repos. Identity still holds through digests. |

## 13. Open questions for the owner

1. Should `primitive_code`, packages and params all be never-overridable? I propose packages yes, params no. [INFERENCE]
2. Is "latest only" acceptable for packages, or must an experiment be able to run `define@1` against `define@2` (a content-addressed multi-version store)?
3. Should `fixtures/` sit outside the digest, to avoid fixture-only releases?
4. Is it acceptable that a package can read the harness's `auth.json`, or must stage calls run as a separate unprivileged user?
5. Rename the brief's `criteria@1` to `criteria/1`?
6. Does `DRAFT=1` also cover workflows, which is a broader authoring change?
