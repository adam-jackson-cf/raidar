Decision: a (high confidence) — remove the generated “wrong-state” case; leave note-state unchanged.

- **Exact change:** delete `"wrong-state"` from `_damage` and `DATA_CASES` in `orchestrator/src/raidar/subject/workflow/stage_check.py:276–301`; remove its enumeration/assertions from `orchestrator/tests/test_stage_check_outcomes.py`.
- Amend `lab/stages/README.md:109`: remove the promise of generically detecting “semantically wrong state”; explain that semantic expectations come from package-authored fixtures.
- **Evidence:** calls `0017` and `0018` both returned valid `done` with `carry.words: 4`. The fixture requires three words; the generated text satisfies its rule.
- **Rationale:** `stage/1` defines context, result protocol, required outputs and filesystem bounds—not a universal state schema or lab-written validity marker. Thus neither (b) nor (c) has a contractual basis.
- The README’s existing non-done requirement is the defective promise, not evidence that these four words are semantically invalid. Both `stage-authoring-{astra,opus}.md` assign method rules/pass meaning to authors; `split-design.md` §5.3 accepts valid results as reported. Canary checks reproducibility, not semantic truth.
- **Identity impact:** no `@N` release, dependency lock or experiment relock. Package/runtime bytes remain unchanged; the read-only inventory check confirmed `stage_check.py` belongs to no primitive, runner or spike identity closure. A subsequent successful stage-check creates the missing generated canary record.
- No files edited; no containers run.
