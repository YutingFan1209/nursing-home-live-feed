import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { open, card, IDS } from "./helpers.js";

// (i) axe scans, all of axe's default rules (WCAG 2.x A/AA plus best practices).
// Each scan's full result is attached to the test report.
async function scan(page, testInfo, name) {
  const results = await new AxeBuilder({ page }).analyze();
  await testInfo.attach(`axe-${name}.json`, { body: JSON.stringify(results.violations, null, 2), contentType: "application/json" });
  const summary = results.violations.map(v => `${v.impact} ${v.id} (${v.nodes.length}): ${v.help}`);
  expect(summary, `${name}\n${summary.join("\n")}`).toEqual([]);
}

test("axe: default view", async ({ page }, testInfo) => {
  await open(page);
  await scan(page, testInfo, "default");
});

test("axe: Details panel open", async ({ page }, testInfo) => {
  await open(page);
  await card(page, IDS.cmsSale).getByRole("button", { name: "Details" }).click();
  await page.locator(`#details-${IDS.cmsSale}`).waitFor();
  await scan(page, testInfo, "details");
});

test("axe: About menu open", async ({ page }, testInfo) => {
  await open(page);
  await page.getByRole("button", { name: /About this data/ }).click();
  await page.locator(`[id="${await page.getByRole("button", { name: /About this data/ }).getAttribute("aria-controls")}"]`).waitFor();
  await scan(page, testInfo, "about-menu");
});
