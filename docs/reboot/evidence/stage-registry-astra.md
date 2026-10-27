# Decision: shared, content-pinned stage packages

## Activity Summary

- **K1 — [INFERENCE] Adopt an in-repository registry with portable, namespaced packages and content-addressed imports.** Reuse `name@N`, `lab/releases.yaml`, canonical hashing and generated experiment locks; do not introduce a separate versioning system.
- [OBSERVED] Read-only verification passed: `make releases-check` returned `releases: ok`; `make experiment-lock … LOCK_CHECK=1` reported the define-spike lock current. No repository files were edited. Recipes: `Makefile:402-407`.

## Basis and boundaries

[INFERENCE] Citation abbreviations: `R/` = `orchestrator/src/raidar/`; `C/` = `var/tmp/stage-council/`; `FXL/` = `/Users/adamjackson/Projects/inflight/factory-x-loops/factory-x-loops/`.

- [OBSERVED] The owner chose one package contract, shared packages, build as a fixed Raidar prompt frame, and Raidar-owned judges/scorers. Malformed stop-result semantics remain a separate decision. (`C/decisions.md:5-10`)
- [OBSERVED] Existing releases map `name@N` to hashes; definitions are edited in unversioned paths. Only the latest version held by the checkout resolves; older entries are history, not retrievable package storage. (`lab/releases.yaml:1-4`; `R/subject/releases.py:71-96`)
- [OBSERVED] Agent manifests separate schema/name from the harness executable version; neither carries its own release number. (`lab/agents/codex.yaml:4-10`; `lab/agents/claude-code.yaml:4-10`)
- [OBSERVED] Workflow identity hashes resolved configuration and skill contents. Stage implementation identity separately hashes normalized Python syntax, adapter modules and Harbor’s version. (`R/subject/workflow/definition.py:301-358`; `R/subject/workflow/stages.py:99-122`)
- [OBSERVED] Current verify installs direct pins during trial preparation and records `uv pip freeze` afterward. This is **not** a pre-trial, wheel-hashed dependency closure. (`R/subject/workflow/verify.py:256-272,409-416`)
- [OBSERVED] FXL hashes component names, versions, scope, paths and contents; it rejects symlinks. Reuse content identity, not its versioned-directory convention. (`FXL/src/factory_x_loops/engine/workflow/components.py:107-128,211-226`)

## Registry location and references

| Option | Reference scheme | Tradeoff and decision |
|---|---|---|
| In-repository only | [INFERENCE] `define@N`, current checkout | Simple atomic reviews, but unqualified names and unavailable historical bytes are insufficient for sharing. Reject as the complete design. |
| Separate central registry repository | [INFERENCE] namespace + `name@N` + digest | Independent publication and governance; introduces another checkout and coordinated release process. Strongest alternative. |
| Direct Git references | [INFERENCE] repository + full commit + subdirectory + digest | Distributed and private-friendly; Git identity alone does not close dependencies or establish compatibility. Use as a future transport, not workflow identity. |
| OCI registry | [INFERENCE] repository + immutable digest | Good blob distribution, retention and existing authentication; adds artifact-layout and operational machinery. Defer transport. |
| **Local registry plus portable imports** | [INFERENCE] namespace + `name@N` + package digest | **Recommend.** Local authoring stays boring; identical package bytes can later arrive through Git, archives or OCI without changing workflows or locks. |

[INFERENCE] **Steelman the separate registry:** it makes third-party publication independent of Raidar releases and provides a natural review, ownership and retention boundary. It is preferable once publication volume needs independent governance. Reject it initially because those benefits do not require a second repository today: establish portable identity now, then move distribution without moving the contract.

## Concrete package and workflow

[INFERENCE] Proposed notation below is not supported YAML today. `D_DEFINE` and `D_VERIFY` stand for generated 64-character SHA-256 values, not literal configuration. Runtime pins are illustrative and must match the selected pack.

```text
lab/stages/define/
├── stage.yaml
├── prompt.md
├── skills/feature-definition/SKILL.md
├── tool/define_tool/                 # ordinary, unversioned modules
├── tools.lock.json                  # generated transitive dependency/runtime closure
└── fixtures/
    ├── cases.yaml                    # inputs, invocation sequence, expected results
    ├── unsealed/                    # fixture workspace
    └── sealed/                      # fixture workspace
```

[INFERENCE] Package manifest: identity, declared capabilities and method configuration; **no release number or self-digest**. Release numbers remain in the register.

```yaml
# lab/stages/define/stage.yaml
schema: stage/1
namespace: raidar
name: define
runtime: {pack: python, interpreter: "3.12.14"}
tool:
  payload: tool/
  lock: tools.lock.json
  commands:
    define: [python, -I, -B, -m, define_tool]
requires:
  primitives: [gate/1, stage-result/1]
provides:
  criteria: criteria@1
prompt_file: prompt.md
skills: [skills/feature-definition]
settings:
  unit_rules: {type: integer, enum: [2], default: 2}
setup: {argv: [define, stage, setup]}
on_stop:
  argv: [define, stage, inspect]
  protocol: stage-result/1
  max_rounds: 3
gate:
  - name: sealed
    argv: [define, stage, sealed]
    before: true
outputs:
  criteria: {path: .raidar/define/criteria.json, schema: criteria@1}
fixtures: fixtures/cases.yaml
```

[INFERENCE] The `stage` subcommands above are proposed package adapters, not claims about the current define CLI. Their deadlines, result obligations and terminal semantics come from the Raidar contract, not package-provided host code.

```yaml
# lab/releases.yaml — proposed addition; existing agents/workflows remain unchanged
stages:
  raidar:
    define@1: D_DEFINE
    verify@1: D_VERIFY

# lab/workflows/define-build-verify/workflow.yaml — proposed replacement
schema: 2
name: define-build-verify
stages:
  - id: define
    namespace: raidar
    use: define@1
    sha256: D_DEFINE
    settings: {unit_rules: 2}
  - id: verify
    namespace: raidar
    use: verify@1
    sha256: D_VERIFY
build:
  prompt: |-
    %%%DEFINE%%%
    Build the requested solution.
    %%%VERIFY%%%
    %%%TASK%%%
  reply: acceptance-reply/1
judge:
  primitive: jev-judge/1
  criteria: define.criteria
  criteria_schema: criteria@1
  tests: verify.tests
```

[INFERENCE] `verify` uses exactly the same package contract: prompt/skills, Python check payload, closed tool lock and fixtures for commit refusal, passing seal and bounded fix turns. Neither shipped package receives privileged registry treatment; build is never a package.

## Namespaces, publication and consumption

- [INFERENCE] Keep authored packages at `lab/stages/<name>/`. Extend the existing register with a namespaced `stages` mapping; reuse its `name@N` parser and integer releases within each namespace.
- [INFERENCE] Reserve `raidar` for upstream packages; external publishers use stable reverse-domain namespaces such as `org.example.tools`. Namespaces distinguish identity, **not authentication**. Consumers explicitly bind namespaces to approved sources; never search public registries as fallback.
- [INFERENCE] Publish upstream through a reviewed repository change. Independent authors run the same checks/release command in their own checkout and distribute the resulting archive plus release mapping through Git or an artifact service. Raidar-source familiarity is unnecessary.
- [INFERENCE] `stage-import` accepts a local checkout or exported archive, verifies the expected namespace/release/digest and dependency closure, then stores immutable material under `var/stages/sha256/<digest>/`. Workflows reference it; they never receive their own package copies.
- [INFERENCE] A checked-in release mapping records imported pins; credentials and transport locations stay in local configuration/provenance, outside execution identity. The first implementation needs no remote resolver.
- [INFERENCE] Forks may consume unchanged upstream bytes under upstream identity. Modified packages use the fork’s namespace; private teams use the identical format with private source bindings. Namespace takeover or conflicting bytes for an existing release must be refused.
- [INFERENCE] Preserve today’s latest-only rule for authored working trees. Historical consumption requires explicitly imported archived bytes matching the registered digest; never silently substitute the latest tree. This adds retrieval, not a second version convention.

## Releases, drafts and dependency closure

- [INFERENCE] `name@N` is immutable. Tool, prompt, skill, fixture, dependency-lock or other shipped-byte changes require the next integer release. Consumers update package pins, release changed workflows and regenerate experiment locks.
- [INFERENCE] The release command refuses replacement of an existing mapping; repository review/CI checks existing mappings against protected release history. A hash in a mutable YAML file alone cannot enforce immutability.
- [INFERENCE] Published workflows require both exact release and digest. Reject floating versions, branches, tags, ranges and unresolved URLs at release/lock time; execution never resolves references.
- [INFERENCE] Drafts use an explicit local path in a scratch workflow. `stage-check` and draft smoke locking snapshot its bytes into the same content store and mark it unreleased. Measurement admission refuses drafts; normal release checks remain strict.
- [INFERENCE] Generate `tools.lock.json`; never edit it or experiment locks by hand. Record every direct/transitive distribution, exact version, selected wheel filename, wheel SHA-256, dependency edges, extras, evaluated markers, target OS/architecture/ABI, interpreter identity and immutable runtime-image identity.
- [INFERENCE] Require a complete wheel set per supported target. Reject unresolved dependencies, editable installs, source distributions and install-time downloads/builds. Custom tools ship source payloads; any required wheel building happens separately, unprivileged, before release.
- [INFERENCE] Keep dependency bytes in content-addressed storage; exports carry the complete selected closure. Import and lock verify every referenced blob. The trusted installer consumes only verified bytes offline; it never executes publisher setup hooks as root.

## Contract compatibility and clean cutover

- [INFERENCE] `stage/1` versions manifest/lifecycle semantics; `gate/1`, `stage-result/1`, `acceptance-reply/1`, `jev-judge/1` and `criteria@1` independently identify primitive or data contracts. Package release integers mean immutable revisions, not compatibility ranges.
- [INFERENCE] The manifest declares requirements and provided outputs. The workflow selects packages, explicit settings, output wiring and enabled named judge capabilities. Effective requirements are their union; workflows cannot weaken package requirements.
- [INFERENCE] Extend lock-time admission to reject unsupported contracts, unknown settings, unavailable outputs, schema mismatches, duplicate command/skill names, invalid ordering, incompatible runtime targets and incomplete dependencies. Runtime checks still verify bytes and installation; they do not discover declared incompatibility.
- [OBSERVED] Lock building already collects resolution and runtime-compatibility errors before sealing the lock. Extend this path rather than creating a parallel validator. (`R/design/experiment/lock.py:382-418`; `R/design/experiment/runtime_gate.py:1-24`)
- [INFERENCE] A breaking contract change gets a new identifier; migrate shipped packages/workflows, regenerate locks and require fresh smoke admission in one cutover. Remove the old runtime path rather than adding compatibility aliases. Historical jobs remain inspectable, not compatible by implication.
- [INFERENCE] Do not settle malformed stop-result classification here. Finalizing that separate decision is a prerequisite to freezing `stage-result/1` and its fixtures.

## Identity and exact hash specification

- [OBSERVED] Raidar’s canonical digest is SHA-256 of compact, key-sorted UTF-8 JSON, preserving Unicode and rejecting non-finite numbers. Reuse it unchanged. (`R/contract/canonical.py:16-34`)
- [INFERENCE] Define **H(x)** as that function. Define each file record as `{path, executable, size, sha256}`: package-relative POSIX path, normalized executable boolean, byte length and SHA-256 of exact bytes.
- [INFERENCE] Inventory every shipped regular file, including raw `stage.yaml`, prompts, skills, payload, dependency lock and fixtures. Sort by path; reject symlinks, traversal, special files and duplicate normalized paths. Exclude only Raidar’s existing ignored-tree names; never install excluded files.
- [OBSERVED] Those ignored names are `.DS_Store`, `__pycache__` and `.git`. (`R/contract/canonical.py:11-13`)
- [INFERENCE] **Package digest D = H({scheme: "stage-content/1", manifest: resolved_manifest, files: sorted_file_records, runtime: resolved_runtime_record, dependencies: sorted_dependency_records}).** Resolve contract defaults before hashing. Dependency records contain the complete selected closure described above, including wheel hashes; runtime records contain interpreter and image digests.
- [INFERENCE] Do not hash timestamps, ownership, checkout paths, archive compression, registry URLs, credentials or download time. Fix installation ownership/modes by contract. The same package imported elsewhere must produce the same D.
- [INFERENCE] Release number is outside D. The lock separately records namespace, `name@N` and D. Package code uses **raw-byte** hashes, not AST normalization; even comment-only payload changes therefore create new package identity.
- [INFERENCE] **Instance identity = H({id, namespace, release, package_digest, effective_settings, effective_bindings}).** Settings include defaults, overrides, deadlines, limits and enabled capabilities. Bindings include output wiring and referenced contract identifiers.
- [INFERENCE] **Workflow identity** hashes the resolved workflow/build frame, its own shipped files and ordered package-instance records. **Arm identity** retains agent release/pin, model/reasoning and capabilities, then adds workflow identity and primitive-code digests.
- [INFERENCE] **Primitive digest = H({contract, source_scheme, modules, resources, dependencies}).** `modules` maps sorted repository-relative paths to existing normalized-source digests; include shared execution/evidence/installation code, the selected primitive’s transitive Raidar helpers and selected adapter. Hash non-Python resources by bytes; record Harbor and other runtime dependency pins plus distribution hashes. Missing inventory coverage is a correctness defect.
- [OBSERVED] Today’s stage digest already separates implementation from workflow content, using `python-ast/1` and Harbor’s version. Replace its closed stage-type inventory with primitive inventories. (`R/subject/workflow/stages.py:28-96,107-122`; `R/contract/source_digest.py:24-25`)
- [INFERENCE] Store package inventories, effective settings and primitive maps in `execution`; retain `lock_sha256 = H(execution)`. Keep assessment and analysis identities separate. Reverify stored bytes before launch.
- [OBSERVED] That execution-only lock hash already exists; current arm locks contain workflow and stage-code identities. (`R/design/experiment/lock.py:460-499`; `lab/experiments/smoke/python__forge__define-spike/experiment.lock.json:1027-1059,1224-1234`)
- [INFERENCE] Pooling must refuse package-byte, effective-setting, contract, runtime-closure or primitive-code mismatches **for corresponding arms across jobs**, with no override. Different treatments remain valid separate arms; they are not pooled as the same treatment.
- [OBSERVED] This tightens current behavior: stage-code differences are non-overridable, but `subject` differences are overridable. Smoke admission already compares full arm identity. (`R/evaluation/analysis/pooling.py:45-56,170-172`; `R/engine/health/admission.py:50-105`)

## Trust model

- [INFERENCE] A shared package is third-party code running as the agent’s user inside the trial container—not a host plugin and not a separately trusted agent. Root may copy verified bytes and set permissions; package code, fixtures and dependency hooks never execute as root or on the host.
- [INFERENCE] Preserve the boundary: no host filesystem, Docker socket, hidden verifier bundle, host-only judge credentials or authoritative evidence store is mounted into package reach. Host-owned records capture invocation, input/output digests, exit status and required-output obligations; packages cannot write their own authoritative verdicts.
- [OBSERVED] Current verify already distinguishes host-owned records from agent-writable draft material and executes workspace-touching checks as the agent user. (`R/subject/workflow/verify.py:7-34`)
- [INFERENCE] These guarantees assume the container boundary holds. They do not establish checker honesty: a malicious package can alter the workspace, fabricate method outputs, manipulate prompts, consume budget or mislead the agent and judge. Hidden grading remains separate; package outputs cannot define scores.
- [INFERENCE] **Offline closure is mandatory:** no dependency resolution, installation downloads or package-index access during trials. Package invocations must not require network; enforce denied egress for those invocations. Necessary harness/provider communication is a separately declared capability, not package dependency access.
- [OBSERVED] Current agents retain provider/package-host networking, and Codex receives an in-container auth file. Thus “no package can access any secret or network” is not a present guarantee. (`lab/agents/codex.yaml:11-15,35-40`; `lab/agents/claude-code.yaml:31-35`)
- [INFERENCE] Same-user malicious code is not isolated from agent-accessible credentials/capabilities. Keep judge secrets host-only; do not claim protection for harness credentials exposed to that user. Stronger protection requires credential brokering and enforced process/network isolation, not manifest promises.
- [INFERENCE] For the lab’s own decisions, admit only reviewed **digests**, including dependency closures, with passing package checks and matching real smokes. Registration is not endorsement; hashes prove identity, not publisher authenticity. Private registries need the same controls.

## Public make targets and authoring loop

| Target | Proposed responsibility |
|---|---|
| `make stage-new STAGE=… NAMESPACE=…` | [INFERENCE] Create a documented package and minimal fixture cases; no orchestrator edits. |
| `make stage-lock STAGE=…` | [INFERENCE] Generate/refresh the complete target-specific tool/runtime lock and verified blob cache. |
| `make stage-check STAGE=…` | [INFERENCE] Validate schema/closure; run fixtures unprivileged and offline on the selected runtime; check outputs, refusals, terminal behavior and immutability. |
| `make stage-release STAGE=…` | [INFERENCE] Require checks; allocate next `@N`, append its digest and export a complete portable archive under `var/`. Never overwrite history. |
| `make stage-import SOURCE=… REF=… SHA256=…` | [INFERENCE] Verify and import a checkout/archive without executing it; reject conflicting identities and incomplete closure. |
| `make stage-list` / `make stage-info STAGE=…` | [INFERENCE] Show namespace, releases, digest, requirements, settings, provenance and review/check status distinctly. |
| Existing release/experiment targets | [INFERENCE] Extend `releases-check`; use `experiment-lock` for all lock writes, then the existing real-smoke and health flow. |

[INFERENCE] Authoring sequence: create/edit → generate dependency lock → run fixtures → draft smoke → review/release → update workflow pin/release → regenerate experiment lock → quality/release checks → matching real smoke. Publish README-linked contract documentation, expanded settings and worked define/verify examples before asking outsiders to author packages.

## First version, deferrals and risks

- [INFERENCE] **Build first:** shared in-repo packages; namespaced release mappings; exact digest pins; immutable local imports/exports; dependency closure; strict compatibility checks; draft snapshots; generic identity/evidence integration; define and verify on the same path. Python-only and one supported target are acceptable if unsupported targets fail explicitly.
- [INFERENCE] **Must be stable immediately:** namespace/release identity, canonical package digest, portable relative paths, closure format, contract identifiers and transport-independent locks. Later remote fetchers only populate the existing content store; they require no workflow or identity migration.
- [INFERENCE] **Defer:** hosted catalogue/search, automatic Git/OCI fetching, signing infrastructure, multi-runtime builds, generators and package-to-package dependency graphs. Do not defer retention/export, integrity checks or explicit source trust.
- [INFERENCE] **Acceptance:** import an exported package into a second checkout without source edits; obtain identical D and settings identity; mutate prompt/tool/dependency/settings independently and observe stale-lock/admission refusal. Exercise define/verify and an external command-check package in real trials.
- [INFERENCE] **Risks:** unavailable archived blobs prevent reproduction; AST inventories can omit helpers; offline wheels limit platforms; fixture-byte changes conservatively invalidate identity; migration can alter subtle define/verify behavior. Retain complete exports, audit inventories and require behavioral smoke evidence.
- [OBSERVED] Only existing release validation and one existing experiment-lock check were exercised here. Proposed package behavior, isolation changes and real-trial acceptance remain unimplemented; no full quality run was performed. (`Makefile:402-407`)

## Open questions for the owner

1. [INFERENCE] Approve digest-reviewed packages plus matching smokes as mandatory for the lab’s own decisions, while allowing explicitly labelled unreviewed draft smokes?
2. [INFERENCE] Which initial runtime/platform and archive-retention owner are required? Recommend the current supported Python target and retained complete release exports.
3. [INFERENCE] Is protecting host-only judge secrets sufficient initially, or must untrusted packages also be unable to access harness credentials? The latter is an isolation prerequisite, not registry work.
4. [INFERENCE] Confirm the separate stop-result decision before freezing `stage-result/1`; registry publication must not freeze unresolved lifecycle semantics.
