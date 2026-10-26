# Experiment definitions

- Author one schema_version=3 experiment.yaml under `<kind>/<runtime>__<subject>__<intent>/`.
- An arm is an agent release plus a workflow release: `agents[]` names registered agent releases
  (`lab/agents/<name>.yaml`) and `arms[]` pairs an agent with an optional `workflow: <name>@N`
  (default `one-shot@1`; workflows live in `lab/workflows/<name>/workflow.yaml`).
- Add variants as arms in the existing experiment.
  Create a new experiment only for a new question: different problems, baseline arm or analysis plan.
- Keep arms different only in their agent or workflow release.
- Reference only releases registered in `lab/releases.yaml`; cut a new release instead of editing a
  released agent or workflow in place.
- Relock after changing an experiment, a referenced release or a problem pin; never hand-edit a lock.
