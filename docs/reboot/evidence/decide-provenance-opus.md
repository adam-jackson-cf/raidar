Decision: a (medium-high). The rule: `make stage-import|workflow-import SRC=<dir> NAME=<ns>/<name> SHA256=<digest> SOURCE=<text>`. `SOURCE` is required and must be one non-empty line; the recommended form is `<repo>@<commit>:<path>`. Raidar stores it unchanged in `lab/imports.yaml` as `{name, sha256, source}`, one entry per imported digest, and never checks it. Identity comes from the bytes alone. `source` is not part of the release, the lock, pooling or admission, so changing it can't change any result's identity.
Why not (c): the export is a separate folder, not a checkout, so Git info read from `SRC` would describe the wrong repository or none. It would also tie importing to Git, against §5.4/§5.6 ("The digest does not depend on the transport").
Why not (b): `export.json` would be a second file format both projects have to version, but the FXL pivot says they "meet only at a package folder and its `stage-tree/1` digest" (§5). Its `sha256` repeats `SHA256=`, and its `dirty` flag is the exporter's own claim, which Raidar can't check.
`releases-check` checks digests, not provenance, in three ways:
- Every folder under a non-`raidar` namespace in `lab/stages/` and `lab/workflows/` has an entry whose `sha256` matches the folder's computed `stage-tree/1` or definition digest.
- Every registered release of an imported name has an entry with the same digest.
- Every entry's `source` is a non-empty single line. Its content is never resolved or checked against any repository.
Non-Git, no-origin and dirty sources are all accepted, because Raidar never looks at the source's version control and the committed bytes alone make runs reproducible. The importer writes what is true, for example `fxl@<commit>+dirty:<path>` or `local:<description>`. The design should state that provenance is unverified.
Refusals:
- Missing or multi-line `SOURCE`.
- Re-importing a recorded digest with a different `SOURCE`; each digest has one entry.
- The existing R5 refusals: the `raidar/` namespace, a digest mismatch, and different bytes under an existing release.
Recommended for FXL, not enforced by Raidar: `package export` prints a ready-made `SOURCE` line and refuses dirty trees for anything meant for measurement.
