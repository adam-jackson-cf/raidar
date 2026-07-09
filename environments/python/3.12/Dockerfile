FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
  git \
  tar \
  gzip \
  && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir \
  "coverage>=7" \
  "lizard>=1.17" \
  "pytest>=9" \
  "ruff>=0.14"

COPY app/ /app/
