import { chromium } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";

const APP_URL = "http://127.0.0.1:3000";
const SERVER_START_TIMEOUT_MS = 45_000;
const VIEWPORT = { width: 1440, height: 900 };
const REGION_CLIPS = [
  {
    name: "hero",
    clip: { x: 0, y: 0, width: 1440, height: 320 },
  },
  {
    name: "features",
    clip: { x: 0, y: 320, width: 1440, height: 420 },
  },
  {
    name: "footer",
    clip: { x: 0, y: 740, width: 1440, height: 160 },
  },
] as const;

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
    const page = await browser.newPage({ viewport: VIEWPORT });

    await page.goto(APP_URL);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(250);

    await page.screenshot({
      path: "./actual.png",
      fullPage: false,
    });
    for (const region of REGION_CLIPS) {
      await page.screenshot({
        path: `./actual-region-${region.name}.png`,
        fullPage: false,
        clip: region.clip,
      });
    }

    await browser.close();
    console.log("Screenshot captured: ./actual.png (+ region captures)");
  } finally {
    await stopServer(server);
  }
}

captureScreenshot().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
