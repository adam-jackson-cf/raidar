# python-guardrails@1: evidence for not-applicable rule IDs

Approved by the user at step 5 of the guardrails setup, 2026-10-04 (all 16 below).

Target: the agent's starting workspace (/app) for Python CLI problems on the Raidar
`python` runtime pack. The workspace starts empty (shared starter framing: "The
directory may start empty"); the agent lays out the project; the problem fixes only the
entry file (`orchestrator/src/raidar/packs/python/pack.yaml`:
`entry_file_template: "{entry_file}.py"`, at the workspace root).

- NA:PYQ100 Maintained roots. The entry file must sit at the workspace root, so the only
  possible maintained root is the root itself; the gate's scope (every non-excluded .py
  under /app) already is that root. A narrower root list would encode one program's layout.
- NA:PYQ101 Script-name allowlist. Enaible's `scripts/` allowlist names Enaible's own
  scripts. The workspace has no `scripts/` convention, and the entry file's name is set by
  the problem specification, not the repository.
- NA:PYQ106 Skill directories. Skills components install into the harness home
  (`~/.agents`, `~/.claude`), never into /app; the workspace has no skills directory.
- NA:PYQ210 NA:PYQ211 Analyzer base classes and project entrypoint. Enaible's
  `shared/analyzers/` subsystem; no counterpart in an agent workspace.
- NA:PYQ220 Subprocess seam-wrapper map. The rule needs a target-owned map of approved
  wrapper functions per file. A greenfield workspace has none, and the pack cannot author
  one without encoding a specific program's architecture (forbidden for components).
- NA:PYQ221 Dynamic imports in adapter modules. Enaible `workflows/analyzer_skills/` paths.
- NA:PYQ230 NA:PYQ231 Contract callables / Any boundaries. Enaible's
  `workflows/analyzer_skills/contracts.py` only.
- NA:PYS251 NA:PYS252 NA:PYS253 Analyzer-like code I/O, subprocess/env and ownership
  marker. Scoped to Enaible's `shared/analyzers/`; CLI programs legitimately read files and
  the environment in their own entry code.
- NA:PARITY.dependency-inventory Declared optional analyzer dependencies versus the
  installer. Enaible's analyzer installer; the workspace has no installer.
- NA:PARITY.install-guidance Documented install commands versus the installer. The
  workspace ships no installer or install documentation contract.
- NA:SEAM.evidence Workflow seam registry/manifest evidence. The workspace has no workflow
  orchestration registry.
- NA:SKILL.metadata Agent skill metadata. No skills exist in the workspace (see PYQ106).
