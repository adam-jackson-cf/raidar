Q1: Separate standard? Vote **(c)**, medium-high confidence. Make it a separate standard by content and by version identity now. Keep it in this repository and under Raidar's governance. Promote it to its own repository, option (a), only when one of the conditions under Governance occurs.
- **Why not (a) now.**
  - The call protocol is written as Raidar's validity guarantees: the attribution table, the caps, the seal and the closed flag (`lab/stages/README.md`, Call protocol and Canary sections). An outside owner could change them without the health model behind them.
  - One person owns all three projects, and FXL is the only consumer outside Raidar.
  - The user decided in the pivot (§5) that "Raidar defines the package schema shape".
  - The phase-5 freeze is pending.
- **Why not plain (b).** The current distribution mixes the standard with Raidar's lab policy, and it is missing part of the standard:
  - **Lab policy inside it:**
    - `agent.py:49-54`: the `AgentName` enum is Harbor's ids (`codex`, `claude-code`), plus the Isolation and Capabilities models;
    - `agent_records.py`: Credentials and StatePath, with shell expressions like `$HOME_DIR`;
    - `releases.py`: the `lab/releases.yaml` register, including its agents kind;
    - `package/registry.py:44-46`: `stage_folder` resolves to `lab/stages/…`.
  - **Missing from it:**
    - The stop result `{status,message,tokens,carry}` and the call context (`task, params, round, carry, state, outputs`, plus `base`) exist only in `orchestrator/src/raidar/subject/primitive/stop.py:35,91` and `site.py:90-99`.
    - No shared conformance cases ship: `contract/` holds only `src/`, `pyproject.toml` and `releases/`.
    - So FXL's phase-4 runtime would rebuild the call protocol from prose. That is the second implementation R1 was meant to prevent.
- **What belongs in the standard:**
  - the `stage/1` manifest and package folder layout;
  - params, tokens and the `frame/1` slot rules;
  - the call contracts of `setup/1`, `gate/1` (check kinds including `pytest`, the context with `base`), `stop/1` (the result shape, round bounds and closed semantics) and `outputs/1`;
  - the env name `RAIDAR_STAGE_CONTEXT`;
  - the caps (300 s timeout, 5 rounds, 1 MiB stdout, state caps);
  - the `install/1` input rules (wheels only, hashed, no build backend);
  - fixtures and the `expect.yaml` schema;
  - `stage-tree/1` and the workflow fold `workflow-packages/1`;
  - workflow schema 2, including the judge configuration shape;
  - `criteria/1` and the `acceptance-reply/1` shape;
  - the `ns/name@N` reference grammar;
  - conformance cases that both runtimes must parse, refuse and digest identically.
- **What stays out:**
  - the implementations of the primitives: runner, seal, canary execution, liveness, the fit rule and `jev-judge/1` (its question set, retries and timeout);
  - health, attribution and dispositions;
  - the agent schema and `NativeAgent` protocol (`protocol.py` imports Harbor; the agent schema is Raidar's measurement policy);
  - the release register and its append-only rule;
  - `lab/` paths, `lab/imports.yaml` provenance, lock identity (`packages`, `primitive_code`) and pooling.
- **Governance.**
  - C7 is unchanged: Raidar approves, FXL supplies a consumer-impact review, and a MAJOR change needs two votes plus an `rc` with migration notes.
  - A MAJOR change to the standard is a `stage/N` cutover in Raidar.
  - R4 is separate: it decides who edits which package namespace (`raidar/*` versus `fxl/*`), not who changes the standard. Leave it as it is.
  - Promotion to (a) takes two votes and any one of: a third runtime or outside authors; FXL needing a change Raidar refuses; factory-x running packages on a runtime neither Raidar nor FXL owns.
  - Even under (a), the primitive implementations and validity semantics stay in Raidar.
- **Timing.**
  - Do not reopen the freeze: `stage/1` freezes at `1.0.0` at phase 5 exit, as planned.
  - Then release the stop-result and context models, plus the conformance cases, as `1.1.0`. That is MINOR: inputs, refusals and digests are unchanged.
  - Then split the distribution, standard versus lab schemas, before FXL phase 4 (its runtime). The split changes no contract id and no digest, so no `stage/2` is needed.
  - [INFERENCE] Removing modules from the distribution is breaking for importers. §5.8's SemVer rules don't name this case; I'd treat it as a distribution MAJOR.
- **Phase 5B impact:** none. Only keep any further lab-specific schema out of `contract/` before the tag.

Q2: Comparing interfaces
- **Concepts.**
  - **Don't make "interface" a Raidar term.** FXL's pivot document gives it two incompatible meanings: §2 says the CLI is an interface that bundles a harness; §5 says "the package is the interface layer". In the talk's sense, the interface is already part of the agent: its adapter (driver) and tool pin. An SDK driver would arrive as a new agent release (R6).
  - **Agent:** harness pin + model + reasoning, driven through a pinned adapter.
  - **Workflow:** the layer above. It is a definition and does nothing without a runtime.
  - **System under test (SUT): make it first-class.** It is what an arm denotes, and it comes in two kinds:
    - **Composed arm (white-box):** agent + workflow, run by Raidar's runtime and primitives. This is every arm today.
    - **Opaque arm (black-box):** a pinned external system that receives the task text and workspace inside the trial container and leaves a workspace. It brings its own harness, driver, runtime and workflow.
  - **An opaque system is not an agent.** It can't honour `NativeAgent` (`protocol.py:47-70`: `session_audit`, per-turn `accounting`, resume).
  - Only composed SUTs break down into factors.
- **"Claude Code CLI" alone** is the composed arm `claude-code@N` + `raidar/one-shot@2`: the agent as shipped. An FXL workflow can't run without an agent, so "CLI versus workflow" means the same agent without and with the workflow.

| Guarantee | White-box (composed) | Black-box (opaque) |
|---|---|---|
| Same task text, sandbox, network allowlist, hidden grading, grade what was left | yes | yes, if it runs inside the container and never on the host |
| Equal budget | per-checkpoint budget plus the fit rule | wall clock only |
| P8, the run saw only its workflow's skills | measured | unmeasured |
| Gate not bypassed, calls recorded, canary attribution (W, G, S and J families) | measured | not applicable; must be reported as such, never as `pass` |
| Accounting; the model actually used | per-turn, reconciled; pinned by the adapter | unmeasured unless an outside meter is added [INFERENCE]; declared and unverified |
| Identity and pooling | `packages` + `primitive_code` | bundle digest only; never pools with composed arms |
| What a result can claim | effect of the varied factor | system-level comparison on the primary outcome; all factors confounded |

- **Vote: both kinds, under these names.** Composed arms are the main instrument. Opaque arms are deferred until a registered question needs them, for example evaluating factory-x as a product.
- **The example experiment (E1, the one to run):**
```yaml
agents: [{id: cc, release: claude-code@N, model: <claude-model>, reasoning: <r>}]
arms:
  - {id: cli, agent: cc, workflow: raidar/one-shot@2}
  - {id: fxl, agent: cc, workflow: fxl/<wf>@N}   # stage-import, then workflow-import
varies: []   # the workflow is the arm's treatment; harness, model and reasoning fixed
analysis: {baseline_arm: cli, max_exclusion_difference: <d>, ...}   # plus a pre-registered TOST margin δ
```
  - **Fixed:** model, harness pin, CLI adapter, Raidar's container runtime and primitives, the per-checkpoint budget, problems and grader.
  - **Varies:** the workflow (packages, build prompt, judge).
  - **Can claim:** the FXL method's effect on reference-solvable SCB success for this agent on Raidar's runtime: improved, worse, equivalent or inconclusive within δ.
  - **Cannot claim:**
    - performance under FXL's host and SDK runtime, or in factory-x;
    - anything about other models or harnesses;
    - that the CLI loses as an interface (both arms use it);
    - the judge's verdicts as evidence of correctness (R8).
  - **Before it counts:**
    - the claude-code isolation proof raised from `partial` to `full`;
    - a Raidar `stage-check` and smoke of the imported packages (C4);
    - the phase-9 A/A and degraded controls;
    - a budget sized for the heavier arm, with deadline endings reported per arm.
- **E2, runtime conformance (later):** the same `fxl/<wf>` digest run as a composed arm versus inside FXL's own runner as an opaque arm, same model. The difference measures runtime plus driver, which is the "two runtimes" risk (C4). It tests whether FXL smokes predict Raidar results.
- **E3, "CLI versus FXL system" fully black-box:** every factor varies, so the result is a product comparison only.
- **Implementation consequences.**
  - **Phase 5:** no code change. Two-vote doc edits: §2 gains the SUT, composed-arm and opaque-arm terms, with "interface" mapped to the adapter; §9 Deferred gains opaque arms.
  - **Phase 6:** opaque arms have no locked primitives, so health applicability already makes W, G, S and J inapplicable. The report needs an explicit "not applicable" label so these never read as `pass`.
  - **Phase 7:** R6's CLI-versus-SDK comparison can't be declared today.
    - `Variation` is only `harness | model | reasoning` (`schema.py:54`), and any `varies` must include `harness` (`schema.py:280-281`).
    - Add an `interface` factor (same harness pin, different adapter).
  - **New phase after 9/10:** the opaque arm kind, built only when a registered question needs it.
    - A system release with its bundle digest and declared models.
    - It runs in the trial container as the agent's user [INFERENCE: as a Harbor `BaseAgent`].
    - Admission refuses mixing kinds unless `varies: [system]`.
    - Analysis labels results "system-level".
    - Results never pool across kinds.
