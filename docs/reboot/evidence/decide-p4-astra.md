Q1: (b) (high) — Remove the privileged hook broker; bypass is method behaviour, not grounds for selectively excluding agent outcomes.
The seal independently reruns checks and records evidence host-side (`verify.py:_seal`, `primitive/gate.py:GateRun.seal`).
Amendment: “Only hook-derived evidence becomes non-invalidating. Preserve independently measured tool integrity—currently G3—and authoritative seal/canary/transport failures as validity checks.”
Also demote hook-log requirements in W5 and hook-derived W11 findings; changing G1–G3 alone leaves invalidation paths (`health/evidence.py`).
Retain seal record-channel protection (`gate_tool.py:_protect_record_channel`); broker removal must not remove it.
The brief’s `verify_gate.py` has moved to `subject/primitive/gate_tool.py`, imported by both gate implementations.
Tradeoff: commit-time compliance becomes untrusted telemetry; §5.3 canary attribution remains authoritative.

Q2: (b) (high) — One explicit namespace-to-directory mapping avoids upstream special cases and competing identities for the same flat folder.
`package/registry.py:stage_folder` already maps `release.name` directly beneath `lab/stages/`; namespaced references therefore already follow this layout.
Namespace refusal is a proposed breaking change: today `contract/releases.py:RELEASE_PATTERN` and `stage/1` permit unnamespaced versioned references.
Amendment: “Require namespaces for stage references only; leave agent/workflow reference syntax unchanged.”
Adoption must update §5.6 and shipped-folder examples, the public contract, stage authoring/discovery, references and affected locks together, without aliases.
This deliberately revises the flat upstream design rather than treating phase 4’s implementation as already compliant.
