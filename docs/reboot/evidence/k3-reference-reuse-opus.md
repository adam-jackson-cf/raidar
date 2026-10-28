The checkpoints aren't already solved. SCB (SlopCodeBench) often reuses one finished, later-checkpoint solution as the reference for several checkpoints, so "reference k−1 passes checkpoint k" only shows the earlier reference was written ahead of its spec. An agent at checkpoint 2 still has to build features that checkpoint 1's spec never mentioned. [OBSERVED] Raidar's check therefore tests how SCB authored its references, not whether checkpoint k adds anything. It also misses the real defect in mocked_http, at checkpoint 7. [INFERENCE]

**1. What the identical references mean**
- I hashed and diffed the reference folders. mocked_http cp1–cp4 are byte-identical (`hmock.py` plus `requirements.txt`, 3,676 lines). cp5–cp7 are identical too: they add `omctl.py` and leave `hmock.py` unchanged. cp8 changes `hmock.py`. datagate cp1–cp6 differ and grow from 319 to 1,433 lines; cp6 = cp7. [OBSERVED]
- The cp1 reference already contains features from later checkpoints:
  - cp2's helpers `gJsonPath`, `uuidv5`, `hmacSHA256` (`solutions/checkpoint_1/hmock.py:1189-1195`) and `body_from_file` (`:1913`);
  - Redis, Kafka, AMQP and gRPC (`:1424-1469`, `:1996`).

  It is close to the finished product, not a cp1-only solution. [OBSERVED]
- datagate's cp6 reference already implements cp7's `enrich=yes` (`solutions/checkpoint_6/datagate.py:587-625`, `:802`), although no spec from cp1 to cp6 mentions "enrich". [OBSERVED]

**2. What an agent faces (mocked_http cp1 → cp2)**
- `checkpoint_1.md` covers HTTP matching, templating with basic helpers, and `reply_http`/`sleep`. It never mentions `jsonPath`, `gJsonPath`, `xmlPath`, `uuidv5`, `regexFind*`, `hmacSHA256`, `isLastIndex`, `htmlEscapeString` or `body_from_file`. [OBSERVED]
- `checkpoint_2.md` introduces exactly those. The tests require them, for example:
  - `tests/test_checkpoint_2.py:16` (`body_from_file`);
  - `:56` (`gJsonPath`, `regexFindFirstSubmatch`, `hmacSHA256`);
  - `:222` (`uuidv5`, `xmlPath`, `isLastIndex`);
  - `:307`, which pins the exact HMAC and UUIDv5 values. [OBSERVED]
- Checkpoint 2 runs 55 tests: 26 are new and 29 are cp1 regression tests (`groups` in the validation record). An agent that solved only cp1's spec would fail the new ones. [OBSERVED counts; INFERENCE about the agent]

**3. How SCB uses references**
- SCB's evaluator never runs them. Its tooling only checks that the folder exists (`slop-code-bench/src/slop_code/entrypoints/commands/problems.py:134`). Its docs call the reference "For problem development • Not used in evaluation" (`docs/problems/structure.md:85-88`). [OBSERVED]
- The only requirement is that every reference passes every collected test for its checkpoint (`docs/contributing-problems/review-checklist.md:129`). SCB also concedes some references don't even do that, and treats the tests as the ground truth (`docs/KNOWN_ISSUES.md:40`). [OBSERVED]
- Nothing requires a reference to be minimal for its checkpoint. In SCB, a reference only proves the tests can be passed. [OBSERVED]

**4. Is `reference_k-1_fails_k` sound?**
- No, for SCB. It assumes reference k−1 implements only spec k−1 (`orchestrator/src/raidar/design/problem/validation.py:13-14`, `:186-198`; `direct.py:28-36`). SCB never promised that. [INFERENCE]
- It wrongly fails mocked_http cp2–cp6 and datagate cp7. [OBSERVED]
- It also passes cases it should not. mocked_http cp7 "passes" only because its reference fails its own new tests:
  - the cp7 reference scores 141/159 on cp7, failing all 18 new tests (Kafka/AMQP);
  - so cp7's set of scoreable tests is 141 cases, all of them regression tests (`solvable.checkpoint_7.size`);
  - the cp8 reference also fails those 18 cp7 tests when they run as regression tests (141/159). [OBSERVED]

  So cp7 currently scores an agent on nothing new, and no check excludes it. The likely causes are that no Kafka/RabbitMQ broker is available to the grader, or a test mismatch. [INFERENCE] The local SCB checkout has an uncommitted edit to `mocked_http/tests/test_checkpoint_7.py` (+239/−38). Its sha256 differs from the pinned commit's copy (`7f4c…` vs `2d42…`). [OBSERVED]
- Better checks to replace it:
  - (a) **Test-diff:** checkpoint k must collect at least one test that earlier checkpoints don't, and reference k must reliably pass at least one of those new tests. This catches mocked_http cp7. [INFERENCE]
  - (b) **Empty-starter negative:** keep it, since it is meaningful. [INFERENCE]
  - (c) **Spec-diff audit:** confirm the new tests check behaviour that first appears in `checkpoint_k.md`. This needs judgement, so record it as a reading rather than gating on it. [INFERENCE]

**5. datagate's flaky negative**
- The cp6 and cp7 references are the same code running the same tests. The negative run (cp6 reference against cp7's tests) is therefore identical to cp7's positive run (cp7 reference, cp7 tests). [OBSERVED]
- `checkpoint_6::TestCore::test_allowlist_blocks_upload_export` passed in repeat 0 and failed in repeats 1 and 2. It passed in all 3 positive repeats at both cp6 and cp7. That is 2 failures in 9 runs of the same code. [OBSERVED]
- So datagate's `reference_k-1_fails_k` "passed" in repeats 1 and 2 only because of this flake. [OBSERVED]
- Likely cause: `do_POST` rejects the request on the allowlist (`datagate.py:778-780`) before reading the upload body (`:852`). Closing the connection with an unread body can race the client reading the 403 reply. [INFERENCE]
- It does matter for grading agents. The case is still in the cp6 and cp7 scoreable sets because positive repeats are compared only against each other. An agent with the same reject-before-read design would sometimes lose a case at random. [INFERENCE]

**Verdict on K3: amend**
1. **Byte-identical → `not_informative`: agree, with one condition.** It is honest, since an identical pair gives no negative evidence. But every step must still pass the new-test check from 4(a). Without that, mocked_http cp7 (identical to cp6, zero new scoreable tests) and datagate cp7 would get through. [INFERENCE]
2. **References differ but k−1 still passes → exclude only that step: reject.** mocked_http cp5 (15 new tests) and datagate cp7 (52 new tests) are real work for an agent. The overlap reflects how SCB wrote its references, not a defect in the step. Use the check from 4(a) instead, and exclude a step only when it has no new scoreable tests. [INFERENCE]
3. **Varying negative becomes a reading: agree, and add a pooling rule.** When the same code runs the same tests, pool positive and negative repeats. Any case whose outcome varies becomes nondeterministic and is removed from the scoreable set at every step where it appears. Without that, the reading hides a known-flaky case that is still scored. [INFERENCE]
4. **Hidden defect:** K3 as written would hide mocked_http cp7, a step with no scoreable new tests, behind a check that passes. Adding the check from 4(a) is what catches it. [INFERENCE]

I edited no file and ran no Docker or trials. Everything above comes from the committed validation records, file hashes, diffs and source reads.
