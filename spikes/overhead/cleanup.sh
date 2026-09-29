#!/usr/bin/env bash
# Remove per-trial env images left by the overhead bench (warm cells keep them:
# Harbor tags one image per trial, sharing layers). OrbStack only.
set -euo pipefail
docker --context orbstack images --format '{{.Repository}}:{{.Tag}}' \
  | grep -E '^ovh-(warm|warm-artifact|warm-init|cold)-[0-9]+-[0-9t]+__env-main:' \
  | xargs -r docker --context orbstack image rm -f
