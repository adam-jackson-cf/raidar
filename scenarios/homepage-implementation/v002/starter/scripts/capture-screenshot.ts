import { chromium } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, mkdtempSync, openSync, readFileSync, closeSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadRuntimeVisualContract } from "./visual-contract";

const SERVER_START_TIMEOUT_MS = 45_000;
const VISUAL_CONTRACT = loadRuntimeVisualContract(process.cwd());

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function readLogTail(logPath: string, maxLines = 80): string {
  if (!existsSync(logPath)) {
    return "Server log unavailable.";
  }
  const content = readFileSync(logPath, "utf8").trim();
  if (!content) {
    return "Server log empty.";
  }
  return content.split(/\r?\n/).slice(-maxLines).join("\n");
}

async function allocatePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close(() => reject(new Error("Failed to allocate a TCP port.")));
        return;
      }
      const { port } = address;
      server.close((error) => {
        if (error) {
          reject(error);
          return;
        }
        resolve(port);
      });
    });
  });
}

async function waitForServer(
  url: string,
  timeoutMs: number,
  server: ChildProcess,
  logPath: string,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (server.exitCode !== null) {
      throw new Error(
        `App server exited early with code ${server.exitCode}.\n${readLogTail(logPath)}`,
      );
    }
    try {
      const response = await fetch(url);
      if (response.ok) {
        return;
      }
    } catch {}
    await delay(500);
  }
  throw new Error(`Timed out waiting for app server at ${url}.\n${readLogTail(logPath)}`);
}

function startServer(port: number): { server: ChildProcess; logPath: string } {
  const logDir = mkdtempSync(join(tmpdir(), "raidar-homepage-capture-"));
  const logPath = join(logDir, "next-dev.log");
  const logFd = openSync(logPath, "w");
  const server = spawn("bun", ["run", "dev", "--port", String(port)], {
    stdio: ["ignore", logFd, logFd],
    env: { ...process.env, PORT: String(port) },
  });
  server.once("close", () => {
    closeSync(logFd);
  });
  return { server, logPath };
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
  const port = await allocatePort();
  const appUrl = `http://127.0.0.1:${port}`;
  const { server, logPath } = startServer(port);
  try {
    await waitForServer(appUrl, SERVER_START_TIMEOUT_MS, server, logPath);

    const browser = await chromium.launch();
    const page = await browser.newPage({ viewport: VISUAL_CONTRACT.viewport });

    await page.goto(appUrl);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(250);

    await page.screenshot({
      path: "./actual.png",
      fullPage: false,
    });
    for (const region of VISUAL_CONTRACT.regions) {
      await page.screenshot({
        path: `./actual-region-${region.name}.png`,
        fullPage: false,
        clip: region.clip,
      });
    }

    await browser.close();
    console.log("Screenshot captured: ./actual.png (+ region captures)");
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`${message}\nCapture log: ${logPath}`);
  } finally {
    await stopServer(server);
  }
}

captureScreenshot().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
