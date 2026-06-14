Implement the alert escalation feature from a retained plan.

Before changing code, create `intentplan.md` at the workspace root. It must
include a `Feature Dashboard` table and an `Acceptance Tracker` table. Each row
must include a status column and an evidence, reference, command, or surface
column that can be filled in after implementation.

Build `src/alerts/escalation.py` and export `route_alert(alert)`. The function
receives a dictionary with `severity`, `owner`, and optional `source` fields and
returns a routing dictionary:

- severity `critical` routes to channel `pager` with priority `p1`
- severity `warning` routes to channel `ops` with priority `p2`
- all other severities route to channel `triage` with priority `p3`
- missing or blank owners become `unassigned`

Add tests in `tests/test_escalation.py` for critical routing, warning routing,
default routing, and missing owner fallback. Update the plan tables with passed
statuses and concrete evidence after verification.

Run all required verification commands before completion and report only after
they pass.
