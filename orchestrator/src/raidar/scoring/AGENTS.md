# Scoring

- A scorer implementation change, including its imports, requires relocking every tracked experiment.
  Semantic changes to `framework/metrics.py` do too.
- Keep scorers offline: write Assessments outside the job and never rerun agents.
- Use process-compliance@7 for process assessment; decide from deterministic trace evidence only.
  An undecidable check is `insufficient_evidence` with an `undetermined: ...` reason, never a numeric zero.
