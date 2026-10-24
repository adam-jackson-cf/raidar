# Working agreement

- Read the whole specification before changing code, and re-read it when a new
  checkpoint adds requirements.
- Keep the existing public interface working; extend it rather than rewriting it.
- Prefer small, cohesive modules. Split a function once it handles more than one
  concern.
- Do not duplicate logic: factor shared behaviour into one place and reuse it.
- Write or update tests for every behaviour you add, and run them before you finish.
- Remove dead code and debugging output before you finish.
