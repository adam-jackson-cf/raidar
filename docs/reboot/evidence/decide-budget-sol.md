# Tie-break: test budgets — GPT-6.1 Sol

## Q1 — Enforcement environment and hook role

**Chosen:** Astra’s full Q1 answer.

**Reason:** Both voters choose CI-only timing enforcement and advisory hook timings. The substantive differences are baseline comparability, environment identity and the hook’s explicit responsibilities.

Astra requires five complete runs of the **same revision and measurement configuration**, and identifies dependencies, coverage, selector and worker count alongside the runner environment. That better isolates runtime variability from suite changes than Opus’s recent-main-run approach.

Checked facts support this choice:

- `test-tiers.md:17–22` includes collection and coverage, excludes provisioning, and explicitly makes CI—not local measurements—the host timing reference.
- `Makefile:748–756` shows `gate-ci` invokes `gate-commit`; timing enforcement must distinguish the invocation without duplicating the fast tier.
- `quality.py:136–162` already runs the covered fast tier once.
- The workflow pins Python 3.13.7 but uses mutable `ubuntu-latest` (`ci-quality-gates-incremental.yml:9–28`).

Opus explicitly fails invalid hook measurements; Astra leaves hook timing advisory while Q3 supplies report-validity requirements. I adopt Astra’s complete answer, not an additional hook-specific policy borrowed from Opus.

## Q2 — Formulas and existing-test exceptions

**Chosen:** Astra’s full Q2 answer.

**Reason:** The actual split is:

- Wall rounding: whole seconds versus Opus’s five-second increments.
- Common per-test limit: **p95 of per-test baseline maxima × 1.5**, rounded to 10 ms, versus **pooled p99 × 1.25**, rounded to 100 ms and capped at 500 ms.
- Exceptions: owned remedies and expiring review deadlines versus mandatory downward ratcheting and exception-count controls.

Astra’s calculation first preserves each test’s worst observed baseline duration, then summarizes the test population. This avoids treating pooled repeated observations as runtime-confidence evidence. Its named exceptions retain bounded ceilings while making remediation accountable through ownership and deadlines.

Checked `guardrail_test_report.py:18–24`: durations include execution phases. Checked `quality.py:155–156`: today’s output retains only ten slowest durations and stops on first failure. Neither is sufficient baseline evidence for either proposed formula; complete normalized reports are necessary.

The multipliers and percentile remain provisional engineering choices, not empirically validated reliability guarantees.

## Q3 — Storage, evidence and verifiable approval

**Chosen:** Astra’s full Q3 answer.

**Reason:** Astra’s tracked directory separates policy, complete baseline evidence and externally verifiable approvals. Opus’s single-file summaries plus artifact-only full reports are weaker for reproducibility: per-run percentile summaries and cases over 100 ms cannot generally reconstruct the pooled distribution needed by its formula.

Checked draft defects substantiate Astra’s controls:

- `check-test-budgets.py:23–24` trusts a committed `ownerApproved` Boolean.
- Its checker does not verify execution phases (`:39–60`).
- `run-budgeted-fast.py:16–20` uses shared fixed report paths.
- The plugin initializes collected cases at zero (`guardrail_test_report.py:11–15`), making completion validation essential.
- `.omp/gate.json:3–6` identifies both enforcement entrypoints; protecting policy alone would leave the enforcement path mutable.

The supplied host fact corrects Opus’s protection claim: an active default-branch ruleset requires `gate` without bypass, but **does not require code-owner review**. Required status therefore does not authenticate owner approval. Astra explicitly requires protected review metadata or an externally anchored owner signature, bound to the exact policy change.

## Q4 — Agreement on the prospective goal

**Chosen:** Astra’s full Q4 answer.

**Reason:** Both voters agree: retain the prospective 30-second target separately, display the gap, and do not make it today’s blocking limit.

Checked `test-tiers.md:10,17–22`: the target is **under 30 seconds**, explicitly prospective. Astra preserves that wording and deliberate tightening without Opus’s additional automatic-goal-transition rule.

A checked [successful CI run](https://github.com/adam-jackson-cf/raidar/actions/runs/42750145146) reports 5,641 fast cases passing in 78.26 seconds. That establishes current debt, not a calibrated outer-wall limit.

## Q5 — Risks and mitigations

**Chosen:** Astra’s full Q5 answer.

**Reason:** Astra’s risk treatment matches the adopted design: weak five-run variability evidence, mutable runners, fixture attribution, accountable exceptions, complete inventories and approval for legitimate aggregate growth.

Checked facts particularly support its completion risks: the current fast command uses `-x`, and the draft collector initially records unexecuted collected cases with zero duration. Its warning about incomplete evidence is therefore concrete.

Opus additionally proposes refusing baselines when every run shares one CPU model. Neither checked repository policy nor the observed runner evidence establishes CPU diversity as necessary or sufficient calibration, so I do not adopt that requirement.

One factual correction does not change Astra’s selected policy: CI timing is no longer wholly unknown. The checked run reports 78.26 seconds and a slowest displayed call phase of 1.44 seconds. These logs still lack the complete, comparable baseline reports required to calculate adopted limits.

## Adopted decision

- **Q1 — Astra:** CI-only timing enforcement; advisory hook timings; comparable same-revision baselines and a versioned measurement profile.
- **Q2 — Astra:** Its complete formulas and named exceptions with owners, remedies, deadlines and approved increases.
- **Q3 — Astra:** Tracked policy/evidence/approval directory; reproducible calculations, complete invocation-bound reports and externally verified approval.
- **Q4 — Astra:** Under-30-second tracked goal, separate from today’s calibrated blocking limit.
- **Q5 — Astra:** Its complete residual-risk policy, with the historical CI-unknown statement corrected by checked evidence.

These picks are coherent: all five select Astra’s full answers. No formula, exception mechanism or storage requirement is spliced from Opus.