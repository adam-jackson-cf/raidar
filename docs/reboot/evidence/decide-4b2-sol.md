Q1: astra (med) — No default; pre-register `analysis.max_exclusion_difference = d` in each experiment lock before collection.
  Refuse comparative verdicts when max(arm exclusion rates) − min(arm exclusion rates) > d; equality passes this gate.
  Rate = excluded/planned slots across all causes, counting each excluded slot once; retain the independent 20% paired-exclusion cap.
  Verified at HEAD `26cb23e`: `schema.py:246–249` defaults to None, permits [0,1]; `effects.py:516–544,590–592` refuses unset bounds.
  All 10 experiment locks contain null: descriptive-only reporting, with comparative verdict `insufficient`, not `inconclusive`.
  §6 requires material-difference refusal; §7.2 and phase 9 require controls before citing comparisons, not a universal 0.10 threshold.
  Phase-9 controls therefore need explicit bounds; missing bounds cannot satisfy their acceptance criteria.
  Opus correctly identifies permissive 1.0 locks, but 0.10 lacks calibration and limits the rate gap—not either arm to half the exclusion cap.
