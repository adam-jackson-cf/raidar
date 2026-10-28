[OBSERVED] These problems contain reference implementations that already implement later requirements; that is not evidence that an agent has already solved those requirements.
[OBSERVED] In `mocked_http`, checkpoint 2 explicitly adds file-backed responses and template helpers, and its tests exercise them.
[INFERENCE] The confusion comes from treating “checkpoint 1 reference” as “a solution containing only checkpoint 1’s requirements”—an assumption these references do not support.

Evidence

[OBSERVED] Paths below use `P` for `/Users/adamjackson/Projects/inflight/scb-problems`, `S` for `/Users/adamjackson/Projects/inflight/slop-code-bench`, and `R` for the Raidar repository.

1. What the identical references mean

[OBSERVED] Recursive folder diffs found:
- `mocked_http`: checkpoint 1–4 source files identical; checkpoint 5–7 identical.
- Checkpoint 4→5 adds only `omctl.py`; `hmock.py` and requirements remain identical.
- Checkpoint 7→8 changes `hmock.py` and requirements.
- `datagate`: checkpoint 6 and 7 folders identical.
- Local `__pycache__` directories exist in HTTP checkpoints 1 and 8; excluding those generated caches gives the source comparisons above.

[OBSERVED] The checkpoint 1 HTTP implementation already registers `gJsonPath`, `uuidv5`, regex helpers and `hmacSHA256`, and loads `body_from_file` (`P/mocked_http/solutions/checkpoint_1/hmock.py:1189-1197,1900-1922`).

[INFERENCE] This establishes reuse of an implementation broader than the earlier specification. It does not establish the historical direction of copying, nor that an agent’s checkpoint 1 submission would contain those later features.

2. What an agent faces: checkpoint 1→2

[OBSERVED] Checkpoint 1 specifies inline response bodies and lists its template functions; it does not request `body_from_file`, `gJsonPath`, or `hmacSHA256` (`P/mocked_http/checkpoint_1.md:105-140`).

[OBSERVED] Checkpoint 2 adds JSON/XML querying, deterministic UUIDs, regex/HMAC helpers and file-backed bodies (`P/mocked_http/checkpoint_2.md:5-83`).

[OBSERVED] Its tests create a response file containing request-context templates and assert the rendered JSON; another test routes using `gJsonPath` and checks regex/HMAC output (`P/mocked_http/tests/test_checkpoint_2.py:16-88`).

[INFERENCE] An agent implementing only checkpoint 1’s requested inline-body behavior would need additional work to pass that file-backed-body test. The reused reference already did that work; the agent has not thereby received it.

[OBSERVED] The recorded 55/55 includes 29 regression cases, not 55 newly introduced cases (`R/lab/problems/registry/scb/mocked_http/38d627ecf668.validation.json:99-117`).

3. What SCB requires of references

[OBSERVED] SCB describes reference solutions as “For problem development” and “Not used in evaluation” (`S/docs/problems/structure.md:85-88`). Its structure checker checks that each solution directory exists, not that it implements only that checkpoint (`S/src/slop_code/entrypoints/commands/problems.py:115-135`).

[OBSERVED] SCB also documents references that do not pass every test and says it prioritized test accuracy over repairing every reference (`S/docs/KNOWN_ISSUES.md:38-58`).

[INFERENCE] The inspected contract supports references as development/validation examples, not certified minimal implementations. A passing reference demonstrates passability for that execution; merely shipping a reference does not guarantee it.

4. Is Raidar’s check sound?

[OBSERVED] Raidar fails the check whenever the earlier reference passes every graded case; any failed check excludes the problem (`R/orchestrator/src/raidar/design/problem/validation.py:25-28,184-196`).

[INFERENCE] That is not a sound test of “checkpoint k tests something new.” Passing can mean an over-capable reference; failing can mean an old regression bug rather than coverage of a new requirement.

[INFERENCE] A better assessment maps new specification obligations to actual test assertions. Spec differences establish requested changes; test-file differences alone do not establish meaningful coverage.

[INFERENCE] Stronger evidence comes from a controlled implementation that satisfies earlier requirements but deliberately lacks the new behavior: old tests should pass and relevant new tests should fail. Reference-folder names cannot establish that control.

5. Datagate’s varying case

[OBSERVED] The negative checkpoint-7 runs disagree on `checkpoint_6::TestCore::test_allowlist_blocks_upload_export`; the positive runs report no variation (`R/lab/problems/registry/scb/datagate/38d627ecf668.validation.json:8-26`).

[OBSERVED] That test sends a multipart upload, then an export request through the same HTTP client, expecting both to receive 403 (`P/datagate/tests/test_checkpoint_6.py:264-283`).

[OBSERVED] The reference uses HTTP/1.1 and rejects the upload before reading its body; its error response neither drains that body nor explicitly closes the connection (`P/datagate/solutions/checkpoint_6/datagate.py:737-738,778-785,843-852,982-992,1010-1023`).

[INFERENCE] Unread upload bytes can corrupt the next request on a reused connection; timing around connection closure/reuse plausibly explains the variation. The recorded outcomes do not prove that exact failure mechanism.

[OBSERVED] Despite identical checkpoint-6/7 reference sources, the varying case remains in checkpoint 7’s 405-case scoring set (`R/lab/problems/registry/scb/datagate/38d627ecf668.validation.json:2402-2407,2684`).

[INFERENCE] Therefore this is not safely dismissed as “only a negative control”: equivalent code has inconsistent outcomes on a scored case. It matters for grading confidence, although it does not prove correctly implemented agents will also fluctuate.

K3 verdict: amend

[INFERENCE] **1—Agree:** identical effective reference inputs make this novelty control `not_informative`, not a benchmark failure. Retain separate checks that the current reference can pass.

[INFERENCE] **2—Reject automatic step exclusion:** different folders do not imply different relevant behavior. HTTP checkpoint 4→5 demonstrates this: only an extra CLI file appears, yet the earlier reference passes 114/114. Treat that as inconclusive novelty evidence pending specification/test review, not grounds to discard the step.

[INFERENCE] **3—Agree only with safeguards:** remove automatic whole-problem exclusion for negative-control variation, but retain a visible grading-reliability finding. Here, investigate the connection-handling defect and resolve whether the reference needs repair or the scored case needs quarantine; do not silently treat the variation as harmless.

[INFERENCE] K3 corrects an unjustified exclusion rule, but unchanged it could both discard valid steps and hide real grading instability. Identical references are not themselves a benchmark defect; missing coverage and unreliable scoring remain defects worth checking.

[OBSERVED] Investigation used source inspection, saved validation records and folder diffs; no files were edited, and no Docker runs or trials were launched.
