FROM oven/bun:1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
  file \
  git \
  gzip \
  libasound2 \
  libatspi2.0-0 \
  libatk-bridge2.0-0t64 \
  libatk1.0-0 \
  libcairo2 \
  libcups2t64 \
  libdbus-1-3 \
  libexpat1 \
  libgbm1 \
  libglib2.0-0 \
  libnspr4 \
  libnss3 \
  libpango-1.0-0 \
  libx11-6 \
  libxcomposite1 \
  libxdamage1 \
  libxext6 \
  libxfixes3 \
  libxkbcommon0 \
  libxrandr2 \
  libxcb1 \
  ripgrep \
  tar \
  && rm -rf /var/lib/apt/lists/*

COPY app/package.json app/bun.lock /app/
RUN bun install --frozen-lockfile
RUN bunx playwright install chromium

COPY app/ /app/
