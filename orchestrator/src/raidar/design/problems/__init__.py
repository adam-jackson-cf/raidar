"""Problems: pinned dataset import, verifier validation and one-shot support (plan WS-B).

Entry points for other workstreams:

- ``raidar.design.problems.store.ProblemStore.load(ProblemId)`` - a digest-verified
  imported problem: agent-visible Harbor ``task_dir`` plus host-only oracle
  ``bundle`` (pass ``bundle.root`` as the verifier's ``oracle_bundle``).
- ``raidar.design.problems.rewrite.VERIFIER_IMPORT_PATH`` - the verifier import path.
- ``raidar.design.problems.resolver.ProblemResolver`` - lock resolver for kind ``problem``.
- ``raidar.design.problems.validation`` - the verifier validation checks and report.
"""
