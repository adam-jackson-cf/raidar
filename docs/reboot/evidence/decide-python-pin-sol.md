Choice: astra
Reason: Project-local pin plus quality guard addresses machine readiness; runtime_gate.py already rejects mismatched validation digests through RT7.
Risk: Explicit interpreter overrides can still create validations under another Python; quality checks do not guard those writes.
