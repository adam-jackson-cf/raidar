import { chromium } from "@playwright/test";

async function captureScreenshot() {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
  });

  await page.goto("http://localhost:3000");
  await page.waitForLoadState("networkidle");

  await page.screenshot({
    path: "./actual.png",
    fullPage: false,
  });

  await browser.close();
  console.log("Screenshot captured: ./actual.png");
}

captureScreenshot();
