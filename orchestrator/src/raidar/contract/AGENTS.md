# Shared contracts

- Semantic changes to canonical.py, references.py or source_digest.py require relocking every tracked experiment.
- Preserve digested snapshot sources, observation subject `trajectory`, evidence input `oracle.cases`,
  and released verifier evidence names; changing hashed bytes requires a release and relock.
