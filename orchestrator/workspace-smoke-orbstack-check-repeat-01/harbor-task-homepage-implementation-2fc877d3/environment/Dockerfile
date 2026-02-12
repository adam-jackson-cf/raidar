FROM oven/bun:1
WORKDIR /app
COPY app/package.json app/bun.lock /app/
RUN bun install --frozen-lockfile
COPY app/ /app/
RUN apt-get update && apt-get install -y --no-install-recommends \
  libx11-6 \
  libxext6 \
  libxcb1 \
  libglib2.0-0 \
  libnspr4 \
  libnss3 \
  libdbus-1-3 \
  libatk1.0-0 \
  libexpat1 \
  libatspi2.0-0 \
  libxcomposite1 \
  libxdamage1 \
  libxfixes3 \
  libxrandr2 \
  libgbm1 \
  libxkbcommon0 \
  libasound2 \
  && rm -rf /var/lib/apt/lists/*
RUN bunx playwright install chromium
