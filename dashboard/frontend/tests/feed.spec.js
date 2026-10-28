import { test, expect } from "@playwright/test";
import { open, card, columns, IDS, fixture } from "./helpers.js";

// Column roles, in order: who, counterparty, where, when
const ROLE_LABELS = [
  /^(Buyer|Lender|Operator)$/,
  /^(Seller|Borrower|Landlord or prior operator|Prior operator|Filed against \(individual\))$/,
  /^(Facility|Facilities|Home|Possible homes( \(\d+\))?)$/,
  // date roles from src/derive.js primaryDate
  /^(Effective|Expected close|Expected close \(in filing\)|Filed|Reported|Decision posted|Council vote|Deal date in source)$/,
];

test.describe("cards", () => {
  test("(a) every card shows four labeled columns in the same order", async ({ page }) => {
    for (const tab of ["", "financing"]) {
      await open(page, `scope=1${tab ? `&tab=${tab}` : ""}`);
      const cards = page.locator('article[id^="record-"]');
      const n = await cards.count();
      expect(n).toBeGreaterThan(0);
      for (let i = 0; i < n; i++) {
        const cols = await columns(cards.nth(i));
        const id = await cards.nth(i).getAttribute("id");
        expect(cols.map(c => c.label), id).toHaveLength(4);
        cols.forEach((c, j) => {
          expect(c.label, `${id} column ${j + 1}`).toMatch(ROLE_LABELS[j]);
          expect(c.value, `${id} column ${j + 1} value`).not.toBe("");
        });
      }
    }
  });

  test("(b) missing values say why, never blank", async ({ page }) => {
    await open(page, "scope=1");
    const cols = async (id) => columns(card(page, id));
    expect((await cols(IDS.undisclosedBuyer))[0]).toEqual({ label: "Buyer", value: "Undisclosed" });
    expect((await cols(IDS.lease))[0]).toEqual({ label: "Operator", value: "Not named in source" });
    expect((await cols(IDS.lease))[1].value).toBe("Not reported");
    expect((await cols(IDS.oldCon))[1]).toEqual({ label: "Seller", value: "Not reported" });
    await open(page, "scope=1&tab=financing");
    expect((await cols(IDS.acqFinancing))[1]).toEqual({ label: "Borrower", value: "Not reported" });
  });

  test("(c) a pending sale never says Confirmed, collapsed or expanded", async ({ page }) => {
    await open(page);
    for (const id of [IDS.pendingSale, IDS.undisclosedBuyer]) {
      const c = card(page, id);
      await expect(c).toContainText("Expected");
      await c.getByRole("button", { name: "Details" }).click();
      await expect(page.locator(`#details-${id}`)).toBeVisible();
      expect(await c.innerText()).not.toMatch(/confirmed/i);
    }
  });

  test("(d) UCC filings show On file", async ({ page }) => {
    await open(page, "tab=financing");
    const c = card(page, IDS.individualUcc);
    await expect(c).toContainText("On file");
    expect(await c.innerText()).not.toMatch(/\bConfirmed\b/);
  });
});

test("(e) tabs switch and their counts match the cards shown", async ({ page }) => {
  await open(page);
  const tabs = page.getByRole("tab");
  await expect(tabs).toHaveCount(2);
  for (let i = 0; i < 2; i++) {
    await tabs.nth(i).click();
    await expect(tabs.nth(i)).toHaveAttribute("aria-selected", "true");
    await expect(tabs.nth(1 - i)).toHaveAttribute("aria-selected", "false");
    const count = Number((await tabs.nth(i).locator("span").innerText()).replace(/,/g, ""));
    await expect(page.locator('article[id^="record-"]')).toHaveCount(count);
  }
  // fixture: 6 skilled-nursing ownership-type records, 1 skilled-nursing financing
  await expect(tabs.nth(0)).toContainText("6");
  await expect(tabs.nth(1)).toContainText("1");
});

test("(e2) arrow keys move between tabs and the panel follows", async ({ page }) => {
  await open(page);
  const tabs = page.getByRole("tab");
  await tabs.nth(0).focus();
  await page.keyboard.press("ArrowRight");
  await expect(tabs.nth(1)).toBeFocused();
  await expect(tabs.nth(1)).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", await tabs.nth(1).getAttribute("id"));
  await expect(card(page, IDS.individualUcc)).toBeVisible();
});

test.describe("(f) keyboard", () => {
  // Tab from the top of the page until the button has focus, then Enter, Escape
  async function tabTo(page, name) {
    const target = page.getByRole("button", { name });
    for (let i = 0; i < 40; i++) {
      await page.keyboard.press("Tab");
      if (await target.evaluate(el => el === document.activeElement)) return target;
    }
    throw new Error(`Tab never reached ${name}`);
  }
  for (const [name, re] of [["More filters", /More filters/], ["About this data", /About this data/]]) {
    test(`${name} opens with Enter and closes with Escape`, async ({ page }) => {
      await open(page);
      const btn = await tabTo(page, re);
      await expect(btn).toHaveAttribute("aria-expanded", "false");
      await page.keyboard.press("Enter");
      await expect(btn).toHaveAttribute("aria-expanded", "true");
      await expect(page.locator(`[id="${await btn.getAttribute("aria-controls")}"]`)).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(btn).toHaveAttribute("aria-expanded", "false");
      await expect(btn).toBeFocused();
    });
  }
});

test("(g) Details has four sections and a working permalink", async ({ page }) => {
  await open(page);
  const id = IDS.pendingSale;
  await card(page, id).getByRole("button", { name: "Details" }).click();
  const details = page.locator(`#details-${id}`);
  await expect(details.locator("h3")).toHaveText(["Transaction overview", "Verification", "Narrative context", "Source documentation"]);
  const link = details.getByRole("link", { name: "Permalink to this record" });
  const href = await link.getAttribute("href");
  expect(href).toMatch(new RegExp(`\\?id=${id}$`));
  await page.goto(href);
  await expect(page.getByRole("region", { name: "Single record" }).locator(`#record-${id}`)).toBeVisible();
});

test("(h) no horizontal scroll at 375px", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const noScroll = async (what) => {
    const [sw, cw] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
    expect(sw, `${what}: scrollWidth ${sw} > clientWidth ${cw}`).toBeLessThanOrEqual(cw);
  };
  await open(page, "scope=1");
  await noScroll("default view");
  await page.getByRole("button", { name: /About this data/ }).click();
  await noScroll("About menu open");
  await card(page, IDS.cmsSale).getByRole("button", { name: "Details" }).click();
  await noScroll("Details open");
  await open(page, "scope=1&tab=financing");
  for (const d of fixture.deals.filter(x => x.id === IDS.individualUcc || x.id === IDS.acqFinancing))
    await card(page, d.id).getByRole("button", { name: "Details" }).click();
  await noScroll("financing tab, Details open");
});

test("(i) a UCC link that only reaches a search page copies the filing number", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await context.route(/bizfileonline\.sos\.ca\.gov/, route => route.fulfill({ body: "portal" }));
  const ca = { ...fixture.deals.find(d => d.id === IDS.individualUcc), ucc_state: "CA",
    ucc_filing_number: "U260079323028", ucc_search_page: true,
    source_url: "https://bizfileonline.sos.ca.gov/search/ucc" };
  await page.route(/deals\.json/, route => route.fulfill({
    json: { ...fixture, deals: fixture.deals.map(d => d.id === ca.id ? ca : d) } }));
  await page.goto("./?tab=financing");
  await card(page, ca.id).getByRole("button", { name: "Details" }).click();
  const details = page.locator(`#details-${ca.id}`);
  const popup = page.waitForEvent("popup");
  await details.getByRole("link", { name: /CA UCC-1 filing #U260079323028/ }).click();
  await popup;
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("U260079323028");
  await expect(details.getByRole("status")).toContainText("paste it into the portal's search box");
});

test("(j) the CSV download has a UTF-8 BOM and the Excel download is a workbook", async ({ page }) => {
  await open(page);
  for (const [name, start] of [["↓ CSV", [0xEF, 0xBB, 0xBF]], ["↓ Excel", [0x50, 0x4B, 0x03, 0x04]]]) {
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name }).click();
    const file = await download;
    const bytes = await new Promise(async resolve => {
      const chunks = [];
      (await file.createReadStream()).on("data", c => chunks.push(c)).on("end", () => resolve(Buffer.concat(chunks)));
    });
    expect([...bytes.subarray(0, start.length)]).toEqual(start);
    expect(file.suggestedFilename()).toMatch(name === "↓ CSV" ? /\.csv$/ : /\.xlsx$/);
  }
});

test("(k) a UCC filing's collateral shows in Details, quoted", async ({ page }) => {
  const ri = { ...fixture.deals.find(d => d.id === IDS.individualUcc), ucc_state: "RI",
    ucc_collateral: "All assets of Debtor, whether now owned or hereafter acquired, and all proceeds thereof." };
  await page.route(/deals\.json/, route => route.fulfill({
    json: { ...fixture, deals: fixture.deals.map(d => d.id === ri.id ? ri : d) } }));
  await page.goto("./?tab=financing");
  await card(page, ri.id).getByRole("button", { name: "Details" }).click();
  await expect(page.locator(`#details-${ri.id}`)).toContainText(
    "Collateral (as filed)All assets of Debtor, whether now owned or hereafter acquired, and all proceeds thereof. · quoted from the state's filing page");
});
