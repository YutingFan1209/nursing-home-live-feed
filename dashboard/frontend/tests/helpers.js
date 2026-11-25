import { readFileSync } from "node:fs";
import { join } from "node:path";

// Eight representative records from the 2026-10-14 export (the individual
// UCC filer's name replaced): a CMS-confirmed sale, a pending news sale, an
// old state CON filing with no seller, an undisclosed buyer, a lease with no
// named parties, a control change, a news acquisition financing with no
// borrower (senior living; shown by default since the 2026-11-12 scope change
// moved scope to the export, so the old scope=1 param is a no-op), and an individual
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

// Public Sans comes from Google Fonts. document.fonts.ready can resolve before
// the font request has even started, so screenshots sometimes caught the
// fallback font (visual.spec.js failed ~3 runs in 4, 2026-11-25). Load every
// variant the site uses (index.html: 400, 600, 700, italic 400) and fail
// loudly if one didn't arrive, instead of comparing against the wrong font.
export async function fontsLoaded(page) {
  const missing = await page.evaluate(async () => {
    const want = ["400 15px", "600 15px", "700 15px", "italic 400 15px"];
    await Promise.all(want.map(w => document.fonts.load(`${w} "Public Sans"`)));
    await document.fonts.ready;
    // "loaded" isn't "painted": wait two frames so the screenshot has the font
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    const loaded = [...document.fonts].filter(f => f.family.replace(/"/g, "") === "Public Sans" && f.status === "loaded")
      .map(f => `${f.style === "italic" ? "italic " : ""}${f.weight}`);
    return ["400", "600", "700", "italic 400"].filter(w => !loaded.includes(w));
  });
  if (missing.length) throw new Error(`Public Sans didn't load (${missing.join(", ")}): no network to fonts.googleapis.com?`);
}
