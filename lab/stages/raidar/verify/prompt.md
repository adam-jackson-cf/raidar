## Verification

Your work is graded from the workspace exactly as you leave it.

Commit with `git commit` after each logical step, starting as soon as your
first test passes. The repository's pre-commit hook is the verify gate: every
commit runs it over the whole tree, so it checks your work as you go. A refused
commit lists what to fix: fix that and commit again until it passes. Never skip
the hook (`--no-verify`, `-n`), change git's hook settings, or run
`git reset --hard`, `git clean` or `git restore`. Check `git status` and
`git diff` before `git add -A`; use Conventional Commit messages
(`feat|fix|refactor|test|docs|chore: <summary>`).

Implementation work is not complete until a commit passes the gate. Do not end
your turn while a commit is refused: a refused commit means the work is
unfinished. When you stop, the process that runs you checks whatever is left
through the same gate and records its verdict. It never commits or restores
your work. Where the workflow provides fix turns, a refusal comes back to you
with the gate's output.

The gate's rules are in your instructions: what blocks a commit and what only
warns. It runs ruff, ruff's formatter check, strict mypy
and your tests with pytest: at least one test must exist and none may fail, and
a test that takes longer than %%%TEST_TIMEOUT%%% s fails. Keep each test
fast, and independent of each other and of shared state. Declare every
third-party package your production code imports in the project's dependency
manifest. Keep data your code or tests write while running out of the commit:
write it to a temporary or configured location, or have the project's
`.gitignore` exclude it.
