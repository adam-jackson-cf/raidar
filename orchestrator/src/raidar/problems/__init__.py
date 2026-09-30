"""Problems: pinned dataset import, oracle validation and one-shot support (plan WS-B).

Entry points for other workstreams:

- ``raidar.problems.store.ProblemStore.load(ProblemId)`` - a digest-verified
  imported problem: agent-visible Harbor ``task_dir`` plus host-only oracle
  ``bundle`` (pass ``bundle.root`` as the verifier's ``oracle_bundle``).
- ``raidar.problems.rewrite.ORACLE_VERIFIER_IMPORT_PATH`` - the verifier import path.
- ``raidar.problems.resolver.ProblemResolver`` - lock resolver for kind ``problem``.
- ``raidar.problems.validation`` - the oracle validation checks and report.
"""
