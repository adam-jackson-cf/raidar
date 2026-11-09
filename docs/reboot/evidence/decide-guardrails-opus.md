# Vote: Full guardrails — Claude Opus 5.5

## Q1. Identity-bearing bytes

**Decision:** Sort findings by what their repair does to a digest, not by folder. Do not make a lint-only identity batch, a lint-only revalidation or a lint-only `@N+1` release.
- **(a) Released package folders** (`lab/stages/raidar/{define,verify,docs-check}`): exclude them from Ruff format/lint rewrites and from strict mypy. Bind the exclusion to the folder's registered `stage-tree/1` digest in `lab/releases.yaml`. The gate fails if the folder's digest differs from the recorded value while the exclusion is still listed. The next real release of that package must comply fully and must drop the exclusion.
- **(b) Grading closure:**
  - **Docstring and formatting repairs are safe to do now.** `python-ast/1` removes every string-only statement (`contract/source_digest.py:1-15,34-51`). So the D-family fixes, 58 of the 78 findings, and pure formatting do not change the digest. Repair them now, with recorded before and after `grading_execution_digest()` values.
  - **Two runtime payload files hash raw bytes.** `grading/runtime/raidar_oracle_plugin.py` and `sitecustomize.py` are hashed with sha256 (`grading/identity.py:174-177`). Freeze them the same way as (a).
  - **Code-changing findings go into a ratchet.** That is about 21 Ruff findings (I001 ×6, PLR0913 ×11, PLR0917, PLR0914, C409, N818) and 20 strict-mypy errors, because annotation edits change the AST. The ratchet allows no new violations. It is cleared in the next identity batch that is already scheduled for this closure for another reason, under that batch's single revalidation.
- **(c) Primitive modules:** same rule as (b). Repair D findings and formatting now; my recount over every contract × adapter gives about 127 D findings. Ratchet about 39 code-changing Ruff findings and the strict-mypy errors. Clear them in the next primitive identity change, which already carries a relock and a `security-gate`.
- **(d) `contract/src`:** ratchet all 20 Ruff findings and 20 mypy errors against the released 2.0.0. Apply the repairs in the next contract release. Do not cut a release only for lint, and do not edit source that 2.0.0 was built from without a version bump.
- **(e) Two identity closures the brief misses.** Both are `python-ast/1` and get the (b) treatment:
  - the lock's `raidar_spec` closure (`design/experiment/lock.py:149-178`): 16 D and 10 code-changing findings; changing it stales every lock;
  - scorer `implementation_digest` (`scoring/framework/registry.py:183`, hashed into Assessment keys by `keys.py:24`): 106 D and 62 code-changing findings, plus 57 mypy errors.

**Reason:** The brief's premise is partly wrong. Docstrings never reach the AST digests, so most of the identity-area backlog can be repaired without touching identity, as long as each unit proves the digests are unchanged. The findings that would change a digest are mostly style or design rules, not defects. Fixing them on their own schedule would cost about 6 h of revalidation on `mbp`, relocks of every experiment, and new stage releases. New releases need new stage-check evidence and break pooling with earlier trials, because `packages` and `primitive_code` are never overridable (§5.4). A ratchet bound to the digest still enforces every rule on every new or changed line. The cleanup then rides identity changes that happen anyway, which keeps to "each identity change gets its own reviewed commit". Tying exclusions to a digest also means they end on their own the moment the bytes change.

## Q2. Catalogue deviations

**Decision:**
- **Line length:** keep 100 and record it as a deviation.
- **Ruff target and mypy version:** keep `py312` and mypy `python_version = "3.12"` with `strict = true`. This is the catalogue value, not a deviation.
- **pydocstyle:** select `D` with `convention = "numpy"` as the catalogue says. Record one deviation: ignore D401 (imperative mood).
- **Preview:** turn preview on for lint (`[lint] preview = true`). Keep the formatter on its stable style (`[format] preview = false`) and record that as a deviation.

**Reason:**
- **Line length.** `RUFF.SETTING.line-length` is conditional ("preserve … or equivalent"). Moving to 88 would rewrite 17 of 43 released-package files and the byte-hashed grading runtime plugin, and it would churn blame across the repository. None of that finds defects.
- **Python version.** For both the Ruff target and mypy, the catalogue requires `target_supported_python_version_required`. Raidar's supported floor is `requires-python >=3.12`. The draft's `py313`/3.13 was a mis-render; 3.13.7 is the validated grading interpreter, not the support floor.
- **Docstrings.** Numpy convention keeps D401. That one rule produces 564 of the 761 D findings, and Ruff cannot fix it automatically. It conflicts with the repo's deliberate descriptive-summary style ("The exact canonical source…"; there are no numpy-style sections anywhere). The remaining ~197 D findings (D205, D209, D202, …) are cheap and mostly fixable automatically, and they stay.
- **Preview.** Lint preview is needed for the catalogue's preview-only rules PLR0917, PLR0914 and PLR0904. Preview formatter styles change between Ruff releases. Each Ruff upgrade would then rewrite bytes in frozen packages and payloads, which defeats the frozen exclusions in Q1.

## Q3. Non-identity backlog (~956 Ruff, ~1,950 mypy)

**Decision:** Use a hybrid.
- **Repair before enabling:** the mechanical, automatically fixable families (I001, D2xx, the handful of B/C4/N findings, formatting), in the same unit that changes the config.
- **Ratchet the rest:** PLR0913/0917/0914 and all strict-mypy errors (including 1,487 in tests). Enable them at once against a tracked baseline that only shrinks, counted per (file, rule code). The checker must:
  - fail when any count goes up, or when a new file or code appears;
  - fail when a baseline entry is higher than the actual count, so every fix lowers the baseline in the same PR;
  - forbid new `noqa` and `type: ignore` lines (the RUFF.IGNORES added-suppression detector plus `warn_unused_ignores`).

**Reason:** The skill allows scope to change which files are gated, never which rules exist. A shrink-only baseline enforces every rule on new code from day one and reduces the backlog as a side effect of normal work. Repairing everything first would mean about 174 signature refactors and about 1,950 typing changes across a live codebase in a single blocking effort. It would conflict with Phase 6 work, and some of it would cross the identity closures in Q1. Automatic fixes are cheap and safe because the D and format repairs don't change digests, so they are done first and don't inflate the baseline. Files inside a lock or scorer closure are left out of the automatic import fix and go to the Q1 ratchet, so no unit has to relock.

## Q4. Commit hook scope

**Decision:**
- **`checks.commit`** = the path check, then the existing `make quality`, then the conservative affected-T2 seam selector, then the rule-evidence validator (once Q5 unit 7 lands).
- **`checks.ci`** = the same, plus every slow and quality tier on every push and PR. CI runs this as the single always-scheduled job named `gate`.
- Do not build a narrower affected-scope commit check.

**Reason:** The catalogue's `GATE.pre-commit` scope is "static checks, all T1 tests and T2 seams for affected code". The fast-tier tests, the coverage floors and the lock/release checks only exist as a whole-suite bundle inside `make quality`. A check limited to affected files would silently drop T1 coverage and the integer coverage floors, which is a narrower policy wearing the Full label. The 137–199 s cost is real. The fix is the `TEST.budget` work in unit 6 (calibrated limits and parallelising the fast suite), not weaker semantics. Reusing `make quality` also keeps one owner for the gate, as the repository rules require.

## Q5. Unit order

**Decision:** Seven units, each committed as `chore`/`ci`/`test`/`refactor`/`docs`. These types never bump the version, so locks are not staled. Each unit records before and after values for every identity digest:
- `grading_execution_digest()`
- every `primitive_code` entry
- `raidar_spec`
- every scorer `implementation_digest`
- every `stage-tree/1`

Each unit must leave all of them unchanged, pass `make quality`, and pass `experiment-locks-check` without a relock.

1. **Bootstrap gate.**
   - Files: `.omp/gate.json` (schema 2 with `checks.commit`, `checks.ci`, `pathCheck`, and `agentMerge` set to allowed), the dispatcher, the path-check adapter with its structure and allowlist edits, and the tracked pre-commit entry. That entry replaces the fix/stage and pre-push entries.
   - Docs: README and test-tiers updates.
   - CI: the `gate` job on every push and PR, already running slow and quality tiers.
   - The rule-coverage ledger lands in report-only mode, with honest `blocked` rows and outside `checks.*`.
   - After `gate` is green on main, the owner applies the host change: require `gate`, enable auto-merge, keep ruleset 14367802, and confirm by reading the settings back.
2. **Identity guard.** Add digest-bound exclusions for the released folders and grading runtime payload. Add a digest-neutrality check comparing each identity digest with the base, so an accidental change fails unless the commit is a declared identity change. Add the shrink-only baseline checker with canary tests.
3. **Ruff to Full** with the Q2 settings. Apply the D, format and import automatic fixes outside the Q1 closures and the frozen paths. Record the Ruff baseline. Add the added-suppression detector.
4. **Strict mypy:** 3.12, strict, `warn_unused_ignores`, all roots including tests and scripts. Record the mypy baseline. Add the inventory-drift check (`MYPY.package-coverage`).
5. **Missing checkers**, split into several PRs if large:
   - PYQ100–107, PYQ220/221/230/231 and PYS250–253;
   - an owner decision with evidence on PYQ210/211;
   - `TEST.growth`/`duplicate-path` with the behaviour map;
   - `TEST.subprocess-timeouts`, `SKILL.metadata`, `SEAM.evidence` and `PARITY.*`;
   - Prettier pinned at 3.6.2;
   - a pinned dependency audit;
   - the affected-T2 selector wired into `checks.commit`.
6. **Test budgets.** Record at least 5 fast-tier runs. Set limits from those runs, provisional until approved; any later increase is a deviation. Wire the budget checker into the runner.
7. **Flip to fail-closed.** Add per-ID canaries for all 104 IDs. Put the deviations from Q2, the digest-bound exclusions and the baselines into the ledger as approved, recorded entries. Add `validate-rule-coverage` to both `checks.commit` and `checks.ci`, and require it to exit 0. This unit makes rule evidence fail closed.

After that, ordinary PRs shrink the baselines. The Q1 identity ratchet entries are cleared only inside identity batches already scheduled for their closure.

**Reason:** The bootstrap goes first so every later unit is checked by the same `gate` locally and in CI, and so the host requirement protects the rollout itself. The identity guard comes before any configuration that rewrites files, so a mistake in units 3–4 fails loudly instead of quietly staling locks or validations. The flip goes last because turning on fail-closed evidence earlier would either block all merges or require fake green rows, and the skill forbids both. Keeping every unit to non-bumping types and identity-neutral changes avoids relock follow-ups entirely.

## Risks
- **Agent merge setting.** The draft `gate.json` sets `agentMerge: "disabled"`, but the owner keeps agent merge allowed. Unit 1 must correct it.
- **Draft errors.** The draft also uses `target-version = "py313"` and lists the scorer and `raidar_spec` closures nowhere. Both need fixing before anything is applied.
- **Accidental rewrites.** `fix = true` in the config lets a local `ruff check` rewrite import order in ratcheted identity files; Ruff has no per-file "unfixable". The unit 2 digest-neutrality check is the only backstop, so it must run in `checks.commit`.
- **Gaming the baseline.** Counts per (file, code) can be gamed by fixing one finding and adding another in the same file. Tracking by symbol is stronger but more brittle.
- **Hook cost and staged state.** The 2–3 min hook remains until unit 6 delivers speed-ups; developers may bypass it with `--no-verify`, which only the CI `gate` catches. The hook checks the working tree, not the staged index, so a mixed staged/unstaged commit can pass on unstaged code.
- **Slow tiers on every PR.** Running slow and quality tiers on every PR from unit 1 lengthens CI and exposes any flaky seams. CI feasibility on `ubuntu-latest` has not been measured.
- **Ratchet entries may never clear.** If no grading, primitive, scorer or contract identity change is ever scheduled, the Q1 ratchet entries persist indefinitely. The owner should review them at each phase exit.
- **D401 deviation.** Ignoring D401 needs explicit owner approval as a Full deviation. Without it, about 564 manual docstring rewrites are required. They change no digest, but they are large-scale churn.