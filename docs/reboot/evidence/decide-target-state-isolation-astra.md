Decision: a (high confidence); local isolation fixes the failure without changing every fast test.
Required: `orchestrator/tests/test_target_selection_and_trust.py::clean_environment` — add `monkeypatch.setenv("RAIDAR_STATE_DIR", str(tmp_path / "run")))` (remove the final extra `)`).
Other gap: `orchestrator/tests/test_target_tunnel_and_ensure.py::target_cli` lacks isolation, but current callers mock transport or avoid state access.
No other exposed fast-test path found; tunnel tests otherwise isolate paths, and guard tests mock transport.
Verification: read-only `make target-list` smoke passed with isolated state and subprocess launches forbidden; selected `mbp`, PID null, alive false.
No files edited. After implementation, run `make quality` with the real tunnel up; not rerun here.
