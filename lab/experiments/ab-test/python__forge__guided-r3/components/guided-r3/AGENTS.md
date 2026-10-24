# Working agreement

## Plan first

- Read the whole specification before changing code, and re-read it when a new
  checkpoint adds requirements.
- Before your first source edit in each checkpoint, write `PLAN.md` at the
  workspace root as its own write, before any other file change. List what is
  new compared with the code already in the workspace, where each change
  belongs, and which earlier behaviour it could affect. Replace the previous
  checkpoint's plan; keep it short.

## Change

- Keep the existing public interface working; extend it rather than rewriting it.
- When a checkpoint changes something every command shares (the output shape,
  the error format, a global flag), apply it to every command, old and new.
- Every failure path must follow the error contract the specification defines,
  including bad or missing arguments, unknown commands and conflicting flags.
  Do not let an argument-parsing library print its own usage text or choose its
  own exit code.
- Prefer small, cohesive modules. Factor shared behaviour into one place and
  reuse it.

## Verify

- `pytest` and `ruff` are not installed in this environment. Write tests with
  the standard library `unittest` and run the whole suite with
  `python3 -m unittest discover` after your last edit (a test directory needs an
  `__init__.py` to be discovered). Do not install packages to get other test
  tools.
- Run the tests for behaviour from earlier checkpoints too, and fix every
  failure before you finish.
- Remove dead code, debugging output and scratch files before you finish. Keep
  `PLAN.md`.
