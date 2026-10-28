# Package boundaries

- Follow `orchestrator/.importlinter` and `scripts/checks/check-structure.py` as the enforced boundary definitions.
- Import only downward: `cli > evaluation > engine > design > scoring > grading > subject > judge > contract`
  (`orchestrator/.importlinter` is authoritative).
- Within evaluation: `review/{projection,quality} > analysis`; ledger is independent.
- Within engine: `job > trial > health > perf > target`.
- Within design: experiment and problem are independent.
- Within scoring: `catalog > individual scorers > framework`; scorers never import each other.
- Within subject: `workflow > primitive | agent > package`; environment is independent.
  Agent-specific code stays private under `subject/agent/adapters/`; consumers use the
  native-agent protocol, including credentials and state paths.
- Health reads records only, never subject workflow/primitive/package code. Scoring never
  imports agent, workflow, primitive or package modules; grading never imports subject.
  Design.problem never imports subject.agent; scoring.process_checks never imports scoring.judge.
- Move shared code into the lowest tier both consumers may import.
- Keep packages at most three levels below this directory, within the declared group/subgroup allowlists.
- Keep runtime data in `lab/agents/`, `lab/workflows/`, `lab/stages/`, `lab/environments/` or
  `lab/judge/`, addressed through explicit paths in `contract/paths.py`.
  Container-shipped code (grader plugin, step_reset.sh) stays in the package.
- Stage behaviour enters a trial only through a Raidar primitive (`stage/1`):
  method logic belongs under `lab/stages/`, never in host imports or execution.
  Container calls in workflows and primitives go through `subject/primitive/calls.py`.
- Edit each primitive in place; no version in file, module, class or folder names.
  Versions are manifest data (`name@N`); archived releases live in tags.
- The Jev scoring policies stay dormant under `lab/judge/AGENTS.md`; the workflow judge
  (`jev-judge/1`) is live.
