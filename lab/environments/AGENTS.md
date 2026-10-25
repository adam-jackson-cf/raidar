# Runtime pack recipes

- Keep agent images free of scorer tools; pin agent versions to the pack.
- Do not introduce another runtime pack before Phase 1 process sign-off.
- An image release changes the image tag: image/ content bytes (excluding ignored editor/OS files),
  pack id/version/python or parsed image fields other than the context path.
  Task bindings and entry_file_template do not change the image tag; review their problem
  contract impact separately. Follow the raidar-release skill for validation and relocking.
- Preserve the test holding deferred image planning-tag removal to the next pack release.
