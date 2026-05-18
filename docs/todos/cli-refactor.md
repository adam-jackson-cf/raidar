# Refactor CLI Repo-State Helpers

## Summary

Move the git-state and generated-artifact guard logic out of `raidar.cli` into a dedicated application module, then update CLI tests to target the new owner. Preserve every public `raidar quality gates` behavior and avoid compatibility wrappers for the old private helper names.

## Key Changes

- Create a new internal module, `raidar.application.repo_state`, for repository cleanliness and artifact guard behavior:
  - Move git path/status command helpers into this module.
  - Move generated artifact filtering and guard logic into this module.
  - Keep `ARTIFACT_CHANGE_PREFIXES = ("experiments/",)` with the moved guard logic.

- Update `raidar.cli` to use the new module:
  - Replace direct calls to `_has_unstaged_changes` and `_assert_no_generated_artifact_changes` with `repo_state.has_unstaged_changes(REPO_ROOT)` and `repo_state.assert_no_generated_artifact_changes(REPO_ROOT)`.
  - Remove the moved private helper definitions from `cli.py`.
  - Keep `_run_or_raise` in `cli.py` because it is still CLI command execution plumbing used by quality gates and tests.

- Update tests to make the new ownership canonical:
  - Import `assert_no_generated_artifact_changes` and `generated_artifact_paths` from `raidar.application.repo_state`.
  - Patch `raidar.application.repo_state.changed_repo_entries` in artifact guard tests.
  - Patch `raidar.cli.repo_state.assert_no_generated_artifact_changes` and `raidar.cli.repo_state.has_unstaged_changes` in CLI command tests that exercise `quality_gates`.

## Interfaces

- Public CLI interface: unchanged.
- New internal application interface:
  - `repo_paths_from_git_cmd(args: list[str]) -> list[str]`
  - `repo_name_status_from_git_cmd(args: list[str]) -> list[tuple[str, str]]`
  - `changed_repo_paths(repo_root: Path) -> list[str]`
  - `generated_artifact_paths(paths: list[str]) -> list[str]`
  - `changed_repo_entries(repo_root: Path) -> list[tuple[str, str]]`
  - `assert_no_generated_artifact_changes(repo_root: Path) -> None`
  - `has_unstaged_changes(repo_root: Path) -> bool`
- Error type remains `click.ClickException` so user-facing CLI failure text and test expectations stay unchanged.

## Test Plan

- Focused tests:
  - `uv run pytest tests/test_cli_commands.py -x --tb=short` from `orchestrator/`
  - `uv run pytest tests/test_experiment.py -x --tb=short` from `orchestrator/`

- Static checks:
  - `uv run ruff format --check --force-exclude` from `orchestrator/`
  - `uv run ruff check . --no-fix --force-exclude` from `orchestrator/`
  - `uv run --project orchestrator lizard -C 10 -L 50 orchestrator/src/raidar/cli.py orchestrator/src/raidar/application/repo_state.py`

- Final gate:
  - `make quality`

## Assumptions

- The moved helpers are private implementation details, so private imports from `raidar.cli` in tests should be updated rather than preserved.
- No behavior changes are intended for generated artifact detection: deletions under `experiments/` remain allowed, modified or added generated artifacts remain rejected.
- Existing dirty user-owned files must not be reverted or reformatted unless directly touched by this refactor.
