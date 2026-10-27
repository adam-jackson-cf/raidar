# Stage authoring: decision document

## 1. Problem

The person who carries the burden is the **eval author**. That is an outside, open-source user who wants to test a process idea and will not read Raidar's source. Today they can edit prompts and a few settings of `define`, `build` and `verify`, and nothing else. Any new method needs Raidar code: a new stage type, a settings model, factory wiring, a stage-code digest and health rows. Whatever we choose must keep these guarantees:
- no agent-written code runs as root;
- evidence is written host-side, and missing evidence makes the trial invalid;
- harness isolation and parity hold;
- no secret enters the container;
- the lock covers everything that can change behaviour;
- grading runs in a separate sandbox.

So the question is not how expressive YAML should be. It is **where method logic lives**: the author should write only their method, and Raidar alone should own the mechanics that carry the guarantees.

## 2. Facts that decide it

- **Stage types are a closed set.** [OBSERVED `definition.py:58`, `:257`]
- **Many settings take one value only:** `unit_rules` (`:90`), the gate triggers (`:164`), `question_set` (`:172`) and `language` (`:191`). Check categories are a closed set (`:71`), and there must be exactly one tests check (`:214`). [OBSERVED]
- **A workflow can ship only skill files**, so it cannot ship a tool today. [OBSERVED `definition.py:69`, `:307-310`]
- **The stage interface and runner are already generic.**
  - The interface is `prepare` / `fragment` / `check` / `decorate` / `close` / `record`. [OBSERVED `stage.py:98-129`]
  - When the agent stops, the runner checks the stages in order, and the first resume wins. [OBSERVED `runner.py:12-16`, `:428-434`]
  - New stage kinds would need no runner change. [INFERENCE]
- **Most of `define.py` is generic mechanics.** It installs the payload root-owned (`:82`, `:145`), keeps a root-owned context, re-checks when the agent stops and allows one repair (`:199-237`), and sets a root-owned "closed" flag that the gate prerequisite reads (`:59-77`). [OBSERVED]
- **Two parts of `define.py` are method logic living in lab code:** the host-side import of the tool to number the issue's units (`:167`) and the "accept every proposal" round (`:239-255`). [OBSERVED]
- **Gate prerequisites are already generic.** Each one runs as the agent's user, with a watched state directory. [OBSERVED `verify_gate.py:341`, `:469-470`, `:662`; `factory.py:22-30`]
- **The judge is tied to define's format.** `acceptance.py:18-19` imports `define_tool.featuredef`. The judge needs define plus structured build replies (`definition.py:280-283`). The judge's API key stays host-side (`asking.py:3-5`). [OBSERVED]
- **The evidence checks are written by hand per stage:** rows W3–W10 in `engine/health/evidence.py:332-344`. [OBSERVED] A new stage kind without rows would leave its evidence unchecked. [INFERENCE]
- **Identity:** the lock records one code digest per stage type, and `define_tool` counts towards all three [OBSERVED `stages.py:1-10`, `:70-73`]. The workflow digest covers the definition plus its skill files [OBSERVED `definition.py:356`].
- **factory-x-loops (FXL) is also option A.** Its workflow kinds are a closed set (plain, gated, judged) [OBSERVED FXL `engine/workflow/config.py:116-120`]. Only skills and instructions are versioned components (`:41-46`). The define tool sits in the engine package (`capability/define/`). FXL has not solved this problem either.

## 3. Options

- **A**: fixed stage types in code only.
- **B**: inline YAML stages over a small set of Raidar primitives.
- **C**: built-in `define`/`build`/`verify` plus one `custom` stage type (prompt, skills, pins, command checks on commit or stop, a resume message).
- **D**: a skill or agent that writes stage artefacts from the user's requirements, on top of A, B, C or E.
- **E**: "stage as a pointer". A stage is a bundle folder (manifest, prompt, skills, tool) that Raidar installs by convention and calls through a fixed command protocol.
- **F** (added): the author's Python `Stage` subclass, loaded as a plugin.

Cells read *files touched / concepts*. The tasks are (i) tweak verify, (ii) add a docs-check, (iii) add a define-like stage.

| Opt | (i) | (ii) | (iii) | Raidar code needed | Risk to guarantees |
|---|---|---|---|---|---|
| A | 1/2 if inside the allowed values, else code | As a gate check: 1/3 after a code edit to allowed categories. As its own stage: about 6 source files plus tests / ~10 (6 hooks, Checkpoint, Resume kinds, root vs agent exec, health row, digest map) | Spike-sized: `define.py`, the 5.4k-line tool, factory, acceptance, health, digests / ~14 | Every new stage | Low, but only maintainers can author, which defeats the core value |
| B | 1/2 | 1 (+ prompt) / 6: pin, check, trigger, resume template, rounds, token | YAML block + tool + install recipe / ~13: install, setup, token-from-output expression, `depends_on`, per-status stop conditions, closing, carried state, outputs, criteria contract | None per stage, but the DSL grows with each method | Evidence: the DSL can declare triggers that Raidar cannot check generically [INFERENCE]. Owner's "too flexible" risk |
| C | 1/2 | 1/5 | Not expressible: falls back to the A path, or `custom` grows into B | Built-ins stay code | No new risk. Two classes of stage, and built-ins can do what customs cannot |
| D | Requirements, then review | Requirements, then review | Requirements, then review: the reviewer still needs the underlying option's concepts | Generator + underlying option | Unreviewed generated tool code [INFERENCE]. Does not reduce debugging |
| **E** | 1/2 | **3/5**: `stage.yaml`, `prompt.md`, `workflow.yaml` (bundle, pin, gate check, fragment token, stages list) | **bundle + judge block / 9**: manifest, payload/runtime, command on PATH, params, setup→tokens, stop statuses + rounds, carry, gate `before`, outputs + `criteria@1` | The primitives, once. `define.py`'s method logic moves into the tool [INFERENCE] | Low if the protocol fixes the user, timeouts and outputs. Bundle files must enter the workflow digest |
| F | 1/2 | 1 module / 8 | 1 package / ~12 | Plugin loader | **High.** Author code runs host-side, where `OPENROUTER_API_KEY` lives, and can write evidence. It breaks the secret, host-side evidence and identity guarantees |

**Options that weaken a guarantee:** F (secrets, evidence, identity) and B (evidence checking, partly). A, C, D and E weaken none, provided E's protocol below holds.

## 4. Leading option: E with C's discipline

Only three primitives carry method knowledge and stay in Raidar code: the **frame** (the `build` prompt and its reply shape), the **gate** and the **judge**. Every other stage is a bundle on one protocol, and `define` and `verify` become bundles too.

**Raidar-owned primitives (code):**
1. **frame**: the build prompt, with `%%%TASK%%%` and one `%%%<STAGE>%%%` slot per enabled stage, each exactly once (today's rule, `definition.py:263-273`). It has an optional fixed reply shape, `acceptance` = `{account, ac_tests, adrs}`.
2. **install**: copies the payload and pip pins root-owned to `/opt/raidar/stages/<name>/`, uses the pack's pinned interpreter with no network, puts commands on PATH and records a digest of the installed tree.
3. **context**: a root-owned `context.json` holding the task, params, round and carry.
4. **setup call**: runs before the agent starts, as the agent's user. Its JSON gives `tokens` for the stage's fragment.
5. **gate**: one pre-commit hook plus the seal when the agent stops. Stages contribute `gate:` checks; `before: true` makes a check a prerequisite. The gate bounds fix rounds. The `tests` check kind, which records per-test outcomes, is Raidar's.
6. **on-stop call**: runs as the agent's user after each turn, after the agent's leftover processes are killed. Its JSON is `{status: done|resume|closed, message, tokens, carry}`. Raidar bounds the rounds. On `closed`, or when the rounds run out, Raidar writes the root-owned closed flag, and the stage's `before` checks then fail.
7. **outputs**: declared files are copied host-side after each call and at close. They are required when the stage ends `done`. One generic health row replaces W8 and W9.
8. **judge**: runs host-side and holds the secret. It reads a `criteria@1` output, the frame's `ac_tests` and the gate's test outcomes.

For every call, Raidar records the argv, timeout, exit code and stdout digest host-side.

**The author owns:** fragments, skills, tool code and its rules and messages, params, and which checks run.

`workflow.yaml` (define-build-verify). Keys are Raidar's; values are the author's:
```yaml
schema: 2
name: define-build-verify
stages: [define, verify]            # bundles in ./stages/<name>/; order = on-stop order
build:                              # PRIMITIVE frame
  prompt: |-                        # holds %%%DEFINE%%% %%%VERIFY%%% %%%TASK%%%, once each
    ...
  reply: acceptance                 # PRIMITIVE reply shape {account, ac_tests, adrs}
gate: { max_fix_rounds: 2, rules_file: AGENTS.md }       # PRIMITIVE
judge:                              # PRIMITIVE, host-side; the key never enters the container
  criteria: define.criteria         # an output of the define bundle, schema criteria@1
  model: typesafe/jev-1.13-20260917
  closed_at: 0.8
  open_at: 0.3
  max_passes: 3
  rework: |-                        # %%%PUSHBACK%%% %%%GATE%%%
    ...
```
`stages/verify/stage.yaml`. `verify` stops being a stage type and becomes the gate's Python checks:
```yaml
schema: 1
pins: [ruff==0.8.6, mypy==2.3.1, pytest==9.1.1]
params: { test_timeout: 60 }        # fills %%%TEST_TIMEOUT%%% in prompt.md (today's verify prompt)
gate:
  - { name: lint,   run: ruff check }
  - { name: format, run: ruff format --check }
  - { name: types,  run: mypy --strict }
  - { name: tests,  kind: pytest }  # PRIMITIVE check kind: per-test outcomes for the judge
skills: [test-per-aspect, pytest-templates]
```
`stages/define/stage.yaml`. The tool (5.4k lines) moves out of `orchestrator/src` into `tool/`:
```yaml
schema: 1
runtime: python==3.12.14
payload: tool/
commands: { define: python -I -B -m define_tool }          # what the agent calls
params: { unit_rules: 2, max_refinement_rounds: 2 }
setup: python -I -B -m define_tool.stage setup              # -> {"tokens": {"UNITS": "..."}}
on_stop:
  run: python -I -B -m define_tool.stage inspect            # -> done | resume(+carry answers) | closed
  max_rounds: 3
gate:
  - { name: sealed, run: python -I -B -m define_tool.stage sealed, before: true, watch: /app/.raidar/define }
outputs:
  criteria:   { path: /app/.raidar/define/criteria.json, schema: criteria@1 }
  definition: { path: /app/.raidar/feature-definition.md }
  state:      { path: /app/.raidar/define/state.json }
skills: [feature-definition, aspect-writing]
```
How define's current Python maps across:
- The host-side units import (`define.py:167`) becomes `setup`.
- The accept-every-proposal round (`:239-255`) becomes `inspect` returning `resume` with the new units and answers in `carry`.
- The closed flag (`:59-77`) becomes Raidar's generic `closed` behaviour.
- `acceptance.py` stops importing `featuredef` and reads `criteria@1`.

`stages/docs-check/stage.yaml` (scenario ii). The codespell pin is illustrative [INFERENCE]:
```yaml
schema: 1
pins: [codespell==2.3.0]
gate:
  - { name: docs-spelling, run: codespell docs README.md }
```
The author also writes a one-paragraph `prompt.md`, adds `docs-check` to `stages:` and places `%%%DOCS_CHECK%%%` in the frame. A refusal resumes through the gate's existing fix turn, so the author writes no resume template.

## 5. Guarantees under E

| Guarantee | How E holds it | Remaining risk |
|---|---|---|
| No agent code as root | Root only copies author files. Every call runs as the agent's user, as the gate does today (`verify_gate.py:469-470`). Runtime defaults to `-I`. | None new. Author code is trusted but never runs as root over agent-written files. |
| Evidence host-side; missing evidence invalidates | Raidar writes the call log and copies outputs. A generic row covers declared outputs. Gate rows W3–W7 stay. | An author who declares too few outputs gets less evidence, but never false evidence. |
| Isolation and parity | Tools live outside the workspace. Skills follow the agent's placement. | Harness-specific wording in a prompt cannot be caught mechanically. [INFERENCE] |
| Secrets | Container calls never receive the host environment. Only the judge holds the key. | A stage that needs a model in the loop cannot be authored as a bundle; see open question 5. |
| Identity | Allow `stages/<name>/**` (today only `skills/`), so bundle files enter the workflow digest. Primitive-code digests replace per-stage-type digests. | Changing a primitive moves every arm that uses it, which is correct. |
| Separate grading | Not affected. | None. |

## 6. Steelman of the rival, then attack on E

**Steelman of C.** Most users only tweak prompts and checks, and `custom` covers command gates such as docs-check with one file and 5 concepts. The built-in define and gate already have hardened health rows, real runs and co-versioned code. C needs no public protocol to version, no port of 5.4k lines, and no new schema between define and the judge. That is a smaller, more boring first step.

**C fails the brief on (iii).** A new *method* like define, which is exactly what the lab exists to test, still needs Raidar code. It also creates two classes of stage, so a built-in method gets abilities a user's method cannot have. That conflicts with comparing arms fairly. [INFERENCE]

**Attack on E:**
- **Hidden complexity.**
  - The protocol is a public API: a status set, carry semantics, stdout limits, timeouts, and how resumes compete between stage rounds, gate fix rounds and judge passes. Today it is simply "first resume wins".
  - Non-Python tools need runtimes in the pack.
  - `criteria@1` is FXL's feature-definition model under another name, so the judge only serves define-shaped methods.
- **What external authors will get wrong.**
  - Logging to stdout, which breaks the JSON.
  - Keeping state in the graded workspace, outside the watched directory.
  - Setup that is not idempotent, or that needs the network.
  - Assuming root.
  - A `before:` check that can never pass. The agent then cannot commit, burns its budget, and the result looks like an agent failure.
  - Not naming the tool in the prompt, so the agent never uses it.
  - Undeclared outputs.
- **A stage misbehaving mid-trial.**
  - A setup failure or a timeout is an instrument failure, so the trial is invalid.
  - If junk printed by the on-stop call also counted as an instrument failure, the agent could invalidate trials on purpose by corrupting its state, which would bias the results.
  - Answer: follow define's precedent (`define.py:204-210`). Junk gets one repair turn, then `closed`, and counts as an agent outcome with the raw output recorded. Health warns about it per arm.
- **Versioning and identity.**
  - Bundles copied between workflows drift.
  - A protocol `schema` bump needs every bundle migrated (clean cutover).
  - Porting changes the digests of `one-shot-verify` and `define-build-verify`, so jobs from the spike stop pooling with new ones.

**How the recommendation answers these:**
- Dogfood first: if define cannot be written as a bundle, the protocol is wrong.
- Keep the schema strict (`extra=forbid`) with a small, closed status set.
- `make stage-check` runs `setup`, `on_stop` and the `before` checks against a fixture workspace and validates the JSON before any trial.
- Bundles live inside their workflow folder; there is no shared registry yet.
- The judge is honestly labelled a Raidar method primitive.

## 7. Recommendation

**Adopt E with C's discipline.** Three primitives stay in code: frame + reply, gate + `pytest` kind, and judge. Every other stage, `define` and `verify` included, is a bundle on one protocol.

- **Not B:** method branching lives in the author's tool, not in a DSL, so the manifest stays at about 10 keys.
- **Not C:** it leaves define-like methods impossible to author.
- **D later:** a thin skill over a stable E.

The owner's "pointer" intuition holds, with one correction. The pointer must also declare its **gate contribution, stop protocol and outputs**. Those are what Raidar needs to keep evidence and identity honest.

**Build first, in order:**
1. A protocol spec page in the README's Concepts style: manifest keys, call contract, statuses, outputs and `criteria@1`. Write it before any code.
2. A generic `BundleStage(Stage)` driven by the manifest:
   - allow `stages/<name>/**` files;
   - add the generic outputs health row;
   - add primitive-code digests.
3. Gate: accept checks contributed by any bundle, and replace the closed categories with free names plus the reserved `tests` kind.
4. Port `verify` to a bundle, then `define`:
   - move `define_tool` out of `orchestrator/src`;
   - have `acceptance.py` read `criteria@1`.

   Then delete `define.py`, `DefineSettings`/`VerifySettings` and the hand-wiring in `factory.py`.
5. Add `make stage-check STAGE=<path>`.

**Defer:** the generator (D), non-Python runtimes, shared bundle releases, an isolated-session primitive, custom reply schemas, and judge question sets.

**Acceptance tests:**
- **AT1 (authoring experience):** someone outside the team, using only the docs, adds `docs-check` to a copy of `one-shot-verify`. All of these must hold:
  - `make stage-check`, `make quality`, `make releases-check` and the lock pass;
  - in one smoke trial, a planted misspelling makes the gate refuse a commit and a `gate_fix` resume follows;
  - the docs-check record appears in `workflow.json`;
  - `make health` reports no violation;
  - `git diff --stat orchestrator/src` is empty.

  Record how long it took. One hour is a suggested target, not a measured one. [INFERENCE]
- **AT2 (dogfood):** `define-build-verify` rebuilt from bundles passes the same health families on a smoke trial, with `define_tool` gone from `orchestrator/src`.
- **AT3 (faults):** a bundle whose setup exits 1 makes the trial invalid. An `on_stop` that prints junk is repaired once, then closed and recorded.

## 8. Open questions for the owner

1. Is a judge fixed to the `criteria@1` (feature-definition) model acceptable as a Raidar primitive, or must model judges be authorable? Authorable judges would need a host-side judge protocol, and with it a secret boundary.
2. When the on-stop call fails, is that an agent outcome (define's precedent, my recommendation) or an instrument failure?
3. Should bundles stay per-workflow copies (my recommendation), or become shared `lab/stages/<name>@N` releases?
4. Python-only tools for the first version, or runtimes for other languages in the pack now?
5. Is the independent-review isolated session the fourth primitive, and when?
6. Do you accept that porting gives `one-shot-verify` and `define-build-verify` new release digests, so their jobs stop pooling with spike jobs?
