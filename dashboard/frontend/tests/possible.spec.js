import { test, expect } from "@playwright/test";
import { fixture, card, IDS } from "./helpers.js";

// A likely buyer the source doesn't name (pipeline/deal_labels.POSSIBLE_PARTIES,
// person's design 2026-11-25): the face keeps the missing-name text with a
// grey "possibly ..." line; Details has a tagged Inferred row with evidence.
test("possible buyer: hint on the face, Inferred row in Details", async ({ page }) => {
  const data = structuredClone(fixture);
  const d = data.deals.find(x => x.id === IDS.undisclosedBuyer);
  d.possible_buyer = { name: "Example Holdings LLC", evidence: "A state licence list shows it on a new licence.",
                       source_label: "State licence list", source_url: "https://example.org/list" };
  await page.route(/deals\.json/, route => route.fulfill({ json: data }));
  await page.goto("./");
  const c = card(page, IDS.undisclosedBuyer);
  await expect(c.getByText("Undisclosed", { exact: true })).toBeVisible();
  await expect(c.getByText("possibly Example Holdings LLC")).toBeVisible();
  await c.getByRole("button", { name: "Details" }).click();
  const row = c.getByText("Possible buyer", { exact: true }).locator("..");
  await expect(row).toContainText("Inferred");
  await expect(row).toContainText("Example Holdings LLC");
  await expect(row).toContainText("Not confirmed.");
  await expect(row.getByRole("link", { name: /State licence list/ })).toHaveAttribute("href", "https://example.org/list");
  // other cards unchanged
  await expect(page.getByText(/^possibly /)).toHaveCount(1);
});
