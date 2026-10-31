Decision: **a** (confidence ≈0.85). The check is the defect. Remove the `wrong-state` case and don't replace it with another generic check. Each package's own `resume` and `closed` fixtures already test what that package considers wrong state.

**Change**
- `orchestrator/src/raidar/subject/workflow/stage_check.py:283,301`: delete the `"wrong-state"` writer from `_damage`, and delete `"wrong-state"` from `DATA_CASES`.
- `orchestrator/tests/test_stage_check_outcomes.py:41,188-190`: delete it from `DATA_MODES`, and delete the assertions that the generated `raidar-wrong-state` bytes differ from the original. Those assertions only test the generator.
- `lab/stages/README.md:109`: delete "semantically wrong state". Add a sentence that each package defines wrong state for itself through its mandatory `closed` fixture and its `resume` fixture (required when `max_rounds > 0`). Add a dated line to the contract change log.
- `CHANGELOG.md:17-21`: replace the "still fails" note with this decision.

**Rationale**
- The generator writes `"unrelated words, no check\n"` into every `done` state file. For note-state that is valid state: `_stop` returns `done` when the note has at least `min_words` (3) words, and this text has 4. Raidar has no package-independent definition of "semantically wrong", so it can't generate such state.
- **(b) has nothing to build on.** `stage/1` defines no lab-written fields in watched state; the root-owned data is the context file, not the state. The only contract rule on state is that `done` requires every declared output, and the `empty-state` case and the runtime "missing done output" rule already cover that.
- **(c) has no rule to cite.** The README says "Authors own their method's text, tools and rules". Nothing in `stage/1` makes a 4-word note invalid. note-state's own `resume` fixture (`two words` → `resume`) is already its wrong-state test.
- The idea came from `stage-stop-failure-opus.md:47` ("schema-valid but semantically wrong"). That assumes a package schema Raidar doesn't have. `split-design.md` §5.3 and the ST1–ST7 rules never require this case, so the design doesn't need to change.

**Identity impact:** none.
- No `@N` release, package digest, workflow release or lock changes, and no relock is needed.
- `stage_check.py` is imported only by `cli/stage_fixtures.py`, so it isn't in any primitive's `primitive_code` import closure.
- `StageCheckRecord` is built only from the `done` run (stdout digest, outputs, resource floors) and doesn't list the cases. Existing stage-check proofs and canaries stay valid.
- Removing a case can't make a passing package fail.

Nothing was edited or run; this comes from reading the code, the fixture, the contract, the design and the evidence docs.
