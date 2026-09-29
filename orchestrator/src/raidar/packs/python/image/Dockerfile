# Raidar Python pack agent image (reboot plan WS-I I1).
# Toolchain + harness binaries only. No scorer tools and no browsers: those are
# provisioned lazily on the host or run in the eval container (tools.lock.yaml).
# Built by `raidar pack build python`, which passes every ARG from pack.yaml and
# tags the result by the pack image lock hash.
#
# Differences from the SCB python-light problem environment
# (scb_to_harbor_lib/templates.py DOCKERFILE_LIGHT), all deliberate:
# - adds nodejs + npm and the pinned harness CLIs (so no per-trial install);
# - omits docker-cli, the compose plugin and the docker group. SCB specs that
#   drive docker (e.g. recli checkpoints 6-8) need a derived image; the pilot
#   problems (forge, circuit_eval) do not use docker.
# apt's npm pulls in Debian python3 (via node-gyp) at /usr/bin/python3. The
# default python3 on PATH stays the base image's /usr/local/bin/python3 (the
# pack's declared 3.12), and the audit probes every python3 interpreter.
# apt and npm transitive versions resolve at build time; the build records the
# image ID and dpkg/npm manifest digests instead of locking them.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG CODEX_CLI_NPM_PACKAGE
ARG CODEX_CLI_VERSION
ARG CLAUDE_CODE_NPM_PACKAGE
ARG CLAUDE_CODE_VERSION

ENV DEBIAN_FRONTEND=noninteractive
ENV HOME=/tmp/agent_home
ENV UV_CACHE_DIR=/tmp/uv-cache
ENV PIP_CACHE_DIR=/tmp/pip-cache
ENV PYTHONUNBUFFERED=1
ENV TERM=xterm
ENV COLUMNS=240
ENV LINES=60

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential \
      ca-certificates \
      curl \
      git \
      procps \
      util-linux \
      nodejs \
      npm \
 && rm -rf /var/lib/apt/lists/*

RUN npm install -g --no-fund --no-audit \
      "${CODEX_CLI_NPM_PACKAGE}@${CODEX_CLI_VERSION}" \
      "${CLAUDE_CODE_NPM_PACKAGE}@${CLAUDE_CODE_VERSION}" \
 && npm cache clean --force

RUN groupadd -g 1000 agent \
 && useradd -m -u 1000 -g 1000 -d "$HOME" -s /bin/bash agent \
 && mkdir -p /app /assets /workspace "$HOME" "$UV_CACHE_DIR" "$PIP_CACHE_DIR" \
      /opt/raidar/harness-baseline/codex-home /opt/raidar/harness-baseline/claude-config \
 && chown -R agent:agent /app /workspace "$HOME" "$UV_CACHE_DIR" "$PIP_CACHE_DIR" \
 && chmod 1777 /tmp "$UV_CACHE_DIR" "$PIP_CACHE_DIR" \
 && chmod -R a+rX /opt/raidar

WORKDIR /app
