**R1: contract package**
- Decision: create `contract/` at the repository root as its own uv project. Distribution name `raidar-contract`, import name `raidar_contract`, dependencies pydantic and pyyaml only. It holds `digest_json`; the `stage/1` manifest, params, tokens and frame-slot rules; the fixture and `expect.yaml` schema; `stage-tree/1`; workflow schema 2; `criteria/1`; release references and the register parser; and the agent YAML schema with the protocol names and record shapes. The Harbor-typed protocol (`protocol.py` imports Harbor) stays in `orchestrator`. Two couplings must be cut during extraction: `package_definition.py` imports the spike `definition.py` (`NAME_PATTERN`, `TASK_TOKEN`), and `registry.py` imports `REPO_ROOT`. The orchestrator depends on the package through a uv path source. The old modules are deleted, with no re-exports.
- Publishing: git tags `contract-vX.Y.Z`. FXL pins `git+…@contract-vX.Y.Z#subdirectory=contract`. Versioning is SemVer:
  - MAJOR when any contract id changes (`stage/N`, schema N, `criteria/N`, `stage-tree/N`, agent schema N);
  - MINOR for new API that leaves accepted inputs, refusals and digests unchanged;
  - PATCH for bug fixes on the same terms.
  The package exposes a `CONTRACTS` map.
- split-design: new §5.8: "`contract/` (`raidar-contract`) is the single implementation of `stage/1`, schema 2, `criteria/1`, `stage-tree/1`, release references and the agent schema; Raidar and FXL pin it; MAJOR tracks contract ids."
- When: in phase 5, as step 1. Tag `1.0.0` at phase 5 exit; FXL may pin `rc` tags before then. Confidence: high (location, name); med (timing).

**R2: port to schema 2**
- Decision: confirmed, and wider than asked. `one-shot` also moves to schema 2 (no stages, prompt byte-identical), and the schema-1 loader is deleted, as a clean cutover. `docs-check` becomes the real package `raidar/docs-check`. `stage-new FROM=<ns>/<name>` copies any in-tree package and defaults to `raidar/docs-check`; `cli/stage_template.py` is deleted.
- Schema 2 currently has only `build.prompt`, `stages` and `gate.max_fix_rounds`, and the package runtime has no judge or reply path. It gains `build.reply` and `judge` (criteria, model, `closed_at`, `open_at`, `max_passes`, rework).
- Where today's settings go: gate pins go to the `raidar/verify` tool lock; the 60 s test timeout becomes the `test_timeout` param; 3 passes becomes `judge.max_passes`. The 3 retries (D3), the question set and the 60 s judge timeout become `jev-judge/1` constants plus the `request.judge` limit. The unit-rules version becomes a `raidar/define` param.
- split-design: §5.7: "All shipped workflows are schema 2, `one-shot` with no stages; schema 1 is refused." Add to the §9 phase 5 row: "schema-2 `reply`/`judge`; `one-shot`; `docs-check` as a package". When: phase 5. Confidence: high.

**R3: homes for FXL's gate enforcement**
- Changed-line coverage and hollow-test refusal: a separate package, `raidar/test-evidence`, ported from FXL `ac5db0f` (`guardrails/changed_lines.py`, `hollow_tests.py`), with checks `changed-lines` and `hollow-tests`, listed after `raidar/verify`. `raidar/verify` keeps spike parity (ruff, ruff format, mypy, pytest) as the control.
  - Why: M-001's proof needs arms that differ only in this lever. Putting it inside `raidar/verify` would move the baseline. FXL has decided not to port it.
  - What it needs: a root-owned `base` field in the `gate/1` context. Today `base` appears only in gate records, not in `RAIDAR_STAGE_CONTEXT`. The package runs its own coverage pytest, so no new primitive.
  - M-001: `home` changes to `raidar/test-evidence@1`. Its proof plan must be restated: `raidar/verify` has no 85% minimum, so the contrast is with a gate that has no coverage rule at all.
- `LIST.item-untested`: FXL forks only. Gate policy 2 is unmeasured, its fault is FXL's FCR-007, and it belongs to iteration 2.
- Golden and example probes: FXL forks only.
- split-design: §5.7: "`raidar/test-evidence` carries changed-line coverage and hollow-test refusal (M-001's lever), never `raidar/verify`; the list rule and probes are `fxl/*`." §9 phase 10: "measure imported `fxl/*` probe and list packages and `raidar/test-evidence`; no new `raidar/verify` or `raidar/define` release."
- When: name the homes before phase 5; add `base` in phase 5; build the package in phase 10. Confidence: med.

**R4: ownership**
- Decision: adopt, made precise.
  - `raidar/*` stages and workflows are the baselines and controls. Raidar changes them only for a contract cutover, a `tool_bug` disposition or a port-parity fix, each as a new release.
  - Methods are developed in FXL as `fxl/*`.
  - Raidar touches `lab/stages/fxl/` and `lab/workflows/fxl/` only by import. FXL never edits imported namespaces (D9).
  - User namespaces stay open.
  - Primitives, judges, scorers, the fit rule and the frame stay Raidar-only.
  - A winning `fxl/*` method becomes a baseline only by two votes, under a new name, and the old release stays.
- split-design: in §5.7, replace "`raidar/verify` is maintained…" with "`raidar/*` are Raidar's baselines and controls, changed only to follow the contract or fix a recorded `tool_bug`; methods are developed in FXL as `fxl/*` and imported byte-identical for measurement." This resolves C1 and C2.
- When: before phase 5 (doc change). Confidence: high.

**R5: importing**
- `make stage-import SRC=<dir> NAME=<ns>/<name> SHA256=<stage-tree/1>`:
  - reads bytes only and applies the existing symlink, special-file and traversal refusals;
  - refuses the `raidar/` namespace, a digest mismatch, and different bytes under an existing release;
  - copies the folder, fixtures included, and prints the release line;
  - records `{sha256, source: <repo>@<commit>:<path>}` in `lab/imports.yaml`, which `releases-check` verifies.
  `stage-check` on `mbp` stays required.
- `make workflow-import`: the same, keyed on the definition digest. It refuses any `use:` that is not registered with an identical digest, so packages are imported first.
- Namespace rule: workflow references require `ns/name@N`, with folders at `lab/workflows/<ns>/<name>/`.
  - Shipped workflows start new series, `raidar/one-shot@1` and so on. The bare entries stay as frozen history and never resolve.
  - Arms that name no workflow run `raidar/one-shot`. Agents keep `name@N`.
  - This reverses the workflow half of the 2026-10-28 namespace vote, so it needs its own vote. `RELEASE_PATTERN` already accepts an optional namespace.
- split-design: §2 Release and §5.6: "Workflow references require a namespace, as stage references do; `stage-import`/`workflow-import` copy an exported folder verified against its digest, with provenance in `lab/imports.yaml`."
- When: phase 5. Confidence: med-high.

**R6: SDKs or CLIs**
- Decision: measured runs stay on CLIs in containers. [INFERENCE] The SDKs drive the same harness binaries. The protocol is already driver-neutral (`isolate`, `process_facts`, `accounting`, `last_message`, `session_store`, `session_audit`), so an SDK driver can arrive later as a new adapter and agent release, run inside the container, never on the host.
- Evidence an SDK adapter needs before it counts:
  - the same harness pin;
  - a full P8 isolation proof;
  - per-turn accounting that reconciles;
  - resume parity;
  - health parity on smokes;
  - then a `varies` comparison of CLI against SDK that reports `equivalent` or `inconclusive`.
- C4: an FXL smoke proves mechanics only. Every package needs a Raidar smoke before a result counts.
- Sign-in: record it as `sign_in: api-key|subscription` in `lab/agents/*.yaml`. It is part of agent identity and is checked by the credential preflight. Subscription sign-in is unverified and is not admitted for measured runs until a preflight smoke passes for each adapter and the user confirms the provider terms allow it.
- split-design: a §4 bullet with the above. When: decide now; any SDK adapter after phase 9. Confidence: med.

**R7: isolated sessions**
- Decision: stays deferred. FXL's motivating method (its M-001, the independent coverage review) was rejected, and fresh-session intake is unmeasured.
- It arrives as `isolated-session/1` with a `stage/2` cutover, because `stage/1`'s manifest keys are closed. It comes after phase 10, and only when a registered Raidar question needs it.
- split-design: §9 Deferred, reworded as above. Confidence: med.

**R8: Jev in two roles**
- `jev-judge/1` is part of the treatment. An arm's primary outcome (hidden tests) can be cited without validating the judge. The judge's verdicts are never cited as evidence of correctness. J health and repeatability (4.96%) are reported.
- The Jev scorers stay readings until they are validated against labels, with a pre-registered agreement threshold and agreement reported separately for arms with and without the judge. Comparing judge arms with non-judge arms needs no material gap between those groups; otherwise a non-Jev scorer model is required.
- Homes: calibration goes in `lab/judge/calibration/`; the `jev-actions` skill goes in `.agents/skills/jev-actions/`. FXL's copy of `jev-judge/1` is for smokes only.
- split-design: §7.1 and the judges line in §5.1. When: move the skill before FXL's phase 1; calibrate in phase 9. Confidence: med.

**R9: FXL's lab history**
- Decision: give FXL's records a namespace rather than renumbering them, so citations to FXL's tag keep working: `FXL-FCR-007`, `FXL-M-002`, `FXL-E-011`, `FXL-Q-009`. This means extending the ID regexes (`records.py:20-21`) and allowing references of the form `factory-x-loops@lab-final-2026-10-31:<path>`.
- Adopted: FCR-002, -003, -004 and -007, M-001 (rejected) and M-002 (adopted), each with its FXL status history verbatim. Raidar changes their status only on Raidar evidence.
- E and Q records are cited by tag and become journal records in phase 8. FXL run data held outside git is handed over as a digested archive before FXL's phase 1, or is marked `gone`.
- Iteration 2 is re-registered as new Raidar experiments (new rationale and lock, TOST margin, `max_exclusion_difference`) on imported `fxl/*` packages after phase 9. FXL's pre-registration is cited as its origin.
- split-design: §8, add the rule. When: the rule and the fault and remedy import before phase 5; journal records in phase 8; iteration 2 in phase 10. Confidence: high (namespace); med (rest).

**R10: agent pairing**
- Decision: adopt. GPT models run on codex and Claude models on claude-code. Comparisons across makers declare `varies: [harness, model]`; `schema.py:279-280` already enforces declaring model with harness. A same-model run on another harness is an explicit `varies: [harness]` robustness check. F5 supports the cost choice, not a claim that harnesses are equivalent.
- D4 does not affect Raidar: it has no opencode adapter. As a result, same-model robustness checks need a new adapter before they can run.
- Claude arms need claude-code's isolation proof raised from `partial` to `full` before measured use.
- split-design: §4: "Each model runs on its maker's harness by default; cross-harness runs are declared `varies` robustness checks." When: before phase 9. Confidence: high.

**C7: governing a `stage/1` bump**
- Decision: `stage/1` freezes at contract `1.0.0`, at phase 5 exit. Nothing is released yet, so phase 5 is the last chance to amend it without a bump.
- After that:
  - A clarification that changes no package's acceptance, behaviour or digest gets a contract-history note and a PATCH.
  - Any change to manifest keys, the call protocol, the context, the digest or the refusals becomes `stage/2` with a MAJOR bump. It needs two votes and an `rc` tag with migration notes before the cutover.
  - Raidar migrates `raidar/*` in one cutover. Old-contract packages are refused by ST1, so imported `fxl/*` packages stop resolving until FXL re-exports them; their past results keep their old identity.
  - No two contracts are supported at once.
- split-design: extend the bump line in §5.6. Confidence: med.

Phase 5 plan:
Before phase 5:
- split-design amendments, adopted by two votes: the R3 homes, R4, the R5 namespace reversal, R6–R10 and C7.
- The R9 ledger rule.
- `mbp` online, with the phase 4/4b Docker exits (AT3, security-gate, fixtures, revalidation) closed before any smoke step.
- User approval for real smokes.

Throughout: the fixed build frame stays, judges, scorers and the fit rule stay Raidar-owned, and the frozen release rules hold (`make release-archive` for every superseded release; the register is append-only).

1. **Extract `contract/` (`raidar-contract`).** Check: import-linter shows no `raidar` or Harbor imports; a clean venv needs only pydantic and pyyaml; every existing release digest is unchanged (`releases-check` asks for no new lines); `make quality` at ≥95%.
2. **Finish the contract before the freeze.** Schema 2 gains `build.reply` and `judge`; workflow references get namespaces; `gate/1` context gains `base`; the judge constants move into `jev-judge/1`. Check: refusal tests (bare workflow reference; judge with no criteria producer, ST3; missing reply); AT4.
3. **`raidar/verify@1` (ruff 0.8.6, mypy 2.3.1, pytest 9.1.1, `test_timeout` 60, `instructions.md` replaces `rules_file`) and `raidar/one-shot-verify`.** Check: `stage-check TARGET=mbp`; per-check verdicts on the same trees match the spike gate; a draft smoke.
4. **`raidar/docs-check@1` and `stage-new FROM=<ns>/<name>`; delete `stage_template.py`.** Check: AT1.
5. **`raidar/define@1` (`define_tool` moved under `tool/src`, its unit tests kept under `make quality`, the stop call gets a timeout) and `raidar/define-build-verify` with reply and judge on `criteria/1`.** Check: done, closed and resume fixtures; parity fixtures for clarification, repair, seal, endings and judge rules; nothing named `define_tool` left in `orchestrator/src`.
6. **`raidar/one-shot` on schema 2.** Check: prompt bytes identical; smoke.
7. **`verification-effort@1` and pre-fix grading from `gate/1` records; delete `gate-effort`, `grading/prefix` and their consumers.** Check: read from a smoke; `not_applicable` without `gate/1`.
8. **Key health rows W3–W10 by primitive id instead of stage type (the generic families stay in phase 6).** Check: no violation on the AT2 smoke.
9. **Delete the spike: `StageType`, the `Define`/`Verify` classes, `verify.py`, `define.py`, `definition.py` and the schema-1 loader, `factory.py`, `stages.py`, `stage_code`.** Check: `structure-check`; no `schema: 1` left.
10. **Release all six `raidar/*` packages and workflows; relock every experiment.** Check: `releases-check`, `experiment-locks-check`.
11. **`stage-import`, `workflow-import` and `lab/imports.yaml`.** Check: an `fxl/docs-check` fork is imported, released, locked without `DRAFT` and smoked; a changed byte is refused.
12. **Exit.** AT1, AT2 (with `verification-effort@1`), AT3, AT4; `make security-gate` and `make perf-gate` on `mbp`; `make quality`. Tag `contract-v1.0.0` and add the `stage/1` freeze to the contract history.
