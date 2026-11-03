Decision: astra (high confidence) — take Astra’s full answer: retain initializers in the closure; move distribution-version metadata out of executable initialization, avoiding exclusion rules.
Review the identity cutover separately; land `1.1.0`, then the split; revalidate/relock once before resuming trials. Intermediate trials require intermediate revalidation.
Batching minimizes required revalidations; Opus also promises one, but relies on maintaining initializer exemptions and unchanged closure bytes.
Secrets claim verified: `sandbox.py:48,241,357–358` imports and executes `raidar.judge.secrets`; read-only digest execution confirmed its source is excluded.
