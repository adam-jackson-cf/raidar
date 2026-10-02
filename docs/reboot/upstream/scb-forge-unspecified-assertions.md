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
