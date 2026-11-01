---
name: "test-per-aspect"
description: "USE WHEN writing or changing tests that prove a feature definition's acceptance criteria, one test per aspect."
---
Write the tests criterion by criterion, before or beside the code that satisfies
each one.

1. **Read the criterion's three parts.** The `Source:` statements are the
   requirement. Each `Aspect:` is one situation to set up and one outcome to
   assert. You write one test per aspect, or one test per situation with one
   assertion per outcome.
2. **Name each test after its aspect:** `test_<criterion id>_<aspect number>_<slug>`
   (`test_f3_ac2_2_missing_table_stderr`), and map every such test in
   `ac_tests`.
3. **Set the situation up from the outside, as the product is used:** the
   command-line argument, the request, the input file, the configuration the
   criterion names. Do not call an internal function to skip the entry point
   the criterion names, and do not set a value the criterion says is a default.
4. **Assert the outcome as the aspect states it:** the exact exit code, the
   message text, the field values, the order, the absence. A proxy is not the
   outcome: only the exit code, only a status, only another field of the same
   reply, truthiness, a not-null check, `>= 0`, or the input echoed back. Where an
   `Example:` line gives a value, assert that value literally.
5. **Arrange the input so the expected outcome differs from the input and from
   the state before the action:** a sort test starts unsorted, a default test
   leaves the value out, a rollback test changes data first, a "not replaced"
   test reads the data after the action. Then ask: if the line that produces
   this outcome were deleted, would this test fail? If not, add the assertion
   that would.
6. **Write every test out in full:** never call one test from another, never
   create tests while the test file loads (by a loop, by assignment, by
   reflection or by evaluating code). One situation per test, or per case of a
   parametrised test.
