"""Frozen location of ``snapshot.py`` (schema v2 vocabulary: the hidden grader is the *verifier*).

The verifier lives in ``raidar.verifier``. ``raidar/oracle/snapshot.py`` stays at
this path, byte for byte, because it is a fingerprint source of the released
``review-loop@1`` and ``review-loop@2`` components: moving or editing it would
change their component digests and so the execution identity (``lock_sha256``)
of every experiment that uses them.
"""
