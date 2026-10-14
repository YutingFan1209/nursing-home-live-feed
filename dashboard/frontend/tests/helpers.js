import { readFileSync } from "node:fs";
import { join } from "node:path";

// Eight representative records from the 2026-10-14 export (the individual
// UCC filer's name replaced): a CMS-confirmed sale, a pending news sale, an
// old state CON filing with no seller, an undisclosed buyer, a lease with no
// named parties, a control change, a news acquisition financing with no
// borrower (senior living, so hidden unless scope=1), and an individual
// UCC-1 matched to several homes.
export const fixture = JSON.parse(readFileSync(join(__dirname, "fixtures", "deals.json"), "utf8"));
export const byId = (prefix) => fixture.deals.find(d => d.id.startsWith(prefix));

export const IDS = {
  cmsSale: byId("fe24935c").id,
  pendingSale: byId("40bb281b").id,
  oldCon: byId("84695e99").id,
  undisclosedBuyer: byId("6b6a97b7").id,
  lease: byId("0a013a42").id,
  controlChange: byId("335c39ed").id,
  acqFinancing: byId("fd9f37e5").id,
  individualUcc: byId("a5ebee98").id,
};

// Serve the fixture instead of the live deals.json; load the page; wait for cards.
export async function open(page, query = "") {
  await page.route(/deals\.json/, route => route.fulfill({ json: fixture }));
  await page.goto(query ? `./?${query}` : "./");
  await page.locator('article[id^="record-"]').first().waitFor();
}

export const card = (page, id) => page.locator(`#record-${id}`);
// The four columns of a collapsed card, each { label, value }
export async function columns(cardLocator) {
  const cols = cardLocator.locator(":scope > div > div:nth-child(2) > div");
  const n = await cols.count();
  const out = [];
  for (let i = 0; i < n; i++) {
    const parts = cols.nth(i).locator(":scope > div");
    out.push({ label: (await parts.nth(0).innerText()).trim(), value: (await parts.nth(1).innerText()).trim() });
  }
  return out;
}
