Decision: a (high). Add `orchestrator/.python-version` containing `3.13.7`, and keep the root `.python-version` at `3.13.7`. The root file is still read by root-level `uv python find` and `uv run`, and `scripts/checks/check-structure.py` lists it in `ROOT_FILES`. Add a `make quality` check under `scripts/checks/`, outside `raidar.grading`. It fails unless the two files are byte-equal and the orchestrator venv's `platform.python_version()` equals them.

uv 0.13.7, run today: at the root, `uv python find` → 3.13.7. With `--project orchestrator`, `--directory orchestrator` or `--system` it → 3.14.10. With `UV_PYTHON=3.13.7 … --project orchestrator` it → 3.13.7. uv's `.python-version` search stops at the project and never reaches the root file. [INFERENCE] uv will read `orchestrator/.python-version`; I did not test it because the brief forbids creating files.

Not (b): passing the pin from the Makefile covers only make targets. README's `uv sync`, direct `uv run --project orchestrator`, IDEs and hooks would still pick 3.14.10, and the pin would live in two places.

Not (c): `requires-python = "==3.13.7"` changes `orchestrator/uv.lock` (line 3 is `requires-python = ">=3.12"`) and may re-resolve it. It reverses the CHANGELOG entry "retaining `requires-python >=3.12`". `[tool.uv]` has no interpreter-pin key. `contract/pyproject.toml` keeps its own `>=3.12`, so the `raidar-contract` range does not change.

Identity impact of (a) plus the check: none. The digest hashes only `raidar.grading` import-closure sources, runtime files and `_host_runtime()` (`grading/identity.py:163-178`). `.python-version`, `pyproject.toml` and `scripts/` are not inputs, so `a93cc5f7…` stays and no relock is needed.

Runtime guard: worth adding as a preflight before problem-validation and lock writes. RT7 already refuses comparisons under the wrong Python. The remaining risk is a new validation or lock record that pins a 3.14 digest. Put the guard in the CLI or lock layer and have it read the pin; that is not an identity change. Placed anywhere under `raidar/grading/**`, it changes closure bytes, so the digest changes and all nine problems need revalidation plus a full relock.

Verification:
1. With 3.14.10 still installed, `uv python find --project orchestrator` → the 3.13.7 path.
2. After `make experiment-lock`, the venv reports 3.13.7 and imports `tree_sitter`.
3. In a scratch copy, set the orchestrator pin to 3.14.10; the quality check must go red.
4. The recomputed digest is still `a93cc5f7…`.
5. `env -u AGENT make quality` passes.

Handoff step 3 text: "Run `uv python install 3.13.7`, then `env -u AGENT uv sync --project orchestrator --all-extras --frozen`; uv reads `orchestrator/.python-version`. Check that `uv run --project orchestrator python -V` prints `Python 3.13.7`. Never pass another `--python`; installing a newer Python no longer moves the venv."

On this host, `orchestrator/.venv` was rebuilt on 3.14.10 without the extras. Re-sync it with the step 3 command once this change lands.
