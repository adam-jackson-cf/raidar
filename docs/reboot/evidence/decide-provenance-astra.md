Decision: a (high) — Both importers require `SRC`, `NAME`, `SHA256` and nonblank `SOURCE`; record `{sha256, source}` in `lab/imports.yaml`, preserving `SOURCE` verbatim as unverified provenance.
Identity remains digest-only: `stage-tree/1` for stages; the resolved definition digest, including released dependencies, for workflows.
`releases-check` verifies the recorded digest against imported content and the applicable release entry; it checks provenance presence, not truth, repository availability or commit correspondence.
Non-Git, no-origin and dirty sources are accepted, including for non-DRAFT releases, subject to existing release gates; no Git inspection or clean-tree requirement.
Missing or blank `SOURCE` is refused; an explicit description such as `unknown origin` is permitted without inventing repository or commit evidence.
Existing safety, namespace, overwrite, dependency and smoke requirements remain unchanged.
This preserves §5.4/§5.6 byte-based, transport-independent identity and FXL’s folder-plus-digest boundary without introducing an exporter metadata contract.
