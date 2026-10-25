# Experiment definitions

- Author one schema_version=2 experiment.yaml under `<kind>/<runtime>__<subject>__<intent>/`.
- Add treatment variants as conditions and arms in the existing experiment.
  Create a new experiment only for a new question: different problems, baseline arm or analysis plan.
- Keep arms different only in their declared factors.
- Relock after changing an experiment, component payload or problem pin; never hand-edit a lock.
- Treat components/ files as agent-delivered treatment content, not repository operating instructions.
- Hand-edited immutable component payloads may use v<N>/ folders; definitions and code may not.
