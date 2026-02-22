import { chromium } from "@playwright/test";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));

async function captureReference() {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
  });

  const htmlPath = join(__dirname, "homepage.html");
  await page.goto(`file://${htmlPath}`);
  await page.waitForLoadState("networkidle");

  await page.screenshot({
    path: join(__dirname, "homepage.png"),
    fullPage: false,
  });

  await browser.close();
  console.log("Reference screenshot captured: homepage.png");
}

captureReference();
