# Decision: package stages; keep execution guarantees in Raidar

## Problem

[OBSERVED] The burden belongs to two different users: the **workflow author**, who wants to assemble or vary a process experiment, and the **method author**, who must supply new instructions and sometimes bespoke tooling. Raidar must make the former inexpensive without pretending the latter disappears. Both need predictable execution: one primary session, bounded resumes, protected tools, host-owned evidence, invalidation when required evidence is missing, harness isolation and parity, host-only judge secrets, complete execution identity, and separate hidden-test grading. The objective is lower conceptual load—not merely fewer Python lines. (`var/tmp/stage-council/context.md:4-9,24-34`)

[INFERENCE] **Recommend E: a stage is a reusable package referenced by a workflow, backed by a small, constrained declarative execution contract.** Supply define, build and verify as packages using that same contract. Do not introduce a privileged “custom Python stage” escape hatch or make prompt instructions responsible for enforcing lifecycle rules.

## Evidence that changes the decision

[INFERENCE] Citation prefixes below: `W/` = `orchestrator/src/raidar/subject/workflow/`; `H/` = `orchestrator/src/raidar/engine/health/`; `FXL/` = `/Users/adamjackson/Projects/inflight/factory-x-loops/factory-x-loops/`.

- [OBSERVED] Existing stages already expose substantial author-owned configuration, but the stage universe and several settings are closed. A prompt/check tweak is not equivalent to adding a stage. (`W/definition.py:58-72,87-109,124-166,191-291`)
- [OBSERVED] The factory shares an `Acceptance` object and attaches define’s prerequisite specifically to verify. The lifecycle interface alone therefore does not make stages independently extensible. (`W/factory.py:15-35`)
- [OBSERVED] Verify is not an arbitrary-command runner: it selects Python files, appends them to commands, and treats pytest specially. Relabelling a docs checker as “lint” would not provide the requested generic stage. (`W/verify_gate.py:274-338,375-385`)
- [OBSERVED] Define is more than setup/check/resume: it computes units through imported tool code, accepts clarification proposals, refreshes context, limits repairs, permanently closes unusable definitions, and loads acceptance criteria. (`W/define.py:162-287`)
- [OBSERVED] Define’s stop check returns immediately once marked ready or unusable; the commit/seal prerequisite performs additional inspection. “Re-check on stop” must preserve these actual transition semantics, not imply unconditional repeated execution. (`W/define.py:64-77,196-240`; `W/verify.py:615-628`)
- [OBSERVED] Structured replies have syntax and semantic repair, incremental criterion-to-test merging, and cross-stage consumption by the judge. A generic JSON parser alone does not reproduce build. (`W/build.py:1-19,84-148`; `W/judgeloop.py:105-160`)
- [OBSERVED] Identity and evidence enforcement explicitly enumerate current stages. Adding a configurable stage without changing these paths would leave guarantees incomplete. (`W/stages.py:28-75,99-122`; `H/evidence.py:145-153,333-385`)
- [OBSERVED] FXL separates method skills from prompt contracts and hashes component contents, but its engine still explicitly wires the define executable and environment. It demonstrates useful packaging—not a fully generic pointer runtime. (`FXL/workflow/define-build-verify/v1.toml:1-18,41-50`; `FXL/src/factory_x_loops/engine/workflow/components.py:1-6`; `FXL/src/factory_x_loops/engine/lifecycle/runner.py:116-152`)

## Options and authoring cost

[INFERENCE] These choices are not all substitutes: A–C describe execution surfaces; D is assistance; E is packaging. E can use B’s constrained primitives without exposing an unrestricted workflow language.

[INFERENCE] Counts below are design estimates, not measured usability results. **C** counts separately learned contracts; **F** counts human-authored files, excluding tests, documentation and generated locks. **T** is the bespoke tool’s implementation-file count; **S** is optional skill-file count. Task (iii) means authoring a **new define-like method**, not merely enabling shipped define. Every change still requires regenerated identity and verification.

| Option | (i) Tweak verify checks/prompt | (ii) Add pinned docs-check; resume on failure | (iii) Add a new define-like method | Raidar code needed; guarantee risk |
|---|---|---|---|---|
| **A — fixed code types** | [INFERENCE] Edit supported prompt/check settings: **2C** (prompt tokens, check contract), **1F**. Changing unsupported semantics becomes code work. | [INFERENCE] Implement stage; register schema, construction, identity and evidence; enable it: **8C** (those five contracts plus provisioning, prompt, trigger/resume), **≥6F**. | [INFERENCE] Same integration plus tool protocol, token production, artifact dependencies and judge/reply compatibility: **12C**, **≥6+T+S F**. | [INFERENCE] No code for supported tweaks; each new type needs stage implementation plus `definition.py`, `factory.py`, `stages.py`, health integration. Guarantees depend on every contributor correctly maintaining privileged internals. |
| **B — fully declarative stages** | [INFERENCE] Edit prompt/check declarations: **2C, 1F**. | [INFERENCE] Declare pin, argv, prompt, trigger, bounded resume and workflow placement: **6C, 1F**, with inline content. | [INFERENCE] Implement tool; declare installation, setup/token extraction, commit prerequisite, stop check, resumes, outputs and judge/reply connections: **12C, 1+T+S F**. | [INFERENCE] One-time generic runtime, schema, identity and evidence work; thereafter **0** for these cases using supported contracts. Broad expressions, arbitrary host commands or configurable evidence exemptions threaten the guarantees. |
| **C — built-ins plus custom** | [INFERENCE] Use built-in settings: **2C, 1F**. | [INFERENCE] Learn custom-stage schema plus B’s six contracts: **7C, 1F**. | [INFERENCE] Learn custom/built-in boundary plus B’s method contracts: **13C, 1+T+S F** if custom supports them; otherwise author another built-in. | [INFERENCE] One-time custom engine; **0** per supported custom stage. Separate built-in/custom execution paths risk unequal identity, failure handling and evidence coverage. |
| **D — generator over A, B, C or E** | [INFERENCE] Describe change, inspect generated diff: **2 upfront C**; review the substrate’s **2C/1F**. | [INFERENCE] Describe behavior, review generated contract: **2 upfront C**, but still B/E’s **6C** and **1–2F** to understand. | [INFERENCE] Specify method and review generated implementation: **2 upfront C**, but still **12C** and **1–3+T+S F**. Bespoke tooling remains engineering work. | [INFERENCE] Inherits substrate code requirements; over A it still generates Raidar changes. Generation cannot establish safety, semantic correctness or complete identity. |
| **E — packaged pointer, constrained manifest** **(recommended)** | [INFERENCE] Edit package prompt/check configuration: **2C, 1–2F**. A published package change also updates the workflow reference: **+1F**. | [INFERENCE] Write one manifest with inline instructions; add workflow pointer: **6C, 2F**. No wrapper needed for a tool with conventional exit codes. | [INFERENCE] Write tool/adapter, manifest, instructions and workflow pointer: **12C, 3+T+S F**. A later consumer only learns package selection/settings: **2C, 1F**. | [INFERENCE] One-time package resolver plus B’s bounded kernel; thereafter **0** for these supported cases. An opaque pointer or prompt-only setup weakens execution/evidence/identity guarantees; the manifest is mandatory. |

[INFERENCE] E does **not** make first-time method creation cheaper than B. Its advantage is amortization: the method publisher owns the integration once; experiment authors reuse it without reconstructing installation, tokens, checks and output wiring.

[INFERENCE] For the twelve complex-method contracts counted above: composition, prompt, tool semantics, dependency/install contract, setup, token extraction, triggers, bounded resumption, output schema, cross-stage dependencies, structured reply, and host judge inputs. They remain real even when a generator hides their spelling.

## Recommended boundary

[INFERENCE] Use this ownership split; do not make authors implement the lab’s security machinery:

| Raidar owns | Method/package author owns |
|---|---|
| [INFERENCE] Immutable installation and dependency resolution; safe paths; unprivileged command execution; deadlines and process cleanup. | [INFERENCE] Tool implementation, pinned dependencies, method-specific state and checks. |
| [INFERENCE] Prompt composition, adapter-specific skill placement, one primary session and bounded resume scheduling. | [INFERENCE] Prompt/skills, setup argv, token declarations, check argv, failure messages and configured limits. |
| [INFERENCE] Commit-hook broker, stop-time observation, immutable terminal status, host-written execution records and required-evidence rules. | [INFERENCE] Meaning of method pass/refusal; declared outputs and versioned output schemas. |
| [INFERENCE] Structured-reply machinery and explicitly supported host judge capabilities, including secret handling. | [INFERENCE] Selection/configuration of those capabilities and compatible input declarations—not arbitrary host plugins. |
| [INFERENCE] Lock closure, snapshot exclusions and separate hidden grading. | [INFERENCE] Versioned package contents and explicit workflow composition. |

[INFERENCE] **Setup instructions in a prompt are guidance, not setup execution.** Raidar must install and prepare the stage before the agent starts; otherwise preparation depends on agent compliance and cannot be guaranteed.

[INFERENCE] Keep a linear stage list, explicit named data dependencies, and commit/stop triggers. Reject cycles and unavailable contracts before launch. Do not add arbitrary scheduling, user-defined privilege levels, host callbacks or harness subagents.

## Concrete authored artifacts

[INFERENCE] The following is **proposed notation, not currently supported YAML**. Every block belongs to the same package contract. Tool references resolve to immutable bytes and dependency closures in the lock; local paths are content-addressed, not mutable runtime lookups.

[INFERENCE] A workflow author assembles packages; list order governs preparation, prompt contributions and stop checks—not separate agent sessions:

```yaml
# workflow.yaml — workflow author
schema: workflow/2
stages:
  - {id: define, use: ./stages/define}
  - {id: build,  use: ./stages/build}
  - {id: verify, use: ./stages/verify}
```

[INFERENCE] Define’s publisher owns this manifest, `instructions.md`, and the tool payload. `define-stage` is a **new package adapter**, not an existing CLI command; it must implement the declared prepare/ready/check protocol around the existing define tool.

```yaml
# stages/define/stage.yaml — method author
schema: stage/1
tool: {payload: ./tool, entry: define-stage, runtime: python@3.12.14}
prompt_file: instructions.md
settings: {unit_rules: 2, max_refinement_rounds: 2, max_repairs: 1}
prepare:
  argv: [define-stage, prepare]
  input: task
  tokens: {UNITS: /units}
commit_requires:
  argv: [define-stage, ready]
after_stop:
  argv: [define-stage, check]
  protocol: stage-result/1
  max_resumes: 3
outputs:
  acceptance:
    path: .raidar/feature-definition.md
    schema: feature-definition/1
    required_when: ready
```

[INFERENCE] `instructions.md` contains the method instructions and `{{UNITS}}`. The adapter computes units, manages clarification proposals and emits validated `ready`, `resume`, `unusable` or `error` results. Raidar owns substitution, the overall resume bound and irreversible unusable status; the package implements the method’s two-refinement/one-repair policy. Commit prerequisites cannot reopen a terminally unusable stage.

[INFERENCE] The output declaration names method data, **not authoritative evidence**. Raidar snapshots and hashes it after checking, validates its supported schema, and records whether it was owed. Missing output after a declared ready result is an instrument/protocol failure; an unusable definition with complete terminal evidence is a recorded method outcome.

```yaml
# stages/build/stage.yaml — method author
schema: stage/1
prompt: |-
  Work the issue under the repository's rules.
  {{stage.define.prompt}}
  Build against the sealed definition.
  {{stage.verify.prompt}}
  {{TASK}}
reply:
  primitive: acceptance-reply/1
  criteria: define.acceptance
  syntax_repairs: 1
  semantic_repairs: 1
```

[INFERENCE] `acceptance-reply/1` is Raidar-owned: normalized final-text parsing, schema checks, incremental `ac_tests` merging and bounded repairs. It is a named compatibility contract, not “any JSON schema implies any downstream semantics.”

```yaml
# stages/verify/stage.yaml — method author
schema: stage/1
tool: {payload: ./python-checks, entry: python-checks, lock: ./tools.lock}
prompt_file: instructions.md
checks:
  argv: [python-checks, run]
  result_schema: gate-tests/1
  on_commit: block
  after_stop: {max_fix_rounds: 2}
judge:
  primitive: jev-aspects/1
  criteria: define.acceptance
  mappings: build.ac_tests
  tests: checks.tests
  model: typesafe/jev-1.13-20260917
  closed_at: 0.8
  open_at: 0.3
  max_passes: 3
  rework_file: rework.md
```

[INFERENCE] The verify package owns Python check policy and exact tool pins; Raidar owns the hook, checked-tree association, host record and named judge implementation. `jev-aspects/1` must preserve the existing baseline, stall, budget and rework rules through explicit, locked defaults. A non-judging verify package omits `judge`; a one-shot build omits define/verify prompt slots and structured replies.

[INFERENCE] A simple docs-check needs only a manifest declaring its pinned executable, argv, inline prompt, stop trigger, failure message and resume bound, plus the workflow pointer. It needs neither a Python stage class nor the structured method adapter used by define.

## Guarantee check

[INFERENCE] “Preserve” below is conditional on implementation and acceptance evidence—not a claim that these proposed surfaces already exist.

| Guarantee | A | B | C | D | E, constrained |
|---|---|---|---|---|---|
| No agent-written code runs as root | [INFERENCE] Preserve through reviewed code | [INFERENCE] Preserve only without root-command escape | [INFERENCE] Same restriction for custom | [INFERENCE] Inherits substrate | [INFERENCE] Copy immutable payloads as root; run package code unprivileged |
| Host-owned evidence; missing required evidence invalidates | [INFERENCE] Each type needs coverage | [INFERENCE] Kernel derives obligations | [INFERENCE] Both paths must share obligations | [INFERENCE] Generated declarations are not proof | [INFERENCE] Kernel records every invocation and expected output |
| Harness isolation/parity; one primary session | [INFERENCE] Preserve through adapters | [INFERENCE] No harness-specific commands in lifecycle | [INFERENCE] Same adapters for both paths | [INFERENCE] Reject generated bypasses | [INFERENCE] Placement and resume remain runner-owned |
| Judge secrets never enter container | [INFERENCE] Preserve host judge | [INFERENCE] Named host capability only | [INFERENCE] No custom host-code shortcut | [INFERENCE] Inherits substrate | [INFERENCE] Packages receive results, never credentials |
| Identity covers behavior | [INFERENCE] Maintain code inventory | [INFERENCE] Hash resolved declarations and primitives | [INFERENCE] Include both implementations | [INFERENCE] Lock generated result, not requirements prose alone | [INFERENCE] Hash package bytes, settings, dependencies and kernel contracts |
| Separate hidden grading; workflow payloads not scored | [INFERENCE] Preserve exclusions | [INFERENCE] Enforce reserved artifact locations | [INFERENCE] Same exclusions for custom | [INFERENCE] Cannot generate scoring privileges | [INFERENCE] Packages cannot classify arbitrary solution files as excluded |
| Bounded failures and resumes | [INFERENCE] Each type implements bounds | [INFERENCE] Kernel enforces bounds | [INFERENCE] Shared global deadline required | [INFERENCE] Generated bounds still validated | [INFERENCE] Per-stage limits plus checkpoint deadline |

[INFERENCE] **Reject E interpreted as “a pointer plus instructions telling the agent what to install/run.”** It weakens deterministic setup, required-evidence coverage, identity closure and bounded execution. Also reject unrestricted B and a C implementation that gives custom stages weaker evidence rules.

[OBSERVED] Current evidence policy distinguishes valid unfavorable agent outcomes from broken instruments, and allows narrowly defined artifact exceptions after cuts or unusable definitions. The replacement must retain that distinction rather than declaring every missing method output invalid. (`H/evidence.py:19-46`)

## Strongest rival and self-attack

### Steelman C: built-ins plus one custom type

[INFERENCE] C is the strongest rival because existing define/build/verify already encode difficult semantics. Keeping them intact while adding a narrow command-check stage could deliver the docs-check experience sooner, with less migration risk. Most experiment authors might never need another complex method.

[INFERENCE] C wins if the intended product is “vary these supplied methods, occasionally add a command.” It loses if external authors must publish genuinely new methods: they eventually face either an underpowered custom type or a second privileged integration API.

### Attack E: packaging can conceal, rather than remove, complexity

- [INFERENCE] **Hidden complexity:** a manifest plus adapter can become a workflow engine hidden inside each package. Keep adapters limited to method state and results; session control, evidence, privilege and secret-bearing operations remain unavailable to them.
- [INFERENCE] **External-author mistakes:** mutable tool tags, wrong exit-code interpretation, tokens referencing absent output, incompatible judge inputs, stale output reuse and conflicting prompts. Resolve identities and validate contracts before launch; show the fully expanded plan rather than silently inferring connections.
- [INFERENCE] **Mid-trial failures:** a hung command, malformed result or missing required output must produce a host-recorded instrument failure, not a “please retry” agent turn. A normal check refusal can resume within bounds; exhaustion is a recorded unfavorable outcome. Deadline cuts retain partial evidence and their explicit reason.
- [INFERENCE] **Cleanup failures:** one package’s failure must not prevent other stages’ closure or the final evidence record. Terminal recording and process cleanup must be runner-owned, not supplied by package callbacks.
- [OBSERVED] The present runner awaits stage closures sequentially before publishing its final record; a generic extension must not assume this already isolates arbitrary closure failures. (`W/runner.py:320-332,437-439`)
- [INFERENCE] **False confidence:** host-owned evidence proves what ran and what it reported; it does not prove an author’s checker is scientifically useful or honest. Method effectiveness remains an experimental outcome.
- [INFERENCE] **Versioning:** a friendly package version is insufficient. Lock the complete resolved package, prompts/skills, tool dependency bytes, settings/defaults, artifact/reply contracts and relevant execution implementation. Changed behavior requires fresh identity and smoke admission.
- [INFERENCE] **Migration risk:** define’s clarification and closure policy, build’s semantic repairs, and verify’s protected evidence are easy to lose behind generic names. Extract them with observable parity criteria; do not replace them with superficially equivalent exit-code checks.

## Recommendation and first steps

1. [INFERENCE] **Build one constrained kernel and package contract first.** Extract protected execution, commit/stop triggers, bounded resumes, output capture and generic evidence obligations. Support plain-command packages and the small structured result protocol; no arbitrary host imports.
2. [INFERENCE] **Prove docs-check before broadening expressiveness.** An external author must add it with one manifest and one workflow-pointer edit. If Raidar code changes are required, the authoring contract is not complete.
3. [INFERENCE] **Migrate supplied stages onto the same path.** Keep define method logic in its package; retain structured replies and secret-bearing judging as named Raidar capabilities. Preserve current clarification, repair, baseline, seal, terminal and judge behavior.
4. [INFERENCE] **Publish worked packages and an expanded-plan view.** Documentation must explain author versus runner ownership, failure meanings, output contracts and lock changes without requiring source reading.
5. [INFERENCE] **Defer generation, remote package registries, general dependency graphs, isolated review sessions and arbitrary new host-side judges.** A generator comes after the hand-written authoring path works; it emits ordinary reviewable packages, never runtime exceptions.

### Acceptance test

- [INFERENCE] Give an author unfamiliar with Raidar internals only the public authoring documentation and a pinned docs-check executable. They add the stage without editing `orchestrator/src`, health code or an identity allowlist.
- [INFERENCE] Run a real trial where the check first refuses, the **same primary session** receives its message, and a subsequent check passes. Also exercise exhausted resumes and a command crash; verify distinct recorded outcomes.
- [INFERENCE] Confirm non-root execution, immutable installed tools, host-owned records, and invalidation after removing required evidence. Confirm stage outputs never enter the graded solution snapshot.
- [INFERENCE] Change the prompt, tool bytes and a resume setting independently; each must change execution identity and invalidate stale admission. An unresolved mutable reference must be refused.
- [INFERENCE] Run the same stage through both supported harness adapters and inspect normalized execution/evidence behavior. This proves adapter parity, not equal model performance.
- [INFERENCE] Complete the public quality gate, lock validation and real smoke. Then repeat the exercise with a define-like package to prove tokens, commit prerequisite, stop inspection and downstream judge consumption—not merely command execution.

## Open questions for the owner

- [INFERENCE] **Who publishes methods?** Is the commitment that users can package tools they possess, or that Raidar supplies new bespoke methods on request? The latter adds method development; no authoring syntax eliminates it.
- [INFERENCE] **How broad must host judging become?** Is the existing acceptance/aspect contract sufficient initially? A fundamentally different secret-bearing judge should require a reviewed Raidar capability, not package host code.
- [OBSERVED] The brief says workflow outputs are never scored, while the README names offline definition/test-coverage scorers and a gate-effort scorer consuming workflow records. (`var/tmp/stage-council/context.md:8`; `README.md:56,59`)
  [INFERENCE] Clarify whether “never scored” means excluded from solution-quality grading or prohibited from all method diagnostics. Preserve separation either way; do not let package authors self-award trial scores.

## Verification boundary

[OBSERVED] Read-only release validation succeeded: `make releases-check` returned `releases: ok`, exercising current workflow loading and registered hashes. Dependency synchronization and bytecode writes were disabled; runtime/cache paths were redirected outside the repository. (`Makefile:406-407`; `orchestrator/src/raidar/cli/releases.py:19-27`)

[INFERENCE] No implementation or repository-file edits were made. No proposed package runtime, real-trial acceptance test or full quality run was exercised; the recommendation is a design decision, not implementation certification.
