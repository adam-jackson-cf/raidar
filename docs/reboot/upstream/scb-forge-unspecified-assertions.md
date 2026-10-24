# DRAFT (not filed): `forge` hidden tests assert error details and output bytes the specification does not state

- **Status:** draft for review; not filed upstream.
- **Upstream:** `gabeorlanski/scb-problems` (problems) at `38d627ecf668a88f88f8d260f8df8df6116e9b03`, problem `forge`, checkpoints 1, 3, 6 and 7. The same content is in the SprocketLab SCB fork checkout Raidar validated against (`docs/reboot/oracle-validation.md`).
- **Kind:** test/spec mismatch. The reference solutions pass every test; conforming solutions written from the specification alone cannot know what these tests require.
- **Prepared by:** Raidar reboot, from its spec-determinacy audit (`make problem-validate PROBLEM=scb/forge@38d627ecf668 AUDIT_ONLY=1`; report section `spec_determinacy` of `problems/registry/scb/forge/38d627ecf668.validation.json`) and from 32 agent trials (pilot v2).

## Summary

25 test cases in `forge` assert something the checkpoint's `instruction.md` (and every earlier one) does not determine:

| Checkpoint | Cases | What the test asserts | What the specification states |
|---|---|---|---|
| 1 | 2 | the error detail contains the exact phrases `name must be a non-empty string` / `requirement_sets must be a non-empty array` | the constraints "`name` must be present and a non-empty string" / "`requirement_sets` must be present and a non-empty array", and that "the detail describes the failing constraints"; never these phrases |
| 3 | 4 | the `invalid_operation` detail contains the offending operation value | the category and its priority only |
| 3 | 2 | the `duplicate_assignment` detail contains the `assignment_id` | the category and its trigger only |
| 6 | 13 | the `detail` of `not_found`, `flash_failed` and `invalid_image` errors **equals** the bare identifier or image string | the categories and their order; for `not_found`, checkpoint 1 says the detail "identifies" the identifier, which a detail such as `"module 1f… not found"` does |
| 7 | 3 | the `not_found` detail equals the bare identifier (UUID-shaped name lookup; missing parent unit) | as for checkpoint 6 |
| 7 | 1 | a revisioned response is compact, key-sorted JSON with no trailing newline (byte-exact) | the revision envelope; no output byte format anywhere in checkpoints 1-7 |

The cases are listed one by one below.

## Why it matters

Under SCB's strict per-checkpoint solve rule and the default `include_prior_tests`, a case that a conforming program cannot pass at checkpoint *k* also fails as a regression at every later checkpoint. With the 21 cases at checkpoints 3, 6 and 7, no conforming submission can solve checkpoint 3 or any later checkpoint, so a trajectory's checkpoint solve rate is capped at 2/8.

Observed (Raidar pilot v2, `codex` 0.156.1 with `gpt-6-luna` at low effort, two conditions, 16 trials each): the 21 cases at checkpoints 3, 6 and 7 that the audit flags and that are not about `duplicate_assignment` failed at their origin checkpoint in 32 of 32 trials. The checkpoint-1 pair failed in 5/16 trials of one condition and 14/16 of the other. In every one of those failures the program rejected the input with the right category and exit code and only the wording differed (for example `actual detail='name must be present and a non-empty string'`, which is the specification's own sentence). The two `duplicate_assignment` cases failed in 0 and 1 of the 32 trials: agents tend to include the id anyway, but the specification does not require it.

## How the cases were found

1. **Static.** Every assertion on an error's `detail`/`message` and every byte-level comparison of stdout in the test files was classified against the text of the case's origin checkpoint and all earlier checkpoints. An assertion is *undetermined* when its expected literal occurs neither in the specification nor among the test's own inputs (a wording to guess), when it requires the detail to contain or equal an input value and no paragraph states what that category's detail contains or that it is exactly that value, or when it compares stdout byte for byte and no output byte format is stated.
2. **Dynamic.** Each reference solution was graded at its checkpoint unchanged and as two spec-conformant variants: every error detail replaced by a neutral text (category, exit code and JSON format kept), and the JSON output re-serialised with indentation and a trailing newline (value and key order kept). A case is listed only when it passes on the reference, fails on a variant, and its test has an undetermined assertion of the kind that variant changes.

All 25 cases meet both conditions. The audit's rules, per-assertion verdicts and per-variant grades are in the validation report.

## The cases

Paths are relative to `forge/` in the problems repository (`tests/` is the hidden test directory; `checkpoint_N.md` is the specification the agent sees, rendered by the Harbor converter as `steps/checkpoint_N/instruction.md`; line numbers below refer to that rendered file and to the test files at the pinned commit).

### Checkpoint 1: unstated wording (2)

| Case | Assertion | Specification |
|---|---|---|
| `test_checkpoint_1.py::test_create_rejects_empty_name_string` | `detail_contains="name must be a non-empty string"` (`tests/test_checkpoint_1.py:568-572`) | "`name` must be present and a non-empty string." (`instruction.md:53`, `:128`); "the detail describes the failing constraints" (`:125-126`) |
| `test_checkpoint_1.py::test_create_rejects_empty_requirement_sets` | `detail_contains="requirement_sets must be a non-empty array"` (`:588-592`) | "`requirement_sets` must be present and a non-empty array." (`:54`, `:129`) |

### Checkpoint 3: unstated detail content (6)

The specification lists the bind error categories and their priority (`instruction.md:54-61`) and the duplicate-assignment guard (`:38`). It says nothing about any detail at checkpoint 3.

| Case | Assertion |
|---|---|
| `test_checkpoint_3.py::test_bind_invalid_op_reports_bad_value` | `invalid_operation` detail contains `'invalid-op'` (`tests/test_checkpoint_3.py:265`) |
| `test_checkpoint_3.py::test_bind_invalid_op_precedes_not_found` | detail contains `'not-an-op'` (`:381`) |
| `test_checkpoint_3.py::test_invalid_op_no_remove_side_effects` | detail contains `'bad-op'` (`:499`) |
| `test_checkpoint_3.py::test_invalid_op_precedes_duplicate` | detail contains `'bad-op'` (`:624`) |
| `test_checkpoint_3.py::test_bind_duplicate_assignment_guard` | `duplicate_assignment` detail contains the `assignment_id` (`:310`) |
| `test_checkpoint_3.py::test_duplicate_guard_blocks_mixed_ops` | same (`:344`) |

### Checkpoint 6: detail must equal the identifier (13)

The specification defines `invalid_image`, `not_found` and `flash_failed` and their order (`instruction.md:21-23`, `:66-74`, `:121-131`). It never states what their detail is. Checkpoint 1 states that a `not_found` detail "identifies" the requested identifier (`checkpoint_1 instruction.md:72`, `:141-144`); the earlier checkpoints' tests accept any detail that contains it (`detail_contains`), while these tests require equality (`detail=`).

| Case | Assertion |
|---|---|
| `test_checkpoint_6.py::test_flash_requires_stored_image` | `flash_failed` detail `== image_a` (`tests/test_checkpoint_6.py:193`) |
| `test_checkpoint_6.py::test_create_unknown_unit_not_found` | `not_found` detail `== missing` (`:208`) |
| `test_checkpoint_6.py::test_tag_delete_removes_from_get_and_list` | `not_found` detail `== tag['uuid']` (`:289`) |
| `test_checkpoint_6.py::test_flash_bad_image[invalid-format]` | detail `== detail` parameter (`:314-329`) |
| `test_checkpoint_6.py::test_flash_bad_image[unknown-image]` | same |
| `test_checkpoint_6.py::test_flash_bad_image[uppercase-uuid]` | same |
| `test_checkpoint_6.py::test_flash_invalid_image_precedes_not_found` | `invalid_image` detail `== 'NOT-A-VALID-UUID'` (`:343`) |
| `test_checkpoint_6.py::test_create_invalid_firmware_image` | `invalid_image` detail `== 'A1B2C3D4-E5F6-7890-ABCD-EF1234567890'` (`:362`) |
| `test_checkpoint_6.py::test_get_flash_nonexistent_not_found` | `not_found` detail `== missing` (`:376`, `:383`) |
| `test_checkpoint_6.py::test_tag_create_requires_module` | `not_found` detail `== missing` (`:519`) |
| `test_checkpoint_6.py::test_flash_unknown_image_rejected_consistently` | `flash_failed` detail `== new_image` (`:1017`, `:1024`) |
| `test_checkpoint_6.py::test_tag_get_delete_nonexistent[get]` | `not_found` detail `== missing` (`:1170`) |
| `test_checkpoint_6.py::test_tag_get_delete_nonexistent[delete]` | same |

### Checkpoint 7: exact detail and output bytes (4)

| Case | Assertion | Specification |
|---|---|---|
| `test_checkpoint_7.py::test_blueprint_get_uuid_shaped_name_uses_uuid_lookup` | `not_found` detail `== uuid_shaped_name` (`tests/test_checkpoint_7.py:925`) | `not_found` only (`instruction.md:34-38`) |
| `test_checkpoint_7.py::test_unit_missing_parent_not_found[1.1]` | `not_found` detail `== parent_uuid` (`:952`) | as above |
| `test_checkpoint_7.py::test_unit_missing_parent_not_found[1.2]` | same | as above |
| `test_checkpoint_7.py::test_revision_preserves_compact_json` | `assert_compact_sorted_json_output` (`tests/_helpers.py:193-198`): no trailing newline and `stdout == json.dumps(parsed, sort_keys=True, separators=(",", ":"))` (`tests/test_checkpoint_7.py:452-469`) | the revision envelope (`:26-28`); checkpoint 1's "Output Format" (`checkpoint_1 instruction.md:99-104`) covers ordering, UUID and timestamp formats only |

## Suggested resolutions (either per case)

1. **State it.** Add the asserted content to the checkpoint's specification: the exact validation phrases at checkpoint 1; "the detail contains the offending operation value" / "the `assignment_id`" at checkpoint 3; "the detail is exactly the identifier (or image string)" at checkpoints 6 and 7; "responses are compact, key-sorted JSON without a trailing newline" (from checkpoint 1 on, if that is the intent).
2. **Relax the test** to what the specification states: `detail_contains` of the identifier instead of `detail ==`; a containment of the constraint's field name instead of the exact phrase; parse the JSON instead of comparing bytes; drop the detail check at checkpoint 3.

## Not included

- `test_checkpoint_5.py::test_create_sorts_capabilities_even_with_duplicates` (checkpoint 5 calls capabilities a "set" but the test keeps duplicates) is a candidate of a different kind (a value that conflicts with the specification's wording rather than one the specification leaves open). The audit's rules do not cover it; it is reported separately if confirmed.
- How Raidar handles these cases until upstream changes: a pinned, pre-registered errata (`problems/registry/scb/forge/38d627ecf668.errata.yaml`) that excludes them from an *additional* errata-applied solve verdict. The raw SCB grade is unchanged and still reported.

## Draft note (separate from the 25; not filed): a timing-dependent ordering test

- **Status:** draft for review, recorded 2026-10-07 (guardrails experiment, rationale §18.5); not filed upstream. Not part of the 25 cases above and not an erratum: the specification does determine this behaviour, so it is not a test/spec mismatch. It is a test whose verdict on a *wrong* program can depend on wall-clock timing.
- **Case:** `test_checkpoint_1.py::test_blueprint_list_orders_by_created_at_name_uuid` (`tests/test_checkpoint_1.py:78-105`).
- **Specification:** "**Ordering**: `created_at` ascending, then `name` ascending lexicographic, then `uuid` ascending lexicographic as final tie-breaker" (`checkpoint_1 instruction.md:64`), with `created_at` in ISO 8601 UTC at second precision (`:24`, `:103`).
- **What the test does:** creates `zeta`, `alpha` and `mango`, in that order, through three separate CLI invocations, lists them, and asserts that the `(created_at, name, uuid)` tuples are sorted.
- **Why the verdict can depend on timing:** when all three creates land in the same second, the specified order is the name order, so a program that ignores `created_at` (for example, one that sorts by name only) produces the same list and passes. When the creates straddle a second boundary, the same program lists `alpha, mango, zeta` against a `created_at` order of `zeta, alpha, mango` and fails. A correct program passes either way. A slower run (a loaded grader, or a run under coverage instrumentation) is more likely to straddle.
- **Observed in Raidar (signal only):** in `smoke-3b-4` the case passed in the grading run and failed in the per-test coverage run at four steps of one trial; it did not flip in any of `main-03`'s 64 offline coverage runs. Raidar records it as a known verifier sensitivity (`problems/registry/scb/forge/38d627ecf668.sensitivities.yaml`), shown beside the grading-vs-coverage disagreement count; correctness and the solve rates are unchanged.
- **Suggested resolution:** make the test independent of timing in the same way as its sibling `test_blueprint_list_name_tiebreak_when_created_at_collides` (`:108-140`), which rewrites the stored `created_at` values: for example, set three distinct stored `created_at` values in an order that differs from both the name order and the creation order, and assert the listed order exactly.
- **Qualification (2026-10-08, rationale §18.11):** rewriting stored values removes the timing only when the rewritten rows leave no tie that a generated `uuid` must break. The suggested three distinct `created_at` values meet that. The uuid tie-break sibling below uses the same rewriting and still depends on chance, because it leaves exactly such a tie.

## Draft note (separate from the 25; not filed): two uuid tie-break tests that depend on random uuids

- **Status:** draft for review, recorded 2026-10-08 (guardrails experiment, rationale §18.11); not filed upstream. Not errata: the specification determines both behaviours. Each is a test whose verdict on a *wrong* program depends on the random `uuid4` values that program generates.
- **`test_checkpoint_1.py::test_blueprint_list_uuid_tiebreak_when_created_at_and_name_collide`** (`tests/test_checkpoint_1.py:771-803`). Specification: `uuid` ascending as the final tie-breaker (`checkpoint_1 instruction.md:64`). The test creates two blueprints, rewrites both stored rows to the same `created_at` and `name`, and asserts that the two tied rows are listed in `uuid` order. A program that does not break the tie by `uuid` keeps insertion order, so it passes exactly when the first generated `uuid` sorts below the second: about half the time, on any run.
- **`test_checkpoint_2.py::test_allocation_list_ordering`** (`tests/test_checkpoint_2.py:99-131`). Specification: allocations are listed by `created_at` ascending with ties broken by `uuid` ascending (`checkpoint_2 instruction.md:58`), and `created_at` has second precision (`:18`). Two separate creates may or may not share a second (timing). When they do, a program without the `uuid` tie-break passes only if the generated `uuid`s happen to be in creation order. When they do not, a program that orders by `uuid` alone passes only by the same chance.
- **Observed in Raidar (signal only):** the first case flipped between the grading run and the coverage run at four steps of one `main-04` trial whose blueprint list sorted by name only. The second case flipped at four steps of one `smoke-4` trial. Both are recorded as known verifier sensitivities (kind `randomness`).
- **Suggested resolution:** write fixed `uuid` values into the stored rows, in an order opposite to insertion order, and assert the listed order exactly. For the allocation test, also rewrite the stored `created_at` values so that the tie is certain.
