import { chromium } from "@playwright/test";
import { fileURLToPath } from "url";
import { dirname, join } from "path";
import { DEFAULT_VISUAL_CONTRACT } from "../starter/scripts/visual-contract";

const __dirname = dirname(fileURLToPath(import.meta.url));

async function captureReference() {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: DEFAULT_VISUAL_CONTRACT.viewport,
  });

  const htmlPath = join(__dirname, "homepage.html");
  await page.goto(`file://${htmlPath}`);
  await page.waitForLoadState("networkidle");

  await page.screenshot({
    path: join(__dirname, "homepage.png"),
    fullPage: false,
  });
  for (const region of DEFAULT_VISUAL_CONTRACT.regions) {
    await page.screenshot({
      path: join(__dirname, `homepage-region-${region.name}.png`),
      fullPage: false,
      clip: region.clip,
    });
  }

  await browser.close();
  console.log(
    "Reference screenshot captured: homepage.png (+ region captures)",
  );
}

captureReference();
