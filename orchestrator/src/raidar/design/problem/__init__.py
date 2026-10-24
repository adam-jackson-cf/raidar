"""Problems: pinned dataset import, verifier validation and one-shot support.

Entry points:

- ``raidar.design.problem.store.ProblemStore.load(ProblemId)`` - a digest-verified
  imported problem: agent-visible Harbor ``task_dir`` plus host-only oracle
  ``bundle`` (pass ``bundle.root`` as the verifier's ``oracle_bundle``).
- ``raidar.design.problem.rewrite.VERIFIER_IMPORT_PATH`` - the verifier import path.
- ``raidar.design.problem.resolver.ProblemResolver`` - lock resolver for kind ``problem``.
- ``raidar.design.problem.validation`` - the verifier validation checks and report.
"""
