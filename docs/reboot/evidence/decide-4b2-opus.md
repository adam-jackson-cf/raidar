```text
Q1: a (med) — default analysis.max_exclusion_difference = 0.10, compared as an absolute difference. Rate = an arm's
    excluded slots / its planned slots, counting every cause; refuse when max − min across arms > 0.10. Locks may set it
    lower; the schema caps it at 0.10 (now le=1.0).
  - The rule is already built, but no default: schema.py:246 defaults to None and effects.py:527-532 then refuses. All 10 locks hold null.
  - A ratio fails when an arm has zero exclusions. A significance test has no power at 5–10 pairs, and Astra's amendment rejects it.
  - At 10 pairs, 0.10 allows one extra one-sided exclusion and refuses two, so one arm can use at most half of the 20% cap.
  - Under (b), every existing experiment and the phase-9 A/A test give no verdict until someone chooses a bound. (b) also lets a lock set 1.0.

Q2: b (high) — timeout_retries = 1: at most 2 attempts of 120 s (TIMEOUT_S), no backoff. A second timeout raises
    DependencyInfraError → dependency_resolver_unavailable.
  - Only `_Exhausted` (a slow index that still answers) is retried (resolver.py:492-499). A failed canary or missing uv raises
    infra at once, so retries cannot hide an outage.
  - The exclude-newer cutoff fixes the result, so a retry cannot change a score. It only cuts exclusions that land on
    arms with more dependencies, which feeds Q1.
  - Resolution runs inside run_oracle before pytest (grader.py:148-156). 240 s of a 900 s verifier budget is
    acceptable; 2 retries plus backoff would eat test time and push slow grades into sandbox_timeout.

Q3: a (high) — keep refusing: expected_seconds → None → no rework turn; the checkpoint ends with its recorded deadline/budget ending.
  - The shipped runner never reaches this case. runner.py:384 always runs FIRST_BUILD before any check, and :498 records
    its seconds. If the first turn throws, the exception propagates and no rework is scheduled.
  - With no stored deadline endings (audit A10), a floor would be a made-up number. A too-small floor brings back the
    "cut, half-edited tree" bias against judged arms; a large one cuts turns for no reason.
  - The same refusal sits in judgeloop.py:93-95 and package.py:207-209. No change needed.

Q4: b (high) — a step the lock pins with an empty solvable set does not gate a halt run: it is neither a pass nor a
    fail, the run advances, and the step is reported excluded (EXCLUDED_STEP_REASON). A step with no verdict at run
    time (infra failure, verifier leak, no pinned set) still halts.
  - Today an empty set gives solvable_pass_rate None (solvable.py:30-31,73). Harbor reads the missing key as -inf, and
    collect._halts agrees. So a halt run on metric_transform_lang stops after checkpoint_1 every time, and mocked_http after checkpoint_7.
  - Continue scoring already skips excluded steps (correctness/scorer.py:413-433). Halt should not score them differently.
  - The agent never sees verifier results (F4), so advancing leaks nothing. (a) would bar 2 of 9 problems from halt runs for no gain.
  - Implementation: emit a separate gate reward for pinned-empty steps. Do not report a fake solvable 1.0, which analysis could read as "solved".
```

I edited no files and ran no Docker or trials. I read the brief, report, audits and votes, then the code each setting touches, at HEAD `26cb23e`.
