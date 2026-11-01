## Verify gate rules

The repository's pre-commit hook runs these checks over the whole work tree
(every Python file outside virtualenvs, caches and `.git`) on every `git commit`, and the same
checks run again, and are recorded, when you stop. A commit is refused while a blocking check
fails; a warning check only reports.

- `lint` (lint, blocking, ruff==0.8.6): `ruff check`
- `format` (format, blocking, ruff==0.8.6): `ruff format --check`
- `types` (types, blocking, mypy==2.3.1): `mypy --strict`
- `tests` (tests, blocking, pytest==9.1.1): pytest: at least one test must be collected and none may fail; a test running longer than the time limit in your verification instructions fails
