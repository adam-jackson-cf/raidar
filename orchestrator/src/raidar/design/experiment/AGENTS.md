# Experiment contract changes

- Semantic changes here require relocking every tracked experiment in the same change.
  The toolchain digest also covers the external modules listed in `SPEC_EXTERNAL_MODULES`.
- Preserve the single experiment authoring unit; do not reintroduce scenario, revision or matrix files.
