# Package boundaries

- Follow `orchestrator/.importlinter` and `scripts/checks/check-structure.py` as the enforced boundary definitions.
- Import only downward: `cli > evaluation > engine > design > scoring > grading > subject > contract`.
- Within evaluation: `review/{projection,quality} > analysis`; ledger is independent.
- Within engine: `job > trial > health > perf > target`.
- Within design: experiment and problem are independent.
- Within scoring: `catalog > individual scorers > framework`; scorers never import each other.
- Within subject: `treatment/{definition,runtime,catalog} > agent`; environment is independent.
- Scoring never imports subject.agent or subject.treatment; grading never imports subject.
  Design.problem never imports subject.agent; scoring.process_checks never imports scoring.judge.
- Move shared code into the lowest tier both consumers may import.
- Keep packages at most three levels below this directory, within the declared group/subgroup allowlists.
- Keep runtime data in `lab/treatments/`, `lab/environments/` or `lab/judge/`,
  addressed through explicit paths in `contract/paths.py`.
  Container-shipped code (grader plugin, mcp_shim.py, step_reset.sh) stays in the package.
- Edit each primitive in place; no version in file, module, class or folder names.
  Versions are manifest data (`name@N`); archived releases live in tags.
- Keep Jev dormant under the policy constraints in `lab/judge/AGENTS.md`.
