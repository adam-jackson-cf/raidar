Read-only vote. Verified Raidar `531c0e4` and FXL `ac5db0f`; no files edited or containers run. All amendments below are proposed.

**R1 — Extract one shared contract distribution.**
Decision: `contract/`, distribution `raidar-contract`, import `raidar_contract`; immutable wheel/sdist under `contract/vX.Y.Z` releases, exact-version and hash pins. Raidar consumes the same distribution; no copied validators.
Amend §5.2: “`raidar-contract` owns manifest/params/tokens, fixture expectations, workflow schema 2, criteria, canonical digests, release references and transport-neutral agent schemas/protocols; runtime implementations remain outside it.”
Include credential/state methods alongside the six named methods; Harbor-specific types stay in Raidar adapters. SemVer governs the distribution; `stage/1` and other contract identifiers retain independent versions.
Timing: **in phase 5**, first implementation step, finalized through dogfooding before FXL consumes it. Confidence: **high**.

**R2 — Finish the entire shipped-default cutover.**
Decision: all three workflows become schema 2, including build-only `one-shot` with no packages. Ship real `raidar/verify`, `raidar/define` and `raidar/docs-check`; `stage-new FROM=` copies the latter, not generated source text.
Amend §§5.2, 5.7, 9: “All shipped workflows use schema 2 and released packages; `one-shot` remains the ungated arm default and `one-shot-verify` the authoring template.”
Complete schema-2 structured replies and judge configuration: current `package_definition.py:47–97` supports neither. Delete schema-1 runtime dispatch, not merely the shipped YAML.
Timing: **in phase 5**. Confidence: **high**.

**R3 — Preserve established enforcement; separate experimental methods.**
Decision: changed-line coverage and hollow-test refusal belong inside `raidar/verify`, including their necessary coverage/base plumbing; no fixed total-coverage minimum. Keep `LIST.item-untested`, golden probes and example probes in **FXL forks**, not baseline defaults or new Raidar packages.
Evidence: FXL `guardrails/list_items.py:1–29` explicitly calls policy 2 unmeasured and records false refusals; Raidar M-001 remains **proposed**, not demonstrated effective.
Amend §§5.2, 5.7, 9: “Baseline verify includes changed-line and hollow-test checks; phase 10 measures FXL list-rule and golden/example-probe variants without changing baseline controls.”
Timing: baseline checks **in phase 5**; experimental checks/probes **later, phase 10**. Pin M-001’s successor release without upgrading its evidence status. Confidence: **high**.

**R4 — Raidar owns controls; FXL develops methods.**
Decision: Raidar maintainers alone change `raidar/*`; FXL authors change `fxl/*`. Imported namespaces are read-only to consumers; imports preserve producer bytes. Raidar reviews/adopts imports but does not silently patch them.
Amend §5.7: “Raidar maintains baseline/control packages for contract compatibility, security and correctness; method experiments are FXL-owned forks imported for measurement, with every behavioral correction separately released.”
This permits necessary baseline bug fixes without turning “contract maintenance only” into a prohibition on fixing broken controls.
Timing: ownership decision **before phase 5**, enforced **in phase 5**. Confidence: **high**.

**R5 — Add local imports and namespace workflows now.**
Decision: `make stage-import SOURCE=<folder> DIGEST=<digest>` and equivalent `workflow-import`; preserve namespace, validate safe paths and identity, refuse overwrites. Import stages first; verify workflow identity against the exact released dependency closure.
Amend §§2, 5.4, 5.6: “Stages and workflows use `ns/name@N`, stored at `lab/{stages,workflows}/<ns>/<name>/`; local imports verify supplied digests but neither release nor authorize packages.”
Migrate all active workflow references, defaults and locks without aliases; retain frozen historical entries. Fixtures need independent checking because `stage-tree/1` excludes them. Remote transports remain deferred.
Timing: **in phase 5**. Confidence: **high**.

**R6 — Keep Raidar’s container CLIs; do not infer SDK equivalence.**
Decision: FXL’s SDK move stands. Raidar retains pinned CLI adapters pending evidence for SDK isolation, resume, accounting, audit completeness, cancellation/reaping, auth and overhead.
Amend §4: “Agent protocol semantics are driver-neutral; FXL SDK smoke and Raidar container execution require separate conformance evidence, and every imported release needs a Raidar smoke before measurement.”
Record subscription support as allowed/unsupported/unverified per provider and pinned adapter in agent documentation and credential-preflight evidence: dated provider-policy reference, redacted sign-in result and authentication mode; never credentials. Neither SDK’s subscription support is established here.
Timing: decision **before phase 5**; import smoke **in phase 5**; SDK assessment **later, phase 7**, with adoption conditional on proof. Confidence: **high** on retaining CLIs.

**R7 — Keep isolated sessions out of this cutover.**
Decision: useful, but not required for shipped defaults; FXL must not invent an incompatible substitute.
Amend §§5.1, 9: “Phase 11 adds a Raidar-owned isolated-session primitive only after explicit context, workspace, budget, accounting, provenance and failure semantics are specified and proved.”
Its review package must not acquire hidden grading access or extra unaccounted time; primary-only packages remain the phase-5 contract.
Timing: **later, new phase 11**. Confidence: **medium**.

**R8 — Require calibration, not merely another model.**
Decision: the arm judge is a treatment component, not independent evidence of treatment success. Coverage scorers remain uncalibrated readings until validated on held-out, independently adjudicated labels, blind to arm and judge verdict.
Amend §§5.1, 7.1–7.2: “Raidar owns judge calibration and `jev-actions`; before quality claims, validate each judge role against independent labels and report shared-error risk; model separation alone is insufficient.”
Keep prompts, calibration records and skill ownership in Raidar, outside method packages; FXL may invoke the specified judge interface for mechanical smoke, not produce evaluation claims.
Timing: boundary **in phase 5**; calibration **later, phase 9**, before citation. Confidence: **high**.

**R9 — Preserve provenance with namespaced historical IDs.**
Decision: adopt `FXL-Q-004…009`, `FXL-E-002…011`, `FXL-FCR-002/003/004/007`, `FXL-M-001/002`, plus cited runs, occurrences and status histories. Preserve rejected M-001 and adopted M-002 as historical statuses, not Raidar efficacy findings.
Amend §8: “Import FXL history with `FXL-` identifiers and original IDs, commit, digests and availability; never renumber or reinterpret historical results as Raidar trials.”
Iteration 2 gets new Raidar registrations and locks referencing FXL-E-011/Q-009; preserve its unrun status. Archive against verified `ac5db0f`; verify the proposed final tag rather than assuming it exists.
Timing: handover/preservation rule **before phase 5** and before FXL deletion; ledger migration **phase 8**; iteration-2 registration/execution **phase 10**. Confidence: **high**.

**R10 — Default to maker pairing, without claiming harness equivalence.**
Decision: GPT/Codex and Claude/Claude Code are default agent pairings; cross-harness robustness experiments explicitly declare `varies: [harness]` and hold model/settings fixed. Cross-maker comparisons vary both model and harness, not “model only.”
Amend §§4, 7.2: “Use maker-paired agents by default; identify every changed factor, and treat historical harness comparisons as descriptive unless their design supports inference.”
D4 retires opencode in FXL; Raidar §4 already retains only Codex and Claude adapters, so no additional Raidar retirement follows. F5’s narrow cohorts do not establish universal equivalence.
Timing: policy **before phase 5**; robustness assessment **later, phase 9**. Confidence: **medium**.

**C7 — Govern contract bumps centrally and cut over active forks together.**
Decision: Raidar owns approval; FXL supplies consumer-impact review. Breaking changes require a new contract identifier and distribution major version, migration instructions and shared conformance cases.
Amend §5.6: “A contract bump migrates all active in-tree and imported FXL forks, re-releases dependent workflows and relocks together; incompatible packages fail explicitly, with no aliases or dual-contract runtime.”
Historical frozen bytes and archived runtimes remain untouched. Additive changes still require compatibility evidence; fixtures alone cannot establish agent-runtime equivalence.
Timing: governance **before phase 5**, conformance machinery **in phase 5**. Confidence: **high**.

**Phase 5 plan:**
1. **Prerequisites:** adopt these decisions and record namespace/contract governance plus FXL-history preservation. Outstanding phase-4/4b Docker security, fixture/replay, performance and revalidation evidence must close before phase-5 exit; the design permits port implementation meanwhile.
2. Extract `raidar-contract`; remove Harbor/repository-layout dependencies from portable schemas. **Accept:** isolated installation imports without Harbor; shared vectors produce identical parsing, refusal and digest results; Raidar uses that implementation.
3. Complete schema 2, namespaced workflows and local imports. **Accept:** structured reply/judge dependencies validate; malformed references, digest mismatches and collisions refuse; imported `fxl/*` stage/workflow releases lock without engine edits.
4. Port verify, then define, then docs-check. Move tools, language rules, prompts, skills and method parameters into packages; retain judge question sets and execution policy in Raidar. **Accept:** offline fixtures prove pass/refusal, criteria output, stop/repair behavior and changed-line/hollow-test enforcement.
5. Migrate all three workflows and authoring defaults; preserve fixed build, Raidar-owned judges/scorers, equal deadlines and the existing fit rule. **Accept:** build-only stays ungated; omitted packages leave no trace; docs-check is copied from real package bytes.
6. Connect `acceptance-reply/1` and `jev-judge/1` to `criteria/1`; replace gate-effort and legacy prefix grading with `verification-effort@1` and pre-fix snapshots, including analysis/review consumers. **Accept:** real smoke records expose effort and grade the actual pre-fix tree.
7. Delete spike classes, closed stage/settings models, factory wiring, inventories, schema-1 dispatch and code-rendered templates. **Accept:** all active callers use packages/primitives; `define_tool` is absent from `orchestrator/src`; no compatibility shims.
8. Publish new releases and regenerate every affected lock together. **Accept:** frozen numbers/history unchanged, latest-only resolution preserved, archived bytes retained, variants separately named, package/primitive identities never pool through overrides; AT4 stale-lock checks pass.
9. Prove AT1–AT4 plus imported-FXL smoke on Raidar: misspelling refusal/fix, define/judge execution, failure attribution, intact evidence and unchanged graded-workspace rules. Run `env -u AGENT make quality`, release/structure checks and `make security-gate` on `mbp`; real-model runs require authorization. **Exit:** recorded passing evidence, not fixtures alone; only then unblock FXL package work.
