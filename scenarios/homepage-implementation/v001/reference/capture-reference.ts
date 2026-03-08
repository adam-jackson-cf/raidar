import { chromium } from "@playwright/test";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
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

async function captureReference() {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: VIEWPORT });

  const htmlPath = join(__dirname, "homepage.html");
  await page.goto(`file://${htmlPath}`);
  await page.waitForLoadState("networkidle");

  await page.screenshot({
    path: join(__dirname, "homepage.png"),
    fullPage: false,
  });
  for (const region of REGION_CLIPS) {
    await page.screenshot({
      path: join(__dirname, `homepage-region-${region.name}.png`),
      fullPage: false,
      clip: region.clip,
    });
  }

  await browser.close();
  console.log("Reference screenshot captured: homepage.png (+ region captures)");
}

captureReference();
