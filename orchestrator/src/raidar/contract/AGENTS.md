# Shared contracts

- Semantic changes to canonical.py, references.py or source_digest.py require relocking every tracked experiment.
- Preserve identity-bearing snapshot sources, observation subject `trajectory`, evidence input `oracle.cases`,
  and released verifier evidence names; changing hashed identities requires a release.
