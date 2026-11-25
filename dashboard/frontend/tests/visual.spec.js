import { test, expect } from "@playwright/test";
import { open, card, IDS, fontsLoaded } from "./helpers.js";

// Pixel comparison against screenshots taken before the design-token change
// (tests/visual.spec.js-snapshots). Moving values into CSS variables must not
// change a pixel at desktop or phone width.
// Exact match. Text renders the same every run (playwright.config.js font
// flags, fontsLoaded in helpers.js, fixed-width state dropdown in App.jsx);
// with those the old baselines still match exactly, so they were kept.
const SHOT = { fullPage: true, threshold: 0, maxDiffPixels: 0 };
for (const [name, size] of [["desktop", { width: 1280, height: 900 }], ["phone", { width: 375, height: 812 }]]) {
  test(`visual: ${name}`, async ({ page }) => {
    await page.setViewportSize(size);
    await open(page, "scope=1");
    await fontsLoaded(page);
    await expect(page).toHaveScreenshot(`${name}-default.png`, SHOT);
    await page.getByRole("button", { name: /About this data/ }).click();
    await card(page, IDS.pendingSale).getByRole("button", { name: "Details" }).click();
    await fontsLoaded(page);
    await expect(page).toHaveScreenshot(`${name}-open.png`, SHOT);
    await open(page, "scope=1&tab=financing");
    await card(page, IDS.individualUcc).getByRole("button", { name: "Details" }).click();
    await fontsLoaded(page);
    await expect(page).toHaveScreenshot(`${name}-financing.png`, SHOT);
  });
}
