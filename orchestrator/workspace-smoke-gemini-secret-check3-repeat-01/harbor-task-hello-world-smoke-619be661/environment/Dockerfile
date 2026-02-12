FROM oven/bun:1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
  npm \
  && rm -rf /var/lib/apt/lists/*
RUN npm install -g @google/gemini-cli
COPY app/package.json app/bun.lock /app/
RUN bun install --frozen-lockfile
COPY app/ /app/
