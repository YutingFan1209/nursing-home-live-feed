import { test, expect } from "@playwright/test";
import { open, card, IDS } from "./helpers.js";

// Pixel comparison against screenshots taken before the design-token change
// (tests/visual.spec.js-snapshots). Moving values into CSS variables must not
// change a pixel at desktop or phone width.
for (const [name, size] of [["desktop", { width: 1280, height: 900 }], ["phone", { width: 375, height: 812 }]]) {
  test(`visual: ${name}`, async ({ page }) => {
    await page.setViewportSize(size);
    await open(page, "scope=1");
    await page.evaluate(() => document.fonts.ready);
    await expect(page).toHaveScreenshot(`${name}-default.png`, { fullPage: true, threshold: 0, maxDiffPixels: 0 });
    await page.getByRole("button", { name: /About this data/ }).click();
    await card(page, IDS.pendingSale).getByRole("button", { name: "Details" }).click();
    await expect(page).toHaveScreenshot(`${name}-open.png`, { fullPage: true, threshold: 0, maxDiffPixels: 0 });
    await open(page, "scope=1&tab=financing");
    await card(page, IDS.individualUcc).getByRole("button", { name: "Details" }).click();
    await expect(page).toHaveScreenshot(`${name}-financing.png`, { fullPage: true, threshold: 0, maxDiffPixels: 0 });
  });
}
