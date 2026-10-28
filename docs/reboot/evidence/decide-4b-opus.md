```text
Q1: b (med). Primary is the solvable set minus cases the fixed audit flags; raw SCB and the full solvable set are reported beside it.
  - A flag is not a bare heuristic. A case is flagged only when its assertion is not fixed by the spec AND a spec-conformant
    variant of the reference fails it (audit.py:3-21). This is two independent pieces of evidence.
  - forge's hand-written errata contain exactly these 25 cases, "by the audit rule alone" (forge errata.yaml basis), so (c)
    adds hand-written work but no extra judgement. (b) produces the same set as generated output, which keeps Need 1.
  - (a) keeps cases we already know are invalid: all 25 are in forge's solvable sets (25/25 checked). 5 forge
    checkpoint-1 trials failed only these cases. The errata say pilot v2 failed checkpoints 3 and 6 in 16/16 trials on such
    cases, so (a) scores those checkpoints at zero for every arm and they can't show any effect.
  Amendment: pin the flagged set's digest and the audit_version in the validation record and lock. Label each problem's
    audit coverage (audited assertions / total) and show `unmeasured` where it is 0; this applies to 6 of 9 problems today
    (2 of those 6 are already excluded). Fix A12 first (Q3), because under (b) it can start changing verdicts.

Q2: b (med), amended.
  - Re-running the reference can't detect flakiness that depends on the implementation. Observed case: datagate
    `test_allowlist_blocks_upload_export` passed 3/3 on reference 7, failed 2/3 on reference 6, yet sits in the 405-case
    checkpoint-7 set with nondeterministic_count 0.
  - A second grade cuts false fails from a 10%-flaky case from 0.10 to 0.01. That is the bias that matters.
  Amendments:
  (1) Re-grade once, in a fresh sandbox, only the steps whose solvable verdict failed. Whether a checkpoint is solved comes
      out the same as grading everything twice, because a dropped case and a passed case both leave it solved. This costs
      far less than doubling all grading.
  (2) Any case that varies in any reference run of that step's tests, positive or negative, is removed, not only when the
      trees are byte-identical (extends split-design.md:132).
  (3) Report each arm's per-trial nondeterministic cases as a health reading, warning when the arms differ. Known cost:
      this goes easier on agent code that has races.

Q3: b (high on the split), amended.
  Phase 4b, before any comparative result is cited:
  - #1: verdict from an exact test: McNemar when there is one checkpoint (K=1), an exact sign-flip permutation test on the
    deltas when K>1. Keep the interval as descriptive only. Do NOT add A1's "no verdict at zero spread" rule. The exact test
    already handles it (5 pairs all one way: p=0.0625), and 6 or more pairs all one way is legitimately significant.
  - #3: refuse when per-arm exclusion rates differ (split-design.md:515). Pre-register "materially" in the lock.
  - #5: pin the grader identity in the lock and refuse a mismatch. Observed: every forge trial was graded on
    `76fabb7fb4f407d7` but validated on `963c14e63ff5e15d`. Consequence: the 7 usable problems need re-validation on Docker
    before phase 9.
  - #6: grading canary.
  - #7: on a resolver timeout, retry once, then class it as infra. Never `cap-exceeded`. Exclusions this creates fall under #3.
  - #8: refuse `halt_on_failure` where the solvable set ≠ the raw set (metric_transform_lang checkpoints 3–5). Defer
    rewriting the gate: all 10 locks use `continue`.
  - #13, plus the Q1 audit coverage and `unmeasured` work, plus the Q2 changes. Under Q1(b) all of these change verdicts.
  Later:
  - #10, and the judge-spend part of #11 → phase 5: judgeloop and the judge are rewritten as packages there, so fix once.
  - #9 and #12 → phase 6 (health). For #12, rename V3's claim; don't add a live /app digest.
  - Pricing both harnesses from one table (#11) → phase 6.
```

**Audit findings I believe are wrong**, all checked against `git show 712bf3d:` and the stored records:
- **Opus A2: "every `*.validation.json` still reports `flagged: 0`."** forge's report has 25 flagged cases (`spec_determinacy.flagged` length 25). The point holds for the other 8 problems.
- **Opus A8: "every verdict is `validated_partial`."** datagate and mocked_http are `excluded`, both failing `reference_k-1_fails_k`. Seven are `validated_partial`, as Astra says.
- **Opus A6: "All 11 locks."** Commit `712bf3d` tracks 10 locks, all `continue`. The mocked_http example also doesn't count, because that problem is excluded. The metric_transform_lang example stands (163/164, 178/180, 73/74).
- **Opus A3 consequence.** The datagate case can't cause a false fail today because datagate is excluded. The observation still shows that flakiness depends on the implementation.
- **Opus A13: "False exclusion."** Removing `repeat_identical_negative` would not restore datagate. `reference_k-1_fails_k` also fails it ("405/405 expected at least one failure").
- **Astra F1: "do not automatically discard every heuristic determinacy flag."** This misdescribes flags as heuristic. They already require static and dynamic evidence together (audit.py:3-21). On forge, the errata Astra proposes as primary are exactly the 25 automatic flags.
