FROM oven/bun:1
WORKDIR /app
COPY app/package.json app/bun.lock /app/
RUN bun install --frozen-lockfile
COPY app/ /app/
