import { chromium } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";

const APP_URL = "http://127.0.0.1:3000";
const SERVER_START_TIMEOUT_MS = 45_000;

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForServer(url: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        return;
      }
    } catch {}
    await delay(500);
  }
  throw new Error(`Timed out waiting for app server at ${url}`);
}

function startServer(): ChildProcess {
  return spawn("bun", ["run", "dev", "--port", "3000"], {
    stdio: "ignore",
    env: { ...process.env, PORT: "3000" },
  });
}

async function stopServer(server: ChildProcess): Promise<void> {
  if (server.killed || server.exitCode !== null) {
    return;
  }
  server.kill("SIGTERM");
  await delay(1_000);
  if (server.exitCode === null) {
    server.kill("SIGKILL");
  }
}

async function captureScreenshot() {
  const server = startServer();
  try {
    await waitForServer(APP_URL, SERVER_START_TIMEOUT_MS);

    const browser = await chromium.launch();
    const page = await browser.newPage({
      viewport: { width: 1440, height: 900 },
    });

    await page.goto(APP_URL);
    await page.waitForLoadState("networkidle");

    await page.screenshot({
      path: "./actual.png",
      fullPage: false,
    });

    await browser.close();
    console.log("Screenshot captured: ./actual.png");
  } finally {
    await stopServer(server);
  }
}

captureScreenshot().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
