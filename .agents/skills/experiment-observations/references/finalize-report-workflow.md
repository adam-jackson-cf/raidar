# Create and finalise the observations report

1. Write a new, never-overwritten report at
   `analysis/<experiment-id>/observations-<NNN>.md` (three digits, one above the
   highest existing; recheck if the path exists). Write elsewhere only when the
   user names a path.
2. Make the report evidence-first. Include: the question and the cohort (jobs,
   lock, arms, attempts, exclusions); provenance (lock digest, problem pin,
   agent, model, effort, condition digests); per-arm outcomes; the analysis
   verdicts as stated in `analysis.json`; quality components; process and
   manipulation-check evidence; per-checkpoint evidence; reconciled fault
   entries; limitations; and a measured interpretation that separates
   observation from inference.
3. Keep `Reached` (execution coverage) distinct from `Solved` (every graded case
   passed, regressions included). One failing case keeps a checkpoint unsolved
   however high its case pass rate.
4. Do not include recommendations or model-selection advice.
5. Return the concise view using exactly this template:

   - `- Cohort: <jobs, lock digest, valid trials per arm, exclusions>.`
   - `- Solved: <a checkpoint is solved only when every graded case, regressions included, passed>.`
   - `- Case pass rate: <passed / graded cases at a checkpoint, averaged over the arm's trials>.`
   - `- Verdicts: <taken from analysis.json under the lock's thresholds; recomputed values are checks>.`

   `Outcomes`

   | Arm | Valid trials | Mean checkpoints solved | Solve rate | Final solved | Mean C | Headline S | Mean cost (nominal) | Mean agent s |
   | --- | --- | --- | --- | --- | --- | --- | --- | --- |
   | `<arm>` | `<n/planned>` | `<mean>` | `<value>` | `<k/n>` | `<value>` | `<value or Unavailable>` | `<USD>` | `<s>` |

   `Effects (treatment vs <baseline arm>)`

   | Metric | View | n | Δ [95% CI] | Verdict |
   | --- | --- | --- | --- | --- |
   | `<metric>` | `<view>` | `<pairs>` | `<delta [lo, hi]>` | `<verdict from analysis.json>` |

   `Manipulation check`

   | Check | Baseline share | Treatment share | p | Verdict |
   | --- | --- | --- | --- | --- |

   `Observed fault types`

   - `<canonical ID> — <exact fault type> (<category>; <owner>; <status>): <arms/checkpoints>; <measured evidence>; [record](<record link>)`

   `Full report`

   - `[observations-<NNN>.md](analysis/<experiment-id>/observations-<NNN>.md)`

6. Fill every placeholder from Step 1 and Step 2. Write `- None observed.` under
   `Observed fault types` when there are none. Add no other section.
